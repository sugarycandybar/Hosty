"""
FilesView -- folders, worlds, backups, and Modrinth integration (per selected server).
"""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk, Pango

from ..utils import *

# Bound on concurrent modrinth icon fetches: rows used to spawn two
# downloads each with no limit, stalling the main loop on page loads.
_ICON_FETCH_SEMAPHORE = threading.Semaphore(6)

# Cap for decoded icon textures kept in memory (48px each, tiny).
_MODRINTH_ICON_CACHE_MAX = 400

# Width below which result rows switch to their stacked layout.
_MODRINTH_ROW_COMPACT_MIN_WIDTH = 550

# Bound on concurrent compatible-version lookups (one per result row):
# without it, fast scrolling stacks dozens of API bursts and trips
# server-side rate limits, which then surface as failed pages.
_VERSION_FETCH_SEMAPHORE = threading.Semaphore(8)

# Cap on total loaded results: deep result sets have diminishing relevance
# and unbounded lists cost memory, layout time, and API quota every scroll.
_MODRINTH_MAX_RESULTS = 200


def _modrinth_row_mode_for_width(width: int) -> str:
    """Return the result-row layout for a list width ("compact"/"expanded")."""
    try:
        width = int(width)
    except Exception:
        return "compact"
    if width <= 0 or width >= _MODRINTH_ROW_COMPACT_MIN_WIDTH:
        return "compact"
    return "expanded"


def _stop_before_install_message(is_plugin: bool) -> str:
    """Body for the "server is running" alert while installing content."""
    if is_plugin:
        return _("Stop the server before installing plugins.")
    return _("Stop the server before installing mods.")


def _select_before_install_message(is_plugin: bool) -> str:
    """Body for the "no server selected" alert while installing content."""
    if is_plugin:
        return _("Select a server before installing plugins.")
    return _("Select a server before installing mods.")


