"""
PropertiesView - GUI editor for server.properties.
Uses Adw.PreferencesPage with typed rows.
"""

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from hosty.shared.backend.config_manager import ConfigManager
from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.utils.constants import (
    DEFAULT_RAM_MB,
    DEFAULT_SERVER_PROPERTIES,
    DIFFICULTIES,
    GAMEMODES,
    MAX_RAM_MB,
    MIN_RAM_MB,
    get_required_java_version,
)

DIFFICULTY_MODES = [*DIFFICULTIES, "hardcore"]


class PropertiesView(Gtk.Box):
    """GUI editor for server.properties using Adwaita preference widgets."""

    def __init__(self, toast_overlay=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._config: ConfigManager | None = None
        self._server_manager: ServerManager | None = None
        self._server_info: ServerInfo | None = None
        self._widgets: dict = {}
        self._ram_row: Adw.SpinRow | None = None
        self._suppress_changes = False
        self._app_toast_overlay = toast_overlay

        # Restart banner
        self._banner = Adw.Banner()
        self._banner.set_title(_("Restart the server to apply changes"))
        self._banner.set_button_label(_("Dismiss"))
        self._banner.set_revealed(False)
        self._banner.connect("button-clicked", lambda b: b.set_revealed(False))
        self.append(self._banner)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        page = Adw.PreferencesPage()

        # ===== General Group =====
        general = Adw.PreferencesGroup(title=_("General"))

        self._autostart_row = Adw.SwitchRow(
            title=_("Start on Launch"),
            subtitle=_("Start this server automatically when Hosty opens"),
        )
        general.add(self._autostart_row)

        self._version_row = Adw.ActionRow(title=_("Minecraft Version"), subtitle=_("Unknown"))

        self._change_version_btn = Gtk.Button(icon_name="software-update-available-symbolic", valign=Gtk.Align.CENTER)
        self._change_version_btn.add_css_class("flat")
        self._change_version_btn.set_tooltip_text(_("Upgrade server version"))
        self._change_version_btn.set_sensitive(False)
        self._change_version_btn.connect("clicked", self._on_change_version_clicked)
        self._version_row.add_suffix(self._change_version_btn)
        general.add(self._version_row)

        self._widgets["motd"] = self._add_entry_row(general, _("Message of the Day"), "motd", _("a hosty server"))

        self._widgets["max-players"] = self._add_spin_row(general, _("Max Players"), "max-players", 1, 1000, 20)
        default_difficulty_mode = (
            "hardcore"
            if str(DEFAULT_SERVER_PROPERTIES.get("hardcore", "false")).lower() == "true"
            else str(DEFAULT_SERVER_PROPERTIES.get("difficulty", "easy"))
        )
        self._widgets["difficulty"] = self._add_combo_row(
            general, _("Difficulty"), "difficulty", DIFFICULTY_MODES, default_difficulty_mode
        )
        self._widgets["gamemode"] = self._add_combo_row(
            general, _("Default Gamemode"), "gamemode", GAMEMODES, "survival"
        )

        page.add(general)

        # ===== Resources (Hosty - not in server.properties) =====
        resources = Adw.PreferencesGroup(title=_("Resources"))
        ram_adj = Gtk.Adjustment(
            value=DEFAULT_RAM_MB,
            lower=MIN_RAM_MB,
            upper=MAX_RAM_MB,
            step_increment=256,
            page_increment=1024,
        )
        self._ram_row = Adw.SpinRow(
            title=_("Allocated RAM (MB)"),
            adjustment=ram_adj,
        )
        self._ram_row.set_tooltip_text(_("Megabytes for the Java heap. Range {}–{}.").format(MIN_RAM_MB, MAX_RAM_MB))
        resources.add(self._ram_row)
        page.add(resources)

        # ===== World Group =====
        world = Adw.PreferencesGroup(title=_("World"))

        # level-type is now read-only in the World Manager
        self._widgets["view-distance"] = self._add_spin_row(world, _("View Distance"), "view-distance", 2, 32, 10)
        self._widgets["simulation-distance"] = self._add_spin_row(
            world, _("Simulation Distance"), "simulation-distance", 2, 32, 10
        )
        self._widgets["spawn-protection"] = self._add_spin_row(
            world, _("Spawn Protection Radius"), "spawn-protection", 0, 256, 16
        )
        self._widgets["max-world-size"] = self._add_spin_row(
            world, _("Max World Size"), "max-world-size", 1000, 29999984, 29999984
        )

        page.add(world)

        # ===== Network Group =====
        network = Adw.PreferencesGroup(title=_("Network"))

        self._widgets["enable-query"] = self._add_switch_row(network, _("Enable Query"), "enable-query", False, "")

        page.add(network)

        # ===== Players Group =====
        players = Adw.PreferencesGroup(title=_("Players"))

        self._widgets["pvp"] = self._add_switch_row(players, _("PvP"), "pvp", True, "")
        self._widgets["allow-flight"] = self._add_switch_row(players, _("Allow Flight"), "allow-flight", False, "")
        self._widgets["keep-inventory"] = self._add_switch_row(
            players, _("Keep Inventory"), "keep-inventory", False, ""
        )

        page.add(players)

        # ===== Advanced Group =====
        advanced = Adw.PreferencesGroup(title=_("Advanced"))

        self._widgets["enable-command-block"] = self._add_switch_row(
            advanced, _("Command Blocks"), "enable-command-block", False, ""
        )
        self._widgets["allow-nether"] = self._add_switch_row(advanced, _("Allow Nether"), "allow-nether", True, "")

        page.add(advanced)

        scrolled.set_child(page)
        self.append(scrolled)

        self._connect_auto_save_signals()

    def _connect_auto_save_signals(self):
        for widget in self._widgets.values():
            if isinstance(widget, Adw.SpinRow):
                widget.connect("notify::value", self._on_widget_changed)
            elif isinstance(widget, Adw.EntryRow):
                widget.connect("changed", self._on_widget_changed)
            elif isinstance(widget, Adw.SwitchRow):
                widget.connect("notify::active", self._on_widget_changed)
            elif isinstance(widget, Adw.ComboRow):
                widget.connect("notify::selected", self._on_widget_changed)

        if self._ram_row:
            self._ram_row.connect("notify::value", self._on_widget_changed)

        if self._autostart_row:
            self._autostart_row.connect("notify::active", self._on_autostart_toggled)

    def _on_autostart_toggled(self, row, _pspec):
        if self._suppress_changes or not self._server_manager or not self._server_info:
            return

        active = row.get_active()
        success, err = self._server_manager.set_server_autostart(self._server_info.id, active)

        if not success:
            # Revert the toggle and show error
            self._suppress_changes = True
            row.set_active(not active)
            self._suppress_changes = False

            # Show toast/banner
            self._banner.set_title(err)
            self._banner.set_revealed(True)

    def _on_entry_apply(self, row, title):
        """Handle entry row apply/confirmation."""
        self._show_toast(_("Property updated"))

    def _show_toast(self, message: str, timeout: int = 2):
        """Show a toast notification."""
        if not self._app_toast_overlay:
            return
        toast = Adw.Toast(title=message)
        toast.set_timeout(timeout)
        self._app_toast_overlay.add_toast(toast)

    def _add_entry_row(self, group, title, key, default):
        """Add an Adw.EntryRow to a group."""
        row = Adw.EntryRow(title=title)
        row.set_show_apply_button(True)
        row.set_text(default)
        row._prop_key = key
        row.connect("apply", self._on_entry_apply, title)
        group.add(row)
        return row

    def _add_spin_row(self, group, title, key, min_val, max_val, default):
        """Add an Adw.SpinRow to a group."""
        adj = Gtk.Adjustment(value=default, lower=min_val, upper=max_val, step_increment=1, page_increment=10)
        row = Adw.SpinRow(title=title, adjustment=adj)
        row._prop_key = key
        group.add(row)
        return row

    def _add_switch_row(self, group, title, key, default, subtitle=""):
        """Add an Adw.SwitchRow to a group."""
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_active(default)
        row._prop_key = key
        group.add(row)
        return row

    def _add_combo_row(self, group, title, key, options, default):
        """Add an Adw.ComboRow to a group."""
        string_list = Gtk.StringList.new(options)
        row = Adw.ComboRow(title=title, model=string_list)
        row._prop_key = key
        row._options = options

        # Set default selection
        try:
            idx = options.index(default)
            row.set_selected(idx)
        except ValueError:
            row.set_selected(0)

        group.add(row)
        return row

    def set_config(
        self,
        config: ConfigManager,
        server_manager: ServerManager | None = None,
        server_info: ServerInfo | None = None,
    ):
        """Load a server's config into the view."""
        self._config = config
        self._server_manager = server_manager
        self._server_info = server_info

        if self._server_info and hasattr(self, "_version_row"):
            version_text = self._server_info.mc_version or _("Unknown")
            if self._server_info.loader_version:
                version_text += f" ({self._server_info.loader_version})"
            self._version_row.set_subtitle(version_text)

        if config:
            config.load()
            self._populate()
        self._refresh_upgrade_button()

    def _refresh_upgrade_button(self):
        if not self._server_manager or not self._server_info or not self._change_version_btn:
            return
        self._change_version_btn.set_sensitive(False)
        self._change_version_btn.set_tooltip_text(_("Checking for newer Minecraft versions..."))

        def worker():
            versions = self._server_manager.download_manager.fetch_game_versions()
            current = self._server_info.mc_version
            has_upgrade = any(ServerManager.is_version_after(v, current) for v in versions)

            def done():
                self._change_version_btn.set_sensitive(has_upgrade)
                if has_upgrade:
                    self._change_version_btn.set_tooltip_text(_("Upgrade server version"))
                else:
                    self._change_version_btn.set_tooltip_text(_("No newer Minecraft versions available"))
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_change_version_clicked(self, button):
        if not self._server_manager or not self._server_info:
            self._show_toast(_("Select a server first"), timeout=3)
            return

        dialog = Adw.Dialog()
        dialog.set_title(_("Update Version"))
        dialog.set_content_width(520)
        dialog.set_content_height(420)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        cancel_btn = Gtk.Button(label=_("Cancel"))
        primary_btn = Gtk.Button(label=_("Next"))
        primary_btn.add_css_class("suggested-action")
        primary_btn.set_sensitive(False)
        header.pack_start(cancel_btn)
        header.pack_end(primary_btn)
        toolbar.add_top_bar(header)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        runtime_page = Adw.PreferencesPage()
        runtime_group = Adw.PreferencesGroup(
            title=_("Runtime"),
        )
        mc_values: list[str] = []
        loader_values: list[str] = []
        mc_row = Adw.ComboRow(title=_("Minecraft version"), model=Gtk.StringList.new([_("Loading...")]))
        runtime_group.add(mc_row)

        fabric_version_row = Adw.ActionRow(
            title=_("Fabric loader"),
            subtitle=_("Loading..."),
        )
        fabric_version_row.set_activatable(False)
        runtime_group.add(fabric_version_row)

        java_info_row = Adw.ActionRow(
            title=_("Java Runtime"),
            subtitle=_("Detecting..."),
        )
        java_info_row.set_activatable(False)
        runtime_group.add(java_info_row)

        runtime_page.add(runtime_group)
        stack.add_named(runtime_page, "runtime")

        mods_page = Adw.PreferencesPage()
        review_group = Adw.PreferencesGroup(
            title=_("Mod Compatibility"),
        )
        mods_page.add(review_group)
        stack.add_named(mods_page, "mods")

        progress_page = Adw.PreferencesPage()
        progress_group = Adw.PreferencesGroup(title=_("Updating Server"))
        progress_row = Adw.ActionRow(title=_("Preparing update"), subtitle="")
        progress_spinner = Gtk.Spinner()
        progress_row.add_suffix(progress_spinner)
        progress_group.add(progress_row)
        progress_bar = Gtk.ProgressBar()
        progress_bar.set_margin_top(12)
        progress_bar.set_margin_bottom(12)
        progress_group.add(progress_bar)
        progress_page.add(progress_group)
        stack.add_named(progress_page, "progress")

        review_rows: list[Gtk.Widget] = []
        selected_mc = {"value": ""}
        selected_loader = {"value": ""}
        compatibility_plan: dict = {}

        toolbar.set_content(stack)
        dialog.set_child(toolbar)

        def update_java_info(mc_version: str) -> None:
            if not mc_version or mc_version == _("No versions found"):
                java_info_row.set_subtitle(_("Select a Minecraft version"))
                return
            try:
                java_ver = get_required_java_version(mc_version)
            except Exception:
                java_ver = 21
            java_mgr = self._server_manager.java_manager
            if java_mgr.is_java_available(java_ver):
                java_info_row.set_subtitle(_("Java {} ✓ Available").format(java_ver))
            else:
                system_ver = java_mgr.system_java_version
                if system_ver and system_ver >= java_ver:
                    java_info_row.set_subtitle(
                        _("Java {} needed - system Java {} can be used").format(java_ver, system_ver)
                    )
                else:
                    java_info_row.set_subtitle(_("Java {} needed - will be downloaded automatically").format(java_ver))

        def selected_mc_version() -> str:
            idx = int(mc_row.get_selected())
            if idx < 0 or idx >= len(mc_values):
                return ""
            return mc_values[idx]

        def validate(*_args):
            update_java_info(selected_mc_version())
            primary_btn.set_sensitive(bool(mc_values) and bool(loader_values))

        mc_row.connect("notify::selected", validate)

        def on_cancel(*_args):
            visible = stack.get_visible_child_name()
            if visible == "mods":
                stack.set_visible_child_name("runtime")
                cancel_btn.set_label(_("Cancel"))
                primary_btn.set_label(_("Next"))
                primary_btn.set_sensitive(bool(mc_values) and bool(loader_values))
                return
            if visible == "progress":
                return
            dialog.close()

        cancel_btn.connect("clicked", on_cancel)

        def add_review_row(row: Gtk.Widget) -> None:
            review_group.add(row)
            review_rows.append(row)

        def clear_review_rows() -> None:
            for row in review_rows:
                review_group.remove(row)
            review_rows.clear()

        def add_plan_group(title: str, items: list[dict], fallback: str) -> None:
            if not items:
                add_review_row(Adw.ActionRow(title=fallback))
                return
            expander = Adw.ExpanderRow(title=title, subtitle=_("{} item(s)").format(len(items)))
            for item in items:
                label = str(item.get("title") or item.get("filename") or _("Unknown"))
                version = str(item.get("version_number") or item.get("version_id") or "").strip()
                filename = str(item.get("filename") or item.get("current_filename") or "").strip()
                subtitle = " · ".join([x for x in (version, filename) if x])
                expander.add_row(Adw.ActionRow(title=label, subtitle=subtitle))
            add_review_row(expander)

        def versions_worker():
            games = self._server_manager.download_manager.fetch_game_versions()
            loaders = self._server_manager.download_manager.fetch_loader_versions()

            def loaded():
                current_mc = self._server_info.mc_version
                current_loader = self._server_info.loader_version
                next_games = [v for v in games if ServerManager.is_version_after(v, current_mc)]
                next_loaders = [
                    v for v in loaders if not current_loader or ServerManager.is_version_at_least(v, current_loader)
                ]
                mc_values.clear()
                mc_values.extend(next_games)
                loader_values.clear()
                loader_values.extend(next_loaders)
                mc_row.set_model(Gtk.StringList.new(mc_values or [_("No versions found")]))
                if mc_values:
                    mc_row.set_selected(0)
                # Automatically use the newest loader (first in list)
                if loader_values:
                    selected_loader["value"] = loader_values[0]
                    fabric_version_row.set_subtitle(loader_values[0])
                validate()
                return False

            GLib.idle_add(loaded)

        def show_mod_review(*_args):
            if not mc_values or not loader_values:
                return
            selected_mc["value"] = selected_mc_version()
            if not selected_mc["value"]:
                return
            # Use the automatically selected newest loader
            selected_loader["value"] = loader_values[0]
            primary_btn.set_sensitive(False)
            primary_btn.set_label(_("Update"))
            cancel_btn.set_label(_("Back"))
            stack.set_visible_child_name("mods")
            clear_review_rows()
            loading_row = Adw.ActionRow(title=_("Checking installed mods and datapacks..."))
            loading_spinner = Gtk.Spinner()
            loading_spinner.start()
            loading_row.add_suffix(loading_spinner)
            add_review_row(loading_row)

            def worker():
                plan = self._server_manager.scan_update_compatibility(
                    self._server_info.id,
                    selected_mc["value"],
                )

                def done():
                    compatibility_plan.clear()
                    compatibility_plan.update(plan)
                    clear_review_rows()
                    compatible = plan.get("compatible", {})
                    incompatible = plan.get("incompatible", {})
                    unknown = plan.get("unknown", {})
                    add_plan_group(
                        _("Compatible and Will Be Updated"),
                        [
                            *compatible.get("modpacks", []),
                            *compatible.get("mods", []),
                            *compatible.get("datapacks", []),
                        ],
                        _("No tracked compatible items found"),
                    )
                    add_plan_group(
                        _("Incompatible and Will Be Disabled"),
                        [
                            *incompatible.get("modpacks", []),
                            *incompatible.get("mods", []),
                            *incompatible.get("datapacks", []),
                        ],
                        _("No incompatible items found"),
                    )
                    unknown_items = [
                        *unknown.get("modpacks", []),
                        *unknown.get("mods", []),
                        *unknown.get("datapacks", []),
                    ]
                    if unknown_items:
                        add_plan_group(_("Could Not Check"), unknown_items, "")
                    primary_btn.set_label(_("Update"))
                    primary_btn.set_sensitive(True)
                    return False

                GLib.idle_add(done)

            threading.Thread(target=worker, daemon=True).start()

        def run_update(*_args):
            mc_version = selected_mc["value"]
            loader_version = selected_loader["value"]
            if not mc_version or not loader_version:
                show_mod_review()
                return
            primary_btn.set_sensitive(False)
            cancel_btn.set_sensitive(False)
            primary_btn.set_label(_("Update"))
            stack.set_visible_child_name("progress")
            progress_spinner.start()
            progress_bar.set_fraction(0.0)
            progress_row.set_title(_("Updating server"))
            progress_row.set_subtitle("")

            def progress(frac, message):
                def update_progress():
                    progress_bar.set_fraction(max(0.0, min(1.0, float(frac))))
                    progress_row.set_subtitle(str(message))
                    return False

                GLib.idle_add(update_progress)

            def worker():
                ok, msg = self._server_manager.update_server_runtime(
                    self._server_info.id,
                    mc_version,
                    loader_version,
                    progress_callback=progress,
                    compatibility_plan=compatibility_plan,
                )

                def done():
                    if ok:
                        self._server_info.mc_version = mc_version
                        self._server_info.loader_version = loader_version
                        try:
                            self._server_info.java_version = get_required_java_version(mc_version)
                        except Exception:
                            self._server_info.java_version = 21
                        self._version_row.set_subtitle(f"{mc_version} ({loader_version})")
                        self._refresh_upgrade_button()
                        self._show_toast(msg, timeout=4)
                        dialog.close()
                    else:
                        cancel_btn.set_sensitive(True)
                        cancel_btn.set_label(_("Back"))
                        primary_btn.set_label(_("Update"))
                        primary_btn.set_sensitive(True)
                        stack.set_visible_child_name("mods")
                        progress_spinner.stop()
                        self._show_toast(msg, timeout=5)
                    return False

                GLib.idle_add(done)

            threading.Thread(target=worker, daemon=True).start()

        def on_primary(*_args):
            if stack.get_visible_child_name() == "runtime":
                show_mod_review()
            else:
                run_update()

        primary_btn.connect("clicked", on_primary)
        threading.Thread(target=versions_worker, daemon=True).start()
        dialog.present(self.get_root())

    def reload_from_disk(self):
        """Reload properties from server.properties on disk."""
        if not self._config:
            return
        self._config.load()
        self._populate()

    def _populate(self):
        """Populate widgets from config."""
        if not self._config:
            return

        self._suppress_changes = True

        if self._ram_row and self._server_info:
            self._ram_row.set_value(float(self._server_info.ram_mb))
        elif self._ram_row:
            self._ram_row.set_value(float(DEFAULT_RAM_MB))

        if hasattr(self, "_autostart_row") and self._server_info:
            self._autostart_row.set_active(getattr(self._server_info, "autostart", False))

        for key, widget in self._widgets.items():
            if isinstance(widget, Adw.EntryRow):
                val = self._config.get(key, "")
                widget.set_text(val)
            elif isinstance(widget, Adw.SpinRow):
                val = self._config.get_int(key, int(widget.get_adjustment().get_value()))
                widget.set_value(val)
            elif isinstance(widget, Adw.SwitchRow):
                val = self._config.get_bool(key, widget.get_active())
                widget.set_active(val)
            elif isinstance(widget, Adw.ComboRow):
                options = widget._options
                if key == "difficulty":
                    # Hardcore mode is represented as a virtual difficulty option in the UI.
                    val = "hardcore" if self._config.get_bool("hardcore", False) else self._config.get("difficulty", "")
                    try:
                        idx = options.index(val)
                        widget.set_selected(idx)
                    except ValueError:
                        widget.set_selected(0)

                else:
                    val = self._config.get(key, "")
                    try:
                        idx = options.index(val)
                        widget.set_selected(idx)
                    except ValueError:
                        widget.set_selected(0)

        self._suppress_changes = False

    def _on_widget_changed(self, *_args):
        if self._suppress_changes:
            return
        self._save_properties()

    def _save_properties(self):
        """Save properties to file."""
        if not self._config:
            return

        for key, widget in self._widgets.items():
            if isinstance(widget, Adw.EntryRow):
                self._config.set_value(key, widget.get_text())
            elif isinstance(widget, Adw.SpinRow):
                self._config.set_value(key, int(widget.get_value()))
            elif isinstance(widget, Adw.SwitchRow):
                self._config.set_value(key, widget.get_active())
            elif isinstance(widget, Adw.ComboRow):
                idx = widget.get_selected()
                options = widget._options
                if key == "difficulty":
                    val = options[idx] if idx < len(options) else options[0]
                    if val == "hardcore":
                        self._config.set_value("difficulty", "hard")
                        self._config.set_value("hardcore", True)
                    else:
                        self._config.set_value("difficulty", val)
                        self._config.set_value("hardcore", False)

                else:
                    val = options[idx] if idx < len(options) else options[0]
                    self._config.set_value(key, val)

        self._config.save()
        running = False
        if self._server_manager and self._server_info and self._ram_row:
            ram_mb = int(self._ram_row.get_value())
            if ram_mb != int(self._server_info.ram_mb):
                self._server_manager.update_server_ram(self._server_info.id, ram_mb)

            process = self._server_manager.get_process(self._server_info.id)
            if process:
                process.set_max_players(self._config.get_int("max-players", 20))
                running = bool(process.is_running)

        if self._server_manager and self._server_info:
            self._server_manager.emit_on_main_thread("server-changed", self._server_info.id)

        self._banner.set_revealed(running)

    def focus_save_button(self):
        """Compatibility no-op after removing explicit save button."""
        return