class ModrinthMixin:
    def _server_loader(self) -> str:
        """Return the Modrinth loader id for *mod* queries (defaults to fabric).

        Arclight mods target the underlying base platform, so the platform
        is returned instead of ``arclight`` (which Modrinth does not know).
        """
        from hosty.shared.utils.constants import LOADER_FABRIC, effective_mod_loader

        if self._server_info is None:
            return LOADER_FABRIC
        return effective_mod_loader(
            getattr(self._server_info, "loader_type", None),
            getattr(self._server_info, "arclight_platform", ""),
        )

    @staticmethod
    def _update_direction_label(installed_version_id: str, chosen_version_id: str, ordered_version_ids) -> str:
        """Return Update/Downgrade for a tracked version that differs.

        ``ordered_version_ids`` is newest-first: a chosen version earlier in
        the list is newer. Unknown direction defaults to Update (the common
        stale-install case).
        """
        installed = str(installed_version_id or "").strip()
        chosen = str(chosen_version_id or "").strip()
        if installed and chosen and installed != chosen and ordered_version_ids:
            try:
                ordered = list(ordered_version_ids)
                if ordered.index(chosen) < ordered.index(installed):
                    return _("Update")
                return _("Downgrade")
            except ValueError:
                pass
        return _("Update")

    def _decide_row_install_button(
        self,
        hit,
        best,
        installed_names,
        *,
        is_modpack: bool,
        is_datapack: bool,
        is_plugin: bool,
        btn_label: str,
        installed_packs: set[str] | None = None,
        installed_dps: set[str] | None = None,
        installed_plugins: set[str] | None = None,
    ) -> tuple:
        """Single source of truth for search-row install buttons.

        Installed means present on disk in any recognized form (current
        file, tracked older file, or slug-matching file) — never a version
        judgment; Update/Downgrade precision lives on the detail page.
        Returns (label, sensitive).
        """
        pid = str(getattr(hit, "project_id", "") or "").strip()
        names = {str(n).lower() for n in (installed_names or set())}

        def _installed(kind: str) -> bool:
            if kind == "modpack":
                return pid in installed_packs if installed_packs is not None else self._is_modpack_installed(pid)
            if kind == "datapack":
                return pid in installed_dps if installed_dps is not None else self._is_datapack_installed(pid)
            return pid in installed_plugins if installed_plugins is not None else self._is_plugin_installed(pid)

        if is_modpack and _installed("modpack"):
            return (_("Installed"), False)
        if is_datapack and _installed("datapack"):
            return (_("Installed"), False)
        if is_plugin and _installed("plugin"):
            return (_("Installed"), False)
        if best is None:
            return (btn_label, False)
        best_file = str(getattr(best, "filename", "") or "")
        if (not is_modpack) and (not is_datapack) and best_file and best_file.lower() in names:
            if self._dependency_dependents(best_file):
                return (_("Dependency"), False)
            return (_("Installed"), False)
        if (
            (not is_modpack)
            and (not is_datapack)
            and (not is_plugin)
            and (self._is_mod_present(pid, names) or self._looks_installed(hit, names))
        ):
            return (_("Installed"), False)
        return (btn_label, True)

    @staticmethod
    def _project_type_items(loader_type: str | None) -> list[tuple[str, str]]:
        """Return the (label, project type) filter entries this server can install.

        Paper installs Bukkit plugins instead of mods (and therefore has no
        modpacks); Arclight is a hybrid that runs both.
        """
        from hosty.shared.utils.constants import supports_mods, supports_plugins

        items: list[tuple[str, str]] = []
        if supports_mods(loader_type):
            items.append((_("Mods"), "mod"))
        if supports_plugins(loader_type):
            items.append((_("Plugins"), "plugin"))
        if supports_mods(loader_type):
            items.append((_("Modpacks"), "modpack"))
        items.append((_("Datapacks"), "datapack"))
        return items

    @staticmethod
    def _is_plugin_hit(hit) -> bool:
        return str(getattr(hit, "project_type", "") or "").strip().lower() == "plugin"

    def _content_dir(self, root) -> Path | None:
        """Return the loader-appropriate jar directory (mods/, or plugins/ for Paper)."""
        from hosty.shared.utils.constants import content_dir_name

        if not root:
            return None
        return Path(root) / content_dir_name(self._server_loader())

    def _push_modrinth_page(self, *_args) -> None:
        search_page = Adw.NavigationPage(
            title=_("Modrinth"),
            child=self._build_modrinth_search_view(),
        )
        try:
            search_page.set_tag("modrinth-search")
        except Exception:
            pass

        # Single NavigationView for search + detail pages: a nested nav
        # swallows the swipe-back gesture on its root page, so touch
        # swipe could never leave the Modrinth search page.
        if self._push_fullscreen_page_cb:
            if self._modrinth_header:
                self._modrinth_header.set_show_end_title_buttons(True)
            # The callback presents the page itself; just reuse its nav.
            host_nav = self._push_fullscreen_page_cb(search_page) or self._nav
            presented = True
        else:
            host_nav = self._nav
            presented = False
        self._modrinth_nav = host_nav
        if getattr(self, "_modrinth_popped_nav", None) is not host_nav:
            host_nav.connect("popped", self._on_modrinth_page_popped)
            self._modrinth_popped_nav = host_nav
        if not presented:
            host_nav.push(search_page)
        self._modrinth_page = search_page

    def _on_modrinth_page_popped(self, _nav, page) -> None:
        try:
            tag = page.get_tag()
        except Exception:
            tag = None
        if tag == "modrinth-detail":
            self._refresh_modrinth_rows_install_state()

    def _build_modrinth_search_view(self) -> Gtk.Widget:
        from hosty.shared.backend import modrinth_client

        tv = Adw.ToolbarView()
        tv.set_hexpand(True)
        header = Adw.HeaderBar()
        header.add_css_class("modrinth-header")
        self._modrinth_header = header
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(False)

        search_outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        search_outer.add_css_class("modrinth-search-box")
        search_outer.set_hexpand(True)
        search_outer.set_valign(Gtk.Align.CENTER)

        entry = Gtk.SearchEntry()
        entry.set_hexpand(True)
        entry.set_placeholder_text(_("Search mods…"))
        entry.add_css_class("modrinth-search-entry")
        search_outer.append(entry)

        filter_btn = Gtk.MenuButton()
        filter_btn.set_icon_name("sliders-horizontal-symbolic")
        filter_btn.add_css_class("flat")
        filter_btn.add_css_class("modrinth-filter-btn")
        filter_btn.set_tooltip_text(_("Filters"))
        search_outer.append(filter_btn)

        header.set_title_widget(search_outer)
        tv.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_hexpand(True)

        project_type_items = self._project_type_items(
            getattr(self._server_info, "loader_type", None) if self._server_info else None
        )
        category_items = [
            (_("Any category"), ""),
            (_("Optimization"), "optimization"),
            (_("Utility"), "utility"),
            (_("Technology"), "technology"),
            (_("Adventure"), "adventure"),
            (_("Decoration"), "decoration"),
            (_("Magic"), "magic"),
            (_("Storage"), "storage"),
            (_("Worldgen"), "worldgen"),
            (_("Library"), "library"),
        ]
        sort_items = [
            (_("Relevance"), "relevance"),
            (_("Downloads"), "downloads"),
            (_("Follows"), "follows"),
            (_("Newest"), "newest"),
            (_("Recently updated"), "updated"),
        ]

        selected_type_idx = [0]
        selected_cat_idx = [0]
        selected_sort_idx = [1]

        def make_filter_buttons(items, default_idx):
            buttons = []
            group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=False)
            for i, (label, _value) in enumerate(items):
                btn = Gtk.ToggleButton(label=label)
                btn.add_css_class("modrinth-filter-option")
                btn.set_active(i == default_idx)
                group.append(btn)
                buttons.append(btn)
            return group, buttons

        def make_filter_flowbox(items, default_idx, max_cols):
            buttons = []
            group = Gtk.FlowBox()
            group.set_max_children_per_line(max_cols)
            group.set_selection_mode(Gtk.SelectionMode.NONE)
            group.set_column_spacing(4)
            group.set_row_spacing(4)
            for i, (label, _value) in enumerate(items):
                btn = Gtk.ToggleButton(label=label)
                btn.add_css_class("modrinth-filter-option")
                btn.set_active(i == default_idx)
                group.append(btn)
                buttons.append(btn)
            return group, buttons

        type_box, type_buttons = make_filter_buttons(project_type_items, 0)
        cat_box, cat_buttons = make_filter_flowbox(category_items, 0, 4)
        sort_box, sort_buttons = make_filter_flowbox(sort_items, 1, 3)

        # Set up Popover for filters
        filter_popover = Gtk.Popover()
        filter_btn.set_popover(filter_popover)

        popover_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        popover_content.set_margin_start(12)
        popover_content.set_margin_end(12)
        popover_content.set_margin_top(12)
        popover_content.set_margin_bottom(12)

        type_label = Gtk.Label(label=_("Type"), xalign=0.0)
        type_label.add_css_class("modrinth-filter-label")
        popover_content.append(type_label)
        popover_content.append(type_box)

        cat_label = Gtk.Label(label=_("Category"), xalign=0.0)
        cat_label.add_css_class("modrinth-filter-label")
        popover_content.append(cat_label)
        popover_content.append(cat_box)

        sort_label = Gtk.Label(label=_("Sort by"), xalign=0.0)
        sort_label.add_css_class("modrinth-filter-label")
        popover_content.append(sort_label)
        popover_content.append(sort_box)

        filter_popover.set_child(popover_content)

        results = Gtk.ListBox()
        self._modrinth_results_list = results
        results.set_selection_mode(Gtk.SelectionMode.NONE)
        results.add_css_class("mod-results-list")
        results.set_vexpand(True)
        results.set_activate_on_single_click(True)
        results.connect("row-activated", self._on_modrinth_row_activated)
        results.set_margin_start(12)
        results.set_margin_end(12)
        results.set_margin_top(2)

        page_size = 20
        # Start loading the next page this far before the bottom so results
        # are ready before the user gets there.
        prefetch_px = 1000
        state = {
            "offset": 0,
            "total": 0,
            "busy": False,
            "all_loaded": False,
            "more_spinner_row": None,
            "generation": 0,
            "errors": 0,
            "empty_streak": 0,
            "error_row": None,
        }

        def selected_category() -> str:
            idx = selected_cat_idx[0]
            if idx < 0 or idx >= len(category_items):
                return ""
            return category_items[idx][1]

        def selected_project_type() -> str:
            idx = selected_type_idx[0]
            if idx < 0 or idx >= len(project_type_items):
                return "mod"
            return project_type_items[idx][1]

        def selected_sort() -> str:
            idx = selected_sort_idx[0]
            if idx < 0 or idx >= len(sort_items):
                return "downloads"
            return sort_items[idx][1]

        def set_busy(busy: bool):
            # Only tracks in-flight state (used to gate pagination/prefetch).
            # The filter controls stay enabled while a search loads, and the
            # loading indicator lives in the results list (see
            # show_more_spinner) rather than in the search pill: a spinner
            # there wedged the entry's clear button away from the filter
            # button, so the two trailing controls looked separated.
            state["busy"] = busy

        def update_search_hint() -> None:
            ptype = selected_project_type()
            if ptype == "modpack":
                entry.set_placeholder_text(_("Search modpacks…"))
            elif ptype == "datapack":
                entry.set_placeholder_text(_("Search datapacks…"))
            elif ptype == "plugin":
                entry.set_placeholder_text(_("Search plugins…"))
            else:
                entry.set_placeholder_text(_("Search mods…"))

        def clear_results():
            while True:
                r = results.get_row_at_index(0)
                if r is None:
                    break
                results.remove(r)

        def installed_mod_names() -> set[str]:
            mods_dir = self._content_dir(self._server_dir())
            if not mods_dir or not Path(mods_dir).is_dir():
                return set()
            return {p.name.lower() for p in mods_dir.glob("*.jar")}

        def installed_plugin_names() -> set[str]:
            plugins_dir = self._plugins_dir()
            if not plugins_dir or not plugins_dir.is_dir():
                return set()
            return {p.name.lower() for p in plugins_dir.glob("*.jar")}

        def hide_more_spinner():
            row = state.get("more_spinner_row")
            state["more_spinner_row"] = None
            if row is not None:
                try:
                    results.remove(row)
                except Exception:
                    pass

        def show_more_spinner():
            hide_more_spinner()
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_halign(Gtk.Align.CENTER)
            box.set_margin_top(12)
            box.set_margin_bottom(12)
            spinner = Gtk.Spinner()
            spinner.start()
            box.append(spinner)
            row.set_child(box)
            results.append(row)
            state["more_spinner_row"] = row

        def hide_load_error():
            row = state.get("error_row")
            state["error_row"] = None
            if row is not None:
                try:
                    results.remove(row)
                except Exception:
                    pass

        def show_load_error(reason: str | None = None):
            subtitle = _("Tap to retry")
            if reason:
                short = str(reason).strip()
                if len(short) > 80:
                    short = short[:77] + "…"
                if short:
                    subtitle = f"{short} · {_('Tap to retry')}"
            existing = state.get("error_row")
            if existing is not None:
                try:
                    existing.set_subtitle(subtitle)
                except Exception:
                    pass
                return
            row = Adw.ActionRow(
                title=_("Couldn't load more results"),
                subtitle=subtitle,
            )
            row.set_activatable(True)

            def on_retry(*_args):
                hide_load_error()
                state["errors"] = 0
                do_search_more()

            row.connect("activated", on_retry)
            results.append(row)
            state["error_row"] = row

        def restore_scroll(saved_value: float) -> None:
            # Appending must never move the viewport: keep the exact position
            # (clamped), so list growth below never yanks what you are reading.
            adj = sw.get_vadjustment()
            upper = adj.get_upper()
            page = adj.get_page_size()
            adj.set_value(max(0.0, min(saved_value, max(0.0, upper - page))))

        def append_result_row(row) -> None:
            # Insert before the loading footer (if present) so the list only
            # ever grows downward; removing-then-readding the footer would
            # shrink first and visibly jump.
            spinner = state.get("more_spinner_row")
            if spinner is not None:
                try:
                    index = spinner.get_index()
                    if index >= 0:
                        results.insert(row, index)
                        return
                except Exception:
                    pass
            results.append(row)

        def finish_search(hits, total, err, version, qtxt, appending: bool, generation: int):
            # Drop late results from a superseded search (e.g. typed query A
            # finishing after query B already reset the list).
            if generation != state["generation"]:
                return
            set_busy(False)
            adj = sw.get_vadjustment()
            saved_value = adj.get_value()
            if err:
                hide_more_spinner()
                # Roll back so a retry fetches this same window instead of
                # skipping it; consecutive failures surface a retry row.
                state["offset"] = max(0, state["offset"] - page_size)
                state["errors"] = int(state.get("errors", 0)) + 1
                if not appending:
                    results.append(self._empty_listbox_row(_("Could not fetch Modrinth results.")))
                elif state["errors"] >= 3:
                    show_load_error(err if isinstance(err, str) else None)
                restore_scroll(saved_value)
                return
            state["total"] = int(total)
            if not appending:
                hide_more_spinner()
                clear_results()
            if not hits:
                if not appending:
                    results.append(self._empty_listbox_row(_("No results")))
                    state["all_loaded"] = True
                    hide_more_spinner()
                    restore_scroll(saved_value)
                    return
                # Appending an empty window: halt only if the server side is
                # exhausted; otherwise skip ahead (bounded) so one empty
                # window can't end pagination while more results exist.
                hide_more_spinner()
                if total <= state["offset"]:
                    state["all_loaded"] = True
                    restore_scroll(saved_value)
                    return
                state["empty_streak"] = int(state.get("empty_streak", 0)) + 1
                if state["empty_streak"] > 5:
                    state["all_loaded"] = True
                    restore_scroll(saved_value)
                    return
                restore_scroll(saved_value)
                maybe_prefetch()
                return

            state["empty_streak"] = 0
            state["errors"] = 0
            hide_load_error()

            if total <= state["offset"] + len(hits):
                state["all_loaded"] = True

            installed = installed_mod_names()
            installed_plugins = installed_plugin_names()
            # Snapshot install state once per page: per-row file reads here
            # would stall the main thread while appending results.
            installed_packs = set(self._modpack_entries())
            installed_dps = set(self._read_datapack_state().get("datapacks", {}))
            installed_plugin_ids = set(self._read_plugin_state().get("plugins", {}))
            for h in hits:
                if self._is_plugin_hit(h):
                    append_result_row(
                        self._make_modrinth_row(
                            h,
                            version,
                            installed_plugins,
                            installed_packs=installed_packs,
                            installed_dps=installed_dps,
                            installed_plugins=installed_plugin_ids,
                        )
                    )
                else:
                    append_result_row(
                        self._make_modrinth_row(
                            h,
                            version,
                            installed,
                            installed_packs=installed_packs,
                            installed_dps=installed_dps,
                            installed_plugins=installed_plugin_ids,
                        )
                    )
            state["loaded"] = int(state.get("loaded", 0)) + len(hits)
            if state["loaded"] >= _MODRINTH_MAX_RESULTS and not state.get("end_shown"):
                state["end_shown"] = True
                state["all_loaded"] = True
                end_row = Adw.ActionRow(
                    title=_("Showing first {count} results").format(count=_MODRINTH_MAX_RESULTS),
                    subtitle=_("Refine your search to see more"),
                )
                end_row.set_activatable(False)
                end_row.add_css_class("dim-label")
                results.append(end_row)
            if state["all_loaded"]:
                hide_more_spinner()
            restore_scroll(saved_value)
            _apply_row_mode()
            # Still inside the prefetch zone (e.g. tall viewport)? Keep going.
            maybe_prefetch()

        def do_search(reset: bool = False):
            if reset:
                state["offset"] = 0
                state["all_loaded"] = False
                state["generation"] = state.get("generation", 0) + 1
                state["errors"] = 0
                state["empty_streak"] = 0
                state["loaded"] = 0
                state["end_shown"] = False
                hide_more_spinner()
                hide_load_error()
                clear_results()
                # Show loading feedback in the list itself so the search
                # pill's clear/filter buttons stay put while the request runs.
                show_more_spinner()
            generation = state.get("generation", 0)
            q = entry.get_text().strip()
            mc_version = self._server_info.mc_version if self._server_info else ""
            qtxt = q
            set_busy(True)
            offset = int(state["offset"])
            category = selected_category() or None
            sort_key = selected_sort()
            project_type = selected_project_type()

            def thread_fn():
                try:
                    hits, total = modrinth_client.search_mods(
                        qtxt,
                        limit=page_size,
                        offset=offset,
                        sort=sort_key,
                        game_version=(mc_version if mc_version else None),
                        category=category,
                        loader=self._server_loader(),
                        server_side_only=(project_type != "datapack"),
                        project_type=project_type,
                    )
                    GLib.idle_add(
                        lambda h=hits, t=total, v=mc_version, qq=qtxt, a=not reset, g=generation: finish_search(
                            h, t, None, v, qq, a, g
                        )
                    )
                except Exception as ex:
                    GLib.idle_add(
                        lambda e=str(ex), v=mc_version, qq=qtxt, a=not reset, g=generation: finish_search(
                            [], 0, e, v, qq, a, g
                        )
                    )

            if not reset:
                show_more_spinner()
            threading.Thread(target=thread_fn, daemon=True).start()

        def do_search_more(*_):
            if state["busy"] or state["all_loaded"]:
                return
            if state.get("error_row") is not None:
                # A failed page waits for an explicit tap-to-retry instead
                # of burning requests on every scroll.
                return
            state["offset"] += page_size
            do_search(reset=False)

        def maybe_prefetch():
            if state["busy"] or state["all_loaded"]:
                return
            adj = sw.get_vadjustment()
            if adj.get_upper() <= adj.get_page_size():
                return
            if adj.get_value() + adj.get_page_size() >= adj.get_upper() - prefetch_px:
                do_search_more()

        def on_scroll(*_):
            maybe_prefetch()

        # One width watcher for all rows (instead of a tick callback per
        # row running every frame): flip each row's compact/expanded stack
        # only when the narrow/wide threshold is crossed.
        row_mode = {"compact": True}

        def _apply_row_mode() -> None:
            try:
                name = _modrinth_row_mode_for_width(results.get_width())
            except Exception:
                return
            if (name == "compact") == row_mode["compact"]:
                return
            row_mode["compact"] = name == "compact"
            index = 0
            while True:
                row = results.get_row_at_index(index)
                if row is None:
                    break
                try:
                    stack = row.get_child()
                    if isinstance(stack, Gtk.Stack) and stack.get_visible_child_name() != name:
                        stack.set_visible_child_name(name)
                except Exception:
                    pass
                index += 1

        def on_row_width_changed(*_args):
            _apply_row_mode()

        results.connect("notify::width", on_row_width_changed)

        def trigger_search(*_):
            update_search_hint()
            do_search(reset=True)
            return False

        entry.connect("search-changed", trigger_search)
        entry.connect("activate", trigger_search)

        def wire_filter_buttons(buttons, selected_idx_ref):
            def handle_click(btn, idx):
                if btn.get_active():
                    for j, other in enumerate(buttons):
                        if j != idx:
                            other.set_active(False)
                    selected_idx_ref[0] = idx
                    trigger_search()
                    filter_btn.set_active(False)
                else:
                    btn.set_active(True)

            for i, btn in enumerate(buttons):
                btn.connect("clicked", lambda b, idx=i: handle_click(b, idx))

        wire_filter_buttons(type_buttons, selected_type_idx)
        wire_filter_buttons(cat_buttons, selected_cat_idx)
        wire_filter_buttons(sort_buttons, selected_sort_idx)

        sw = Gtk.ScrolledWindow()
        sw.add_css_class("mod-scroll")
        sw.set_vexpand(True)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp()
        clamp.set_child(results)
        clamp.set_maximum_size(900)
        sw.set_child(clamp)

        adj = sw.get_vadjustment()
        adj.connect("value-changed", on_scroll)

        outer.append(sw)

        # Run initial discovery search when opening the page.
        update_search_hint()
        GLib.idle_add(lambda: do_search(reset=True) or False)
        tv.set_content(outer)
        return tv

    def _empty_listbox_row(self, title: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        label = Gtk.Label(label=title, xalign=0.0)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_lines(3)
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.set_margin_top(10)
        label.set_margin_bottom(10)
        row.set_child(label)
        return row

    def _looks_installed(self, hit, installed_names: set[str]) -> bool:
        slug = (hit.slug or "").strip().lower()
        if slug and any(slug in n for n in installed_names):
            return True
        needle = (hit.title or "").strip().lower().replace(" ", "-")
        if needle and any(needle in n for n in installed_names):
            return True
        return False

    def _installed_mod_names(self) -> set[str]:
        mods_dir = self._content_dir(self._server_dir())
        if not mods_dir or not mods_dir.is_dir():
            return set()
        return {p.name.lower() for p in mods_dir.glob("*.jar")}

    def _installed_plugin_names(self) -> set[str]:
        plugins_dir = self._plugins_dir()
        if not plugins_dir or not plugins_dir.is_dir():
            return set()
        return {p.name.lower() for p in plugins_dir.glob("*.jar")}

    def _refresh_modrinth_rows_install_state(self) -> None:
        if not hasattr(self, "_modrinth_results_list"):
            return
        installed_names = self._installed_mod_names()
        installed_plugin_names = self._installed_plugin_names()
        i = 0
        while True:
            row = self._modrinth_results_list.get_row_at_index(i)
            if row is None:
                break
            hit = getattr(row, "_hit", None)
            if hit is not None:
                _set_row_btn = getattr(row, "_set_row_btn", None)
                if _set_row_btn is None:
                    i += 1
                    continue
                is_modpack = getattr(row, "_is_modpack", False)
                is_datapack = getattr(row, "_is_datapack", False)
                is_plugin = bool(getattr(row, "_is_plugin", False)) or self._is_plugin_hit(hit)
                btn_label = getattr(row, "_install_btn_label", _("Install"))
                best_version = getattr(row, "_best_version", [None])
                best = (best_version or [None])[0]
                names = installed_plugin_names if is_plugin else installed_names
                label, sensitive = self._decide_row_install_button(
                    hit,
                    best,
                    names,
                    is_modpack=is_modpack,
                    is_datapack=is_datapack,
                    is_plugin=is_plugin,
                    btn_label=btn_label,
                )
                _set_row_btn(label, sensitive)
            i += 1

    def _configure_known_mod_after_download(self, hit, as_plugin: bool = False) -> None:
        if not self._server_manager or not self._server_info:
            return
        slug = str(getattr(hit, "slug", "") or "").strip().lower()
        title = str(getattr(hit, "title", "") or "").strip().lower()
        project_id = str(getattr(hit, "project_id", "") or "").strip().lower()
        identifiers = {slug, title.replace(" ", "-"), project_id}
        playit = self._server_manager.playit_manager
        server_dir = str(self._server_info.server_dir)
        # Plugin installs (plugins/) use Bukkit-style config paths; mod
        # installs use the loader-specific paths (with Arclight probing so a
        # plugin-style config keeps working when present).
        raw_loader = getattr(self._server_info, "loader_type", "")
        platform = getattr(self._server_info, "arclight_platform", "")
        loader = "paper" if as_plugin else raw_loader

        if "geyser" in identifiers:
            playit.configure_geyser_mod(server_dir, loader=loader, platform=platform)
            return

        if "floodgate" in identifiers:
            playit.configure_floodgate_mod(server_dir, loader=loader, platform=platform)
            return

        if "simple-voice-chat" in identifiers or "voice-chat" in identifiers:
            try:
                from hosty.shared.backend.playit_config import load_playit_config

                cfg = load_playit_config(self._server_info.server_dir)
                endpoint = str(cfg.get("voicechat_endpoint", "")).strip()
            except Exception:
                endpoint = ""
            playit.configure_voicechat_mod(
                server_dir,
                self._server_info.id,
                endpoint=endpoint,
                voicechat_port=int(cfg.get("voicechat_port", 24454)),
                loader="paper" if as_plugin else raw_loader,
                platform=platform,
            )

    def _load_icon_async(self, images, url: str, size: int = 44) -> None:
        """Fetch a project icon and apply it to one or more Gtk.Images.

        Textures are cached in memory (bounded) so the two layouts of a
        row share a single fetch, and rebuilds never re-download. Fetches
        are pooled so a page of results can't stall the loop.
        """
        if isinstance(images, Gtk.Image):
            images = [images]
        images = [im for im in (images or []) if im is not None]
        if not images or not url:
            return
        try:
            cache = getattr(self, "_modrinth_icon_textures", None)
            if cache is None:
                cache = {}
                self._modrinth_icon_textures = cache
            texture = cache.get(url)
            if texture is not None:
                for im in images:
                    try:
                        im.set_from_paintable(texture)
                    except Exception:
                        pass
                return
        except Exception:
            pass

        def worker():
            try:
                from hosty.shared.backend import modrinth_client

                with _ICON_FETCH_SEMAPHORE:
                    path = modrinth_client.get_icon_path(url)
                    if not path:
                        return
                    with open(path, "rb") as f:
                        data = f.read()

                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if not pixbuf:
                    return
                scaled = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR) or pixbuf
                texture = Gdk.Texture.new_for_pixbuf(scaled)

                try:
                    cache = getattr(self, "_modrinth_icon_textures", None)
                    if cache is None:
                        cache = {}
                        self._modrinth_icon_textures = cache
                    if len(cache) >= _MODRINTH_ICON_CACHE_MAX:
                        try:
                            cache.pop(next(iter(cache)))
                        except Exception:
                            pass
                    cache[url] = texture
                except Exception:
                    pass

                def ui_set():
                    for im in images:
                        try:
                            im.set_from_paintable(texture)
                        except Exception:
                            pass

                GLib.idle_add(ui_set)
            except Exception:
                return

        threading.Thread(target=worker, daemon=True).start()

    def _on_modrinth_row_activated(self, listbox, listbox_row):
        hit = getattr(listbox_row, "_hit", None)
        if hit is not None:
            detail_page = self._build_modrinth_detail_page(hit)
            self._modrinth_nav.push(detail_page)

    def _make_modrinth_row(
        self,
        hit,
        mc_version: str,
        installed_names: set[str],
        installed_packs: set[str] | None = None,
        installed_dps: set[str] | None = None,
        installed_plugins: set[str] | None = None,
    ) -> Gtk.ListBoxRow:
        from hosty.shared.backend import modrinth_client

        ptype = str(getattr(hit, "project_type", "mod")).lower()
        is_modpack = ptype == "modpack"
        is_datapack = ptype == "datapack"
        is_plugin = ptype == "plugin"

        row = Gtk.ListBoxRow()
        row.set_activatable(True)
        row.add_css_class("mod-card-row")
        row.add_css_class("card")
        row.set_margin_bottom(6)

        btn_label = _("Install")

        compact_install = Gtk.Button(label=btn_label)
        compact_install.add_css_class("mod-install-btn-fixed")
        compact_install.set_valign(Gtk.Align.CENTER)
        expanded_install = Gtk.Button(label=btn_label)
        expanded_install.add_css_class("mod-install-btn-expanded")

        row_btns = [compact_install, expanded_install]

        def _set_row_btn(label=None, sensitive=None):
            for b in row_btns:
                if label is not None:
                    b.set_label(label)
                if sensitive is not None:
                    b.set_sensitive(sensitive)

        pid = str(getattr(hit, "project_id", "") or "").strip()

        def _installed(kind: str) -> bool:
            if kind == "modpack":
                return pid in installed_packs if installed_packs is not None else self._is_modpack_installed(pid)
            if kind == "datapack":
                return pid in installed_dps if installed_dps is not None else self._is_datapack_installed(pid)
            return pid in installed_plugins if installed_plugins is not None else self._is_plugin_installed(pid)

        if is_modpack and _installed("modpack"):
            _set_row_btn(_("Installed"), False)
        elif is_datapack and _installed("datapack"):
            _set_row_btn(_("Installed"), False)
        elif is_plugin and _installed("plugin"):
            _set_row_btn(_("Installed"), False)
        elif (not is_modpack) and (not is_datapack) and (not is_plugin) and self._looks_installed(hit, installed_names):
            _set_row_btn(_("Installed"), False)

        def _mk_text_col():
            c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            c.set_hexpand(True)
            tl = Gtk.Label(label=hit.title, xalign=0.0)
            tl.add_css_class("title-4")
            tl.set_wrap(False)
            tl.set_ellipsize(Pango.EllipsizeMode.END)
            c.append(tl)
            dl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            dl_i = Gtk.Image.new_from_icon_name("folder-download-symbolic")
            dl_i.set_pixel_size(12)
            dl_i.add_css_class("dim-label")
            dl_box.append(dl_i)
            dl_l = Gtk.Label(label=_format_compact_count(int(hit.downloads or 0)), xalign=0.0)
            dl_l.add_css_class("caption")
            dl_l.add_css_class("dim-label")
            dl_box.append(dl_l)
            c.append(dl_box)
            dt = (hit.description or "").strip()
            if dt:
                d = Gtk.Label(label=dt, xalign=0.0)
                d.set_wrap(True)
                d.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                d.set_lines(2)
                d.set_ellipsize(Pango.EllipsizeMode.END)
                d.add_css_class("dim-label")
                d.add_css_class("caption")
                c.append(d)
            return c

        def _mk_icon():
            ic = Gtk.Image.new_from_icon_name("application-x-addon-symbolic")
            ic.set_pixel_size(48)
            ic.set_valign(Gtk.Align.START)
            return ic

        compact_icon = _mk_icon()
        expanded_icon = _mk_icon()
        if hit.icon_url:
            # One fetch feeds both layouts (plus the memory cache).
            self._load_icon_async([compact_icon, expanded_icon], hit.icon_url, size=48)

        def _mk_chevron():
            ch = Gtk.Image.new_from_icon_name("go-next-symbolic")
            ch.set_pixel_size(16)
            ch.add_css_class("dim-label")
            ch.set_valign(Gtk.Align.CENTER)
            return ch

        compact = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        compact.set_margin_start(12)
        compact.set_margin_end(6)
        compact.set_margin_top(10)
        compact.set_margin_bottom(10)
        compact.append(compact_icon)
        compact.append(_mk_text_col())
        compact.append(compact_install)
        compact.append(_mk_chevron())

        expanded = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        expanded.set_margin_start(12)
        expanded.set_margin_end(6)
        expanded.set_margin_top(10)
        expanded.set_margin_bottom(10)
        expanded_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        expanded_top.append(expanded_icon)
        expanded_top.append(_mk_text_col())
        expanded_top.append(_mk_chevron())
        expanded.append(expanded_top)
        expanded_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        expanded_actions.set_margin_top(2)
        expanded_spacer = Gtk.Box()
        expanded_spacer.set_hexpand(True)
        expanded_actions.append(expanded_spacer)
        expanded_actions.append(expanded_install)
        expanded.append(expanded_actions)

        row_stack = Gtk.Stack()
        row_stack.set_hhomogeneous(False)
        row_stack.set_vhomogeneous(False)
        row_stack.add_named(compact, "compact")
        row_stack.add_named(expanded, "expanded")
        row.set_child(row_stack)

        best_version = [None]

        def on_install(*_b):
            if self._is_running():
                self._alert(_("Server is running"), _stop_before_install_message(is_plugin))
                return
            if not mc_version and not is_datapack:
                self._alert(_("Unknown version"), _("Could not read Minecraft version for this server."))
                return

            chosen = best_version[0]
            if not chosen:
                self._alert(_("No compatible version"), _("No compatible server version is available."))
                return

            op_token = self._begin_mod_operation()
            if not op_token:
                self._alert(_("No server selected"), _select_before_install_message(is_plugin))
                return

            _set_row_btn(_("Installing…"), False)
            self._perform_install(
                hit, chosen, mc_version, row_btns, btn_label, is_modpack, is_datapack, op_token, is_plugin
            )

        def load_best_version():
            if not mc_version and not is_datapack:
                _set_row_btn(sensitive=False)
                return
            try:
                if is_datapack:
                    loader_for_query = "datapack"
                elif is_plugin:
                    loader_for_query = "plugin"
                else:
                    loader_for_query = self._server_loader()
                if is_plugin:
                    with _VERSION_FETCH_SEMAPHORE:
                        versions = modrinth_client.find_compatible_plugin_versions(
                            hit.project_id,
                            mc_version,
                            limit=1,
                        )
                else:
                    with _VERSION_FETCH_SEMAPHORE:
                        versions = modrinth_client.find_compatible_versions(
                            hit.project_id,
                            mc_version,
                            loader=loader_for_query,
                            limit=1,
                        )
                if not versions:
                    _set_row_btn(sensitive=False)
                    return

                best_version[0] = versions[0]

                def ui_update():
                    label, sensitive = self._decide_row_install_button(
                        hit,
                        best_version[0],
                        installed_names,
                        is_modpack=is_modpack,
                        is_datapack=is_datapack,
                        is_plugin=is_plugin,
                        btn_label=btn_label,
                        installed_packs=installed_packs,
                        installed_dps=installed_dps,
                        installed_plugins=installed_plugins,
                    )
                    _set_row_btn(label, sensitive)

                GLib.idle_add(ui_update)

            except Exception:
                _set_row_btn(sensitive=False)

        compact_install.connect("clicked", on_install)
        expanded_install.connect("clicked", on_install)
        row._hit = hit
        row._is_modpack = is_modpack
        row._is_datapack = is_datapack
        row._is_plugin = is_plugin
        row._install_btn_label = btn_label
        row._set_row_btn = _set_row_btn
        row._best_version = best_version
        threading.Thread(target=load_best_version, daemon=True).start()
        return row

    def _perform_install(
        self,
        hit,
        chosen,
        mc_version: str,
        install_btns: list[Gtk.Button],
        btn_label: str,
        is_modpack: bool,
        is_datapack: bool,
        op_token: str,
        is_plugin: bool = False,
    ) -> None:
        from hosty.shared.backend import modrinth_client

        def _set_btns(label=None, sensitive=None):
            for b in install_btns:
                if label is not None:
                    b.set_label(label)
                if sensitive is not None:
                    b.set_sensitive(sensitive)

        if is_plugin:

            def ui_ok_plugin(fname: str):
                _set_btns(_("Installed"), False)
                self._record_plugin_install(
                    hit.project_id,
                    hit.title,
                    chosen.version_id,
                    chosen.filename,
                    version_number=chosen.version_number,
                )
                self._toast(_("Installed plugin {}").format(fname))
                self._end_mod_operation(op_token)
                self._rebuild_lists()

            def ui_err_plugin(msg: str):
                _set_btns(_("Install"), True)
                self._end_mod_operation(op_token)
                self._alert(_("Install failed"), msg)

            def install_plugin_thread():
                GLib.idle_add(self._progress_begin)
                try:
                    root = self._server_dir()
                    if not root:
                        raise RuntimeError("No server selected.")
                    plugins_dir = self._plugins_dir()
                    if not plugins_dir:
                        raise RuntimeError("This server does not support plugins.")
                    plugins_dir.mkdir(parents=True, exist_ok=True)

                    old_state = self._read_plugin_state().get("plugins", {}).get(hit.project_id, None)
                    if old_state:
                        old_filename = str(old_state.get("filename", "")).strip()
                        if old_filename and old_filename.lower() != chosen.filename.lower():
                            old_path = plugins_dir / old_filename
                            if old_path.exists():
                                old_path.unlink(missing_ok=True)

                    dest = plugins_dir / chosen.filename
                    modrinth_client.download_to(
                        chosen.download_url,
                        dest,
                        expected_hashes=chosen.hashes,
                        progress_callback=self._install_progress_reporter(0, 1),
                    )
                    self._configure_known_mod_after_download(hit, as_plugin=True)
                    GLib.idle_add(lambda f=chosen.filename: ui_ok_plugin(f))
                except Exception as e:
                    GLib.idle_add(lambda m=str(e): ui_err_plugin(m))
                finally:
                    GLib.idle_add(self._progress_end)

            threading.Thread(target=install_plugin_thread, daemon=True).start()
            return

        if is_datapack:

            def ui_ok_dp(fname: str, dep_count: int):
                _set_btns(_("Installed"), False)
                self._record_datapack_install(
                    hit.project_id,
                    hit.title,
                    chosen.version_id,
                    chosen.filename,
                    version_number=chosen.version_number,
                )
                if dep_count > 0:
                    self._toast(_("Installed {} required dependencies").format(dep_count))
                self._toast(_("Installed datapack {}").format(fname))
                self._end_mod_operation(op_token)
                self._rebuild_lists()

            def ui_err_dp(msg: str):
                _set_btns(_("Install"), True)
                self._end_mod_operation(op_token)
                self._alert(_("Install failed"), msg)

            def install_dp_thread(deps_to_install: list):
                total_files = len(deps_to_install) + 1
                GLib.idle_add(self._progress_begin)
                try:
                    root = self._server_dir()
                    if not root:
                        raise RuntimeError("No server selected.")
                    dp_dir = self._datapacks_dir()
                    if not dp_dir:
                        raise RuntimeError("Could not determine datapacks folder.")
                    dp_dir.mkdir(parents=True, exist_ok=True)

                    installed_dep_count = 0
                    for idx, dep in enumerate(deps_to_install):
                        dep_dest = dp_dir / dep.filename
                        modrinth_client.download_to(
                            dep.download_url,
                            dep_dest,
                            expected_hashes=dep.hashes,
                            progress_callback=self._install_progress_reporter(idx, total_files),
                        )
                        self._record_datapack_install(
                            dep.project_id,
                            dep.name or dep.filename,
                            dep.version_id,
                            dep.filename,
                            version_number=dep.version_number,
                        )
                        installed_dep_count += 1

                    dest = dp_dir / chosen.filename
                    modrinth_client.download_to(
                        chosen.download_url,
                        dest,
                        expected_hashes=chosen.hashes,
                        progress_callback=self._install_progress_reporter(total_files - 1, total_files),
                    )
                    GLib.idle_add(lambda f=chosen.filename, c=installed_dep_count: ui_ok_dp(f, c))
                except Exception as e:
                    GLib.idle_add(lambda m=str(e): ui_err_dp(m))
                finally:
                    GLib.idle_add(self._progress_end)

            def prompt_dp_dependencies(deps_to_install: list):
                if not deps_to_install:
                    threading.Thread(target=install_dp_thread, args=([],), daemon=True).start()
                    return

                dep_names = [d.filename for d in deps_to_install]
                preview = "\n".join([f"- {n}" for n in dep_names[:6]])
                more = ""
                if len(dep_names) > 6:
                    more = f"\n- and {len(dep_names) - 6} more"

                dialog = Adw.AlertDialog()
                dialog.set_heading(_("Install required dependencies?"))
                dialog.set_body(
                    _("This datapack requires additional dependencies:\n\n{}{}\n\nInstall them as well?").format(
                        preview, more
                    )
                )
                dialog.add_response("cancel", _("Cancel"))
                dialog.add_response("install", _("Install"))
                dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
                dialog.set_default_response("install")
                dialog.set_close_response("cancel")

                def on_response(_d, response):
                    if response == "install":
                        threading.Thread(target=install_dp_thread, args=(deps_to_install,), daemon=True).start()
                    else:
                        _set_btns(_("Install"), True)
                        self._end_mod_operation(op_token)

                dialog.connect("response", on_response)
                dialog.present(self.get_root())

            def resolve_and_prompt_dp():
                try:
                    deps = modrinth_client.resolve_required_dependencies(
                        chosen.version_id,
                        mc_version,
                        loader="datapack",
                    )
                    dp_state = self._read_datapack_state().get("datapacks", {})
                    installed_ids = set(dp_state.keys())

                    deps_to_install = []
                    for dep in deps:
                        if dep.project_id in installed_ids:
                            continue
                        if dep.project_id == hit.project_id:
                            continue
                        deps_to_install.append(dep)

                    GLib.idle_add(lambda d=deps_to_install: prompt_dp_dependencies(d))
                except Exception as e:
                    GLib.idle_add(lambda m=str(e): ui_err_dp(m))

            threading.Thread(target=resolve_and_prompt_dp, daemon=True).start()
            return

        if is_modpack:
            _set_btns(_("Installing..."))

            def ui_ok_pack(downloaded_count: int, override_count: int, managed_mods: list[str], file_details: dict):
                _set_btns(_("Installed"), False)
                self._record_modpack_install(
                    hit.project_id,
                    chosen.version_id,
                    version_number=chosen.version_number,
                    title=hit.title,
                    mod_files=sorted(
                        {
                            str(name).strip().lower()
                            for name in managed_mods
                            if str(name).strip().lower().endswith(".jar")
                        }
                    ),
                    file_details=file_details,
                )
                self._toast(_("Installed modpack ({} files)").format(downloaded_count))
                self._end_mod_operation(op_token)
                self._rebuild_lists()

            def ui_err_pack(msg: str):
                if self._is_modpack_installed(hit.project_id):
                    _set_btns(_("Installed"), False)
                    self._end_mod_operation(op_token)
                    self._alert(_("Install failed"), msg)
                    return
                _set_btns(_("Install"), True)
                self._end_mod_operation(op_token)
                self._alert(_("Install failed"), msg)

            def ui_progress_pack(done: int, total: int):
                if int(total) <= 0:
                    _set_btns(_("Installing..."))
                else:
                    _set_btns(f"{done}/{total}")

            def install_pack_thread():
                GLib.idle_add(self._progress_begin)
                try:
                    root = self._server_dir()
                    if not root:
                        raise RuntimeError("No server selected.")

                    def on_pack_progress(d: int, t: int, rel_path: str):
                        if int(t) > 0:
                            GLib.idle_add(self._progress_update, min(1.0, int(d) / int(t)))
                        else:
                            GLib.idle_add(self._progress_update, None)
                        GLib.idle_add(lambda dd=d, tt=t: ui_progress_pack(dd, tt))

                    result = modrinth_client.install_modpack(
                        chosen.version_id,
                        root,
                        progress_callback=on_pack_progress,
                    )
                    d, o, m = (
                        result.downloaded_files,
                        result.extracted_override_files,
                        result.managed_mod_files,
                    )
                    details = dict(result.mod_file_details or {})
                    GLib.idle_add(lambda: ui_ok_pack(d, o, m, details))
                except Exception as e:
                    GLib.idle_add(lambda m=str(e): ui_err_pack(m))
                finally:
                    GLib.idle_add(self._progress_end)

            threading.Thread(target=install_pack_thread, daemon=True).start()
            return

        def ui_ok(fname: str, dep_count: int):
            _set_btns(_("Installed"), False)
            self._record_individual_mod_install(
                hit.project_id,
                hit.title,
                chosen.version_id,
                chosen.filename,
                version_number=chosen.version_number,
            )
            if dep_count > 0:
                self._toast(_("Installed {} required dependencies").format(dep_count))
            self._toast(_("Installed {}").format(fname))
            if self._is_running():
                self._toast(_("Restart the server for mod changes to apply"))
            self._end_mod_operation(op_token)
            self._rebuild_lists()

        def ui_err(msg: str):
            _set_btns(_("Install"), True)
            self._end_mod_operation(op_token)
            self._alert(_("Install failed"), msg)

        def thread_fn(deps_to_install: list, all_required_deps: list):
            total_files = len(deps_to_install) + 1
            GLib.idle_add(self._progress_begin)
            try:
                root = self._server_dir()
                if not root:
                    raise RuntimeError("No server selected.")

                mods_dir = self._content_dir(root)
                if not mods_dir:
                    raise RuntimeError("No server selected.")
                mods_dir.mkdir(parents=True, exist_ok=True)
                installed_names_local = {p.name.lower() for p in mods_dir.glob("*.jar")}

                # Delete old version if replacing an existing install
                old_state = self._read_individual_mod_state().get("mods", {}).get(hit.project_id, None)
                if old_state:
                    old_filename = old_state.get("filename", "")
                    if old_filename and old_filename.lower() != chosen.filename.lower():
                        old_path = mods_dir / old_filename
                        if old_path.exists():
                            old_path.unlink(missing_ok=True)
                        installed_names_local.discard(old_filename.lower())

                installed_dep_count = 0
                for idx, dep in enumerate(deps_to_install):
                    dep_name = dep.filename.lower()
                    if dep_name in installed_names_local:
                        continue
                    if dep_name == chosen.filename.lower():
                        continue
                    dep_dest = mods_dir / dep.filename
                    modrinth_client.download_to(
                        dep.download_url,
                        dep_dest,
                        expected_hashes=dep.hashes,
                        progress_callback=self._install_progress_reporter(idx, total_files),
                    )
                    installed_names_local.add(dep_name)
                    installed_dep_count += 1

                dest = mods_dir / chosen.filename
                modrinth_client.download_to(
                    chosen.download_url,
                    dest,
                    expected_hashes=chosen.hashes,
                    progress_callback=self._install_progress_reporter(total_files - 1, total_files),
                )
                self._record_dependency_installs(chosen.filename, all_required_deps)
                self._configure_known_mod_after_download(hit)
                GLib.idle_add(lambda f=chosen.filename, c=installed_dep_count: ui_ok(f, c))
            except Exception as e:
                GLib.idle_add(lambda m=str(e): ui_err(m))
            finally:
                GLib.idle_add(self._progress_end)

        def prompt_dependencies(deps_to_install: list, all_required_deps: list):
            if not deps_to_install:
                threading.Thread(target=thread_fn, args=([], all_required_deps), daemon=True).start()
                return

            dep_names = [d.filename for d in deps_to_install]
            preview = "\n".join([f"- {n}" for n in dep_names[:6]])
            more = ""
            if len(dep_names) > 6:
                more = f"\n- and {len(dep_names) - 6} more"

            dialog = Adw.AlertDialog()
            dialog.set_heading(_("Install required dependencies?"))
            dialog.set_body(
                _("This mod requires additional dependencies:\n\n{}{}\n\nInstall them as well?").format(preview, more)
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("install", _("Install"))
            dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("install")
            dialog.set_close_response("cancel")

            def on_response(_d, response):
                if response == "install":
                    threading.Thread(target=thread_fn, args=(deps_to_install, all_required_deps), daemon=True).start()
                else:
                    _set_btns(_("Install"), True)
                    self._end_mod_operation(op_token)

            dialog.connect("response", on_response)
            dialog.present(self.get_root())

        def resolve_and_prompt():
            try:
                root = self._server_dir()
                if not root:
                    raise RuntimeError("No server selected.")

                mods_dir = self._content_dir(root)
                if not mods_dir:
                    raise RuntimeError("No server selected.")
                mods_dir.mkdir(parents=True, exist_ok=True)
                installed_names_local = {p.name.lower() for p in mods_dir.glob("*.jar")}
                deps = modrinth_client.resolve_required_dependencies(
                    chosen.version_id,
                    mc_version,
                    loader=self._server_loader(),
                )
                deps_to_install = []
                for dep in deps:
                    dep_name = dep.filename.lower()
                    if dep_name in installed_names_local:
                        continue
                    if dep_name == chosen.filename.lower():
                        continue
                    deps_to_install.append(dep)

                GLib.idle_add(lambda d=deps_to_install, a=deps: prompt_dependencies(d, a))
            except Exception as e:
                GLib.idle_add(lambda m=str(e): ui_err(m))

        threading.Thread(target=resolve_and_prompt, daemon=True).start()

    def _build_modrinth_detail_page(self, hit) -> Adw.NavigationPage:
        from hosty.shared.backend import modrinth_client

        ptype = str(getattr(hit, "project_type", "mod")).lower()
        is_modpack = ptype == "modpack"
        is_datapack = ptype == "datapack"
        is_plugin = ptype == "plugin"

        btn_label = _("Install")

        tv = Adw.ToolbarView()
        tv.set_hexpand(True)
        tv.set_vexpand(True)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(True)

        tv.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(24)
        content.set_margin_bottom(24)

        mc_version = self._server_info.mc_version if self._server_info else ""

        install_btn_wide = Gtk.Button(label=btn_label)
        install_btn_wide.add_css_class("suggested-action")
        install_btn_wide.add_css_class("mod-install-btn-large")
        install_btn_wide.add_css_class("pill")
        install_btn_narrow = Gtk.Button(label=btn_label)
        install_btn_narrow.add_css_class("suggested-action")
        install_btn_narrow.add_css_class("mod-install-btn-large")
        install_btn_narrow.add_css_class("pill")
        install_btn_narrow.set_halign(Gtk.Align.CENTER)
        install_btn_narrow.set_margin_top(8)

        _detail_btns = [install_btn_wide, install_btn_narrow]

        def _set_dbtn(label=None, sensitive=None):
            for b in _detail_btns:
                if label is not None:
                    b.set_label(label)
                if sensitive is not None:
                    b.set_sensitive(sensitive)

        stats_lbl = Gtk.Label(
            label=f"{_format_compact_count(int(hit.downloads or 0))} downloads",
            xalign=0.0,
        )
        stats_lbl.add_css_class("dim-label")

        icon = Gtk.Image.new_from_icon_name("application-x-addon-symbolic")
        icon.set_pixel_size(72)
        icon.set_valign(Gtk.Align.START)
        if hit.icon_url:
            self._load_icon_async(icon, hit.icon_url, size=72)

        title_lbl = Gtk.Label(label=hit.title, xalign=0.0)
        title_lbl.add_css_class("title-1")
        title_lbl.set_wrap(True)
        title_lbl.set_hexpand(True)

        author_lbl = Gtk.Label(label=f"by {hit.author or 'Unknown'}", xalign=0.0)
        author_lbl.add_css_class("title-4")
        author_lbl.add_css_class("dim-label")

        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title_col.set_hexpand(True)
        title_col.append(title_lbl)
        title_col.append(author_lbl)
        stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        stats_box.set_margin_top(4)
        si = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        si.set_pixel_size(14)
        stats_box.append(si)
        stats_box.append(stats_lbl)
        title_col.append(stats_box)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header_row.set_hexpand(True)
        header_row.append(icon)
        header_row.append(title_col)
        install_btn_wide.set_valign(Gtk.Align.START)
        header_row.append(install_btn_wide)
        content.append(header_row)

        if hit.categories:
            cats_box = Gtk.FlowBox()
            cats_box.set_max_children_per_line(8)
            cats_box.set_selection_mode(Gtk.SelectionMode.NONE)
            cats_box.set_column_spacing(6)
            cats_box.set_row_spacing(4)
            for cat in hit.categories:
                chip = Gtk.Label(label=cat)
                chip.add_css_class("mod-chip")
                chip.add_css_class("caption")
                cats_box.append(chip)
            content.append(cats_box)

        content.append(install_btn_narrow)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        content.append(sep)

        desc_text = (hit.description or "No description available.").strip()
        desc_lbl = Gtk.Label(label=desc_text, xalign=0.0)
        desc_lbl.set_wrap(True)
        desc_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        desc_lbl.set_hexpand(True)
        content.append(desc_lbl)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        version_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        version_title = Gtk.Label(label=_("Versions"), xalign=0.0)
        version_title.add_css_class("title-3")
        version_section.append(version_title)

        version_listbox = Gtk.ListBox()
        version_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        version_listbox.add_css_class("boxed-list")
        loading_row = Gtk.ListBoxRow()
        loading_row.set_activatable(False)
        loading_lbl = Gtk.Label(label=_("Loading versions…"), xalign=0.0, margin_top=10, margin_bottom=10)
        loading_lbl.add_css_class("dim-label")
        loading_row.set_child(loading_lbl)
        version_listbox.append(loading_row)
        version_section.append(version_listbox)
        content.append(version_section)

        open_btn = Gtk.Button(label=_("Open in Modrinth"))
        open_btn.add_css_class("pill")
        open_btn.set_halign(Gtk.Align.START)

        def on_open_page(*args):
            slug = hit.slug or hit.project_id
            if is_modpack:
                route = "modpack"
            elif is_datapack:
                route = "datapack"
            elif is_plugin:
                route = "plugin"
            else:
                route = "mod"
            if not _open_uri(f"https://modrinth.com/{route}/{slug}"):
                self._alert(_("Could not open browser"), _("Unable to open the Modrinth page."))

        open_btn.connect("clicked", on_open_page)
        content.append(open_btn)

        sw = Gtk.ScrolledWindow()
        sw.add_css_class("mod-scroll")
        sw.set_vexpand(True)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(900)
        clamp.set_child(content)
        sw.set_child(clamp)
        tv.set_content(sw)

        install_btn_narrow.set_visible(True)
        install_btn_wide.set_visible(False)

        def _apply_install_btn_mode() -> None:
            try:
                w = sw.get_width()
            except Exception:
                return
            if w >= 600:
                install_btn_wide.set_visible(True)
                install_btn_narrow.set_visible(False)
            elif w > 0:
                install_btn_wide.set_visible(False)
                install_btn_narrow.set_visible(True)

        sw.connect("notify::width", lambda *_: _apply_install_btn_mode())
        _apply_install_btn_mode()

        version_objs: list = []
        selected_index = [0]
        installed_names: set[str] = self._installed_plugin_names() if is_plugin else self._installed_mod_names()

        def selected_version():
            if not version_objs:
                return None
            idx = selected_index[0]
            if idx < 0 or idx >= len(version_objs):
                return None
            return version_objs[idx]

        def _update_version_checkmarks():
            idx = selected_index[0]
            i = 0
            while True:
                r = version_listbox.get_row_at_index(i)
                if r is None:
                    break
                row_box = r.get_child()
                if isinstance(row_box, Gtk.Box):
                    last = row_box.get_last_child()
                    if isinstance(last, Gtk.Image):
                        row_box.remove(last)
                    if i == idx:
                        chk = Gtk.Image.new_from_icon_name("object-select-symbolic")
                        chk.add_css_class("accent-color")
                        row_box.append(chk)
                i += 1

        def _apply_version_selection(idx: int):
            if idx < 0 or idx >= len(version_objs):
                return
            selected_index[0] = idx
            chosen = version_objs[idx]
            _update_version_checkmarks()

            is_installed = False
            label = btn_label
            sensitive = True
            if is_modpack and self._is_modpack_installed(hit.project_id):
                is_installed = True
            elif is_datapack and self._is_datapack_installed(hit.project_id):
                is_installed = True
            elif is_plugin:
                installed_state = self._read_plugin_state().get("plugins", {}).get(hit.project_id, None)
                if installed_state:
                    if installed_state.get("version_id", "") == chosen.version_id:
                        is_installed = True
                    else:
                        label = self._update_direction_label(
                            installed_state.get("version_id", ""),
                            chosen.version_id,
                            [v.version_id for v in version_objs],
                        )
                elif chosen.filename.lower() in installed_names:
                    is_installed = True
            elif not is_modpack and not is_datapack:
                installed_state = self._read_individual_mod_state().get("mods", {}).get(hit.project_id, None)
                if installed_state:
                    if installed_state.get("version_id", "") == chosen.version_id:
                        is_installed = True
                    else:
                        label = self._update_direction_label(
                            installed_state.get("version_id", ""),
                            chosen.version_id,
                            [v.version_id for v in version_objs],
                        )
                elif chosen.filename.lower() in installed_names:
                    is_installed = True

            if is_installed:
                dependents = (
                    self._dependency_dependents(chosen.filename) if not (is_modpack or is_datapack or is_plugin) else []
                )
                _set_dbtn(_("Dependency") if dependents else _("Installed"), False)
            else:
                _set_dbtn(label, sensitive)

        def on_version_row_activated(listbox, listbox_row):
            _apply_version_selection(listbox_row.get_index())

        version_listbox.connect("row-activated", on_version_row_activated)

        def on_install(*_b):
            if self._is_running():
                self._alert(_("Server is running"), _stop_before_install_message(is_plugin))
                return
            if not mc_version and not is_datapack:
                self._alert(_("Unknown version"), _("Could not read Minecraft version for this server."))
                return

            chosen = selected_version()
            if not chosen:
                self._alert(_("No compatible version"), _("No compatible server version is available."))
                return

            op_token = self._begin_mod_operation()
            if not op_token:
                self._alert(_("No server selected"), _select_before_install_message(is_plugin))
                return

            _set_dbtn(_("Installing…"), False)
            self._perform_install(
                hit, chosen, mc_version, _detail_btns, btn_label, is_modpack, is_datapack, op_token, is_plugin
            )

        install_btn_wide.connect("clicked", on_install)
        install_btn_narrow.connect("clicked", on_install)

        def load_detail():
            try:
                project = modrinth_client.get_project(hit.project_id)
                if project:
                    full_desc = str(project.get("description", "") or "").strip()
                    if full_desc and full_desc != desc_text:
                        GLib.idle_add(desc_lbl.set_label, full_desc)

                    follows = int(project.get("follows", 0))
                    if follows:
                        GLib.idle_add(
                            lambda: stats_lbl.set_label(
                                _("{} downloads · {} follows").format(
                                    _format_compact_count(int(hit.downloads or 0)),
                                    _format_compact_count(follows),
                                )
                            )
                        )

                if is_datapack:
                    loader_for_query = "datapack"
                elif is_plugin:
                    loader_for_query = "plugin"
                else:
                    loader_for_query = self._server_loader()
                if is_plugin:
                    all_versions = modrinth_client.find_compatible_plugin_versions(
                        hit.project_id,
                        mc_version,
                        limit=20,
                    )
                else:
                    all_versions = modrinth_client.find_compatible_versions(
                        hit.project_id,
                        mc_version,
                        loader=loader_for_query,
                        limit=20,
                    )

                GLib.idle_add(
                    lambda: self._populate_detail_versions(
                        version_listbox,
                        version_objs,
                        selected_index,
                        all_versions,
                        _detail_btns,
                        btn_label,
                        hit,
                        is_modpack,
                        is_datapack,
                        installed_names,
                        loading_row,
                        is_plugin,
                    )
                )
            except Exception:
                pass

        threading.Thread(target=load_detail, daemon=True).start()

        page = Adw.NavigationPage(title=hit.title, child=tv)
        try:
            page.set_tag("modrinth-detail")
        except Exception:
            pass
        return page

    def _populate_detail_versions(
        self,
        version_listbox: Gtk.ListBox,
        version_objs: list,
        selected_index: list,
        versions: list,
        install_btns: list[Gtk.Button],
        btn_label: str,
        hit,
        is_modpack: bool,
        is_datapack: bool,
        installed_names: set[str],
        loading_row: Gtk.ListBoxRow | None = None,
        is_plugin: bool = False,
    ) -> None:
        def _set_btn(label=None, sensitive=None):
            for b in install_btns:
                if label is not None:
                    b.set_label(label)
                if sensitive is not None:
                    b.set_sensitive(sensitive)

        if loading_row:
            try:
                version_listbox.remove(loading_row)
            except Exception:
                pass

        if not versions:
            item = Gtk.ListBoxRow()
            item.set_activatable(False)
            lbl = Gtk.Label(label=_("No compatible versions"), xalign=0.0, margin_top=8, margin_bottom=8)
            lbl.add_css_class("dim-label")
            item.set_child(lbl)
            version_listbox.append(item)
            _set_btn(sensitive=False)
            return

        names = []
        seen = set()
        chosen_for_labels = []
        for v in versions:
            vn = (v.version_number or v.name or "").strip()
            if not vn or vn in seen:
                continue
            seen.add(vn)
            names.append(vn)
            chosen_for_labels.append(v)

        version_objs.clear()
        version_objs.extend(chosen_for_labels)

        # Installed version id for the badge below (selection keeps the
        # check icon; the badge shows what is actually on disk).
        installed_version_id = ""
        try:
            if is_modpack:
                installed_version_id = str(
                    (self._modpack_entries().get(hit.project_id, {}) or {}).get("version_id", "")
                )
            elif is_datapack:
                installed_version_id = str(
                    (self._read_datapack_state().get("datapacks", {}).get(hit.project_id, {}) or {}).get(
                        "version_id", ""
                    )
                )
            elif is_plugin:
                installed_version_id = str(
                    (self._read_plugin_state().get("plugins", {}).get(hit.project_id, {}) or {}).get("version_id", "")
                )
            else:
                installed_version_id = str(
                    (self._read_individual_mod_state().get("mods", {}).get(hit.project_id, {}) or {}).get(
                        "version_id", ""
                    )
                )
        except Exception:
            installed_version_id = ""
        installed_version_id = installed_version_id.strip()

        def _row_is_installed(version) -> bool:
            if installed_version_id and str(version.version_id or "").strip() == installed_version_id:
                return True
            return not installed_version_id and str(version.filename or "").lower() in installed_names

        for i, name in enumerate(names):
            lbl = Gtk.Label(label=name, xalign=0.0)
            lbl.set_hexpand(True)

            game_vers = (chosen_for_labels[i].game_versions or [])[:3]
            gv_lbl = Gtk.Label(
                label=", ".join(game_vers) if game_vers else "",
                xalign=1.0,
            )
            gv_lbl.add_css_class("caption")
            gv_lbl.add_css_class("dim-label")

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row_box.set_margin_start(12)
            row_box.set_margin_end(12)
            row_box.set_margin_top(8)
            row_box.set_margin_bottom(8)
            row_box.append(lbl)
            row_box.append(gv_lbl)

            if _row_is_installed(chosen_for_labels[i]):
                # Plain label (not an image): the selection checkmark updater
                # only strips trailing images, so the badge survives.
                installed_lbl = Gtk.Label(label=_("Installed"))
                installed_lbl.add_css_class("caption")
                installed_lbl.add_css_class("dim-label")
                row_box.append(installed_lbl)

            if i == 0:
                chk = Gtk.Image.new_from_icon_name("object-select-symbolic")
                chk.add_css_class("accent-color")
                row_box.append(chk)

            item = Gtk.ListBoxRow()
            item.set_activatable(True)
            item.set_child(row_box)
            version_listbox.append(item)

        selected_index[0] = 0
        first = version_objs[0]

        is_installed = False
        label = btn_label
        sensitive = True
        if is_modpack and self._is_modpack_installed(hit.project_id):
            is_installed = True
        elif is_datapack and self._is_datapack_installed(hit.project_id):
            is_installed = True
        elif is_plugin:
            installed_state = self._read_plugin_state().get("plugins", {}).get(hit.project_id, None)
            if installed_state:
                if installed_state.get("version_id", "") == first.version_id:
                    is_installed = True
                else:
                    label = self._update_direction_label(
                        installed_state.get("version_id", ""),
                        first.version_id,
                        [v.version_id for v in version_objs],
                    )
            elif first.filename.lower() in installed_names:
                is_installed = True
        elif not is_modpack and not is_datapack:
            installed_state = self._read_individual_mod_state().get("mods", {}).get(hit.project_id, None)
            if installed_state:
                if installed_state.get("version_id", "") == first.version_id:
                    is_installed = True
                else:
                    label = self._update_direction_label(
                        installed_state.get("version_id", ""),
                        first.version_id,
                        [v.version_id for v in version_objs],
                    )
            elif first.filename.lower() in installed_names:
                is_installed = True

        if is_installed:
            dependents = (
                self._dependency_dependents(first.filename) if not (is_modpack or is_datapack or is_plugin) else []
            )
            _set_btn(_("Dependency") if dependents else _("Installed"), False)
        else:
            _set_btn(label, sensitive)
