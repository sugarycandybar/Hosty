"""
FilesView — folders, worlds, backups, and Modrinth integration (per selected server).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, Gdk, GdkPixbuf

from hosty.backend.server_manager import ServerManager, ServerInfo


def _open_uri(uri: str) -> bool:
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
        return True
    except Exception:
        pass

    try:
        if webbrowser.open(uri):
            return True
    except Exception:
        pass

    try:
        cmd = ["open", uri] if sys.platform == "darwin" else ["xdg-open", uri]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


def _open_path(path: Path) -> bool:
    p = path.resolve()
    target = p.parent if p.is_file() else p

    if sys.platform == "win32":
        try:
            os.startfile(str(target))
            return True
        except Exception:
            pass

    return _open_uri(target.as_uri())


def _world_dirs(server_root: Path) -> list[Path]:
    out = []
    if not server_root.is_dir():
        return out
    for item in server_root.iterdir():
        if item.is_dir() and (item / "level.dat").exists():
            out.append(item)
    return sorted(out, key=lambda p: p.name.lower())


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    val = float(size)
    for u in units:
        if val < 1024.0 or u == units[-1]:
            if u == "B":
                return f"{int(val)} {u}"
            return f"{val:.1f} {u}"
        val /= 1024.0
    return f"{int(size)} B"


def _format_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _format_compact_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


class FilesView(Gtk.Box):
    """Browse files for the currently selected server only."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._server_info: Optional[ServerInfo] = None
        self._server_manager: Optional[ServerManager] = None
        self._root_page: Optional[Adw.NavigationPage] = None

        self._folders_group: Optional[Adw.PreferencesGroup] = None
        self._worlds_group: Optional[Adw.PreferencesGroup] = None
        self._mods_group: Optional[Adw.PreferencesGroup] = None
        self._world_rows: list[Gtk.Widget] = []
        self._mod_rows: list[Gtk.Widget] = []

        self._backups_group: Optional[Adw.PreferencesGroup] = None
        self._backup_rows: list[Gtk.Widget] = []
        self._backup_busy = False

        self._nav = Adw.NavigationView()
        self._nav.set_vexpand(True)
        self.append(self._nav)

        root_content = self._build_root_content()
        self._root_page = Adw.NavigationPage(title="Files", child=root_content)
        try:
            self._root_page.set_tag("hosty-files-root")
        except Exception:
            pass
        self._nav.push(self._root_page)

    def _build_root_content(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        page = Adw.PreferencesPage()

        self._folders_group = Adw.PreferencesGroup(title="Folders")

        open_server_row = Adw.ActionRow(title="Open server folder")
        open_server_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        open_server_row.set_activatable(True)
        open_server_row.connect("activated", self._on_open_server_folder)
        self._folders_group.add(open_server_row)

        open_mods_row = Adw.ActionRow(title="Open mods folder")
        open_mods_row.add_prefix(Gtk.Image.new_from_icon_name("application-x-addon-symbolic"))
        open_mods_row.set_activatable(True)
        open_mods_row.connect("activated", self._on_open_mods_folder)
        self._folders_group.add(open_mods_row)

        page.add(self._folders_group)

        self._worlds_group = Adw.PreferencesGroup(title="Worlds")
        backups_row = Adw.ActionRow(
            title="Backups",
            subtitle="Create, restore, and manage backup archives",
        )
        backups_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        backups_row.set_activatable(True)
        backups_row.connect("activated", self._push_backups_page)
        self._worlds_group.add(backups_row)
        page.add(self._worlds_group)

        self._mods_group = Adw.PreferencesGroup(title="Mods")
        modrinth_row = Adw.ActionRow(
            title="Modrinth",
            subtitle="Discover and install compatible mods",
        )
        modrinth_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        modrinth_row.set_activatable(True)
        modrinth_row.connect("activated", self._push_modrinth_page)
        self._mods_group.add(modrinth_row)
        page.add(self._mods_group)

        scroll.set_child(page)
        return scroll

    def set_server(self, server_info: ServerInfo, server_manager: ServerManager):
        self._pop_to_root()
        self._server_info = server_info
        self._server_manager = server_manager
        self._rebuild_lists()

    def _pop_to_root(self) -> None:
        if not self._root_page:
            return
        try:
            self._nav.pop_to_tag("hosty-files-root")
        except Exception:
            for _ in range(24):
                if self._nav.get_visible_page() == self._root_page:
                    break
                self._nav.pop()

    def _server_dir(self) -> Optional[Path]:
        if not self._server_info:
            return None
        return Path(self._server_info.server_dir)

    def _process(self):
        if not self._server_info or not self._server_manager:
            return None
        return self._server_manager.get_process(self._server_info.id)

    def _is_running(self) -> bool:
        p = self._process()
        return p is not None and p.is_running

    def _clear_group_rows(self, group: Adw.PreferencesGroup, rows: list[Gtk.Widget]) -> None:
        for row in rows:
            try:
                group.remove(row)
            except Exception:
                pass
        rows.clear()

    def _rebuild_lists(self) -> None:
        if not self._worlds_group or not self._mods_group:
            return

        self._clear_group_rows(self._worlds_group, self._world_rows)
        self._clear_group_rows(self._mods_group, self._mod_rows)

        root = self._server_dir()
        if not root or not root.is_dir():
            self._world_rows.append(self._add_info_row(self._worlds_group, "No server folder"))
            self._mod_rows.append(self._add_info_row(self._mods_group, "No server folder"))
            return

        worlds = _world_dirs(root)
        if not worlds:
            self._world_rows.append(self._add_info_row(self._worlds_group, "No worlds yet"))
        else:
            for w in worlds:
                row = self._make_world_row(w)
                self._worlds_group.add(row)
                self._world_rows.append(row)

        mods_dir = root / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        jars = sorted(mods_dir.glob("*.jar"), key=lambda p: p.name.lower())
        if not jars:
            self._mod_rows.append(self._add_info_row(self._mods_group, "No mods installed"))
        else:
            for jar in jars:
                row = self._make_mod_row(jar)
                self._mods_group.add(row)
                self._mod_rows.append(row)

    def _add_info_row(self, group: Adw.PreferencesGroup, title: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title)
        row.set_activatable(False)
        group.add(row)
        return row

    def _icon_button(
        self,
        icon_name: str,
        tooltip: str,
        handler,
        destructive: bool = False,
    ) -> Gtk.Button:
        b = Gtk.Button(icon_name=icon_name)
        b.add_css_class("flat")
        if destructive:
            b.add_css_class("destructive-action")
        b.set_tooltip_text(tooltip)
        b.connect("clicked", handler)
        return b

    def _make_world_row(self, path: Path) -> Adw.ActionRow:
        row = Adw.ActionRow(title=path.name)
        row.set_activatable(False)
        open_btn = self._icon_button(
            "folder-open-symbolic",
            "Open world folder",
            lambda *_p, p=path: self._open_target(p),
        )
        del_btn = self._icon_button(
            "user-trash-symbolic",
            "Delete world",
            lambda *_p, p=path, n=path.name: self._confirm_delete_world(p, n),
            destructive=True,
        )
        row.add_suffix(open_btn)
        row.add_suffix(del_btn)
        return row

    def _make_mod_row(self, jar: Path) -> Adw.ActionRow:
        row = Adw.ActionRow(title=jar.name)
        row.set_subtitle(_format_size(jar.stat().st_size))
        row.set_activatable(False)
        open_btn = self._icon_button(
            "document-open-symbolic",
            "Open mod location",
            lambda *_p, p=jar: self._open_target(p),
        )
        del_btn = self._icon_button(
            "user-trash-symbolic",
            "Delete mod",
            lambda *_p, p=jar, n=jar.name: self._confirm_delete_mod(p, n),
            destructive=True,
        )
        row.add_suffix(open_btn)
        row.add_suffix(del_btn)
        return row

    def _backups_dir(self) -> Optional[Path]:
        root = self._server_dir()
        if not root:
            return None
        d = root / "hosty-backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _build_subpage_shell(self, title: str, content: Gtk.Widget) -> Gtk.Widget:
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(False)

        title_lbl = Gtk.Label(label=title)
        title_lbl.add_css_class("heading")
        header.set_title_widget(title_lbl)

        tv.add_top_bar(header)
        tv.set_content(content)
        return tv

    def _push_backups_page(self, *_args) -> None:
        page = Adw.NavigationPage(title="Backups", child=self._build_backups_page())
        self._nav.push(page)

    def _build_backups_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        actions = Adw.PreferencesGroup(title="Actions")
        create_row = Adw.ActionRow(
            title="Create backup now",
            subtitle="Save server files, worlds, configs, and mods",
        )
        create_row.add_prefix(Gtk.Image.new_from_icon_name("document-save-symbolic"))
        create_row.set_activatable(True)
        create_row.connect("activated", lambda *_: self._on_create_backup())
        actions.add(create_row)

        open_row = Adw.ActionRow(title="Open backups folder")
        open_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        open_row.set_activatable(True)
        open_row.connect("activated", self._on_open_backups_folder)
        actions.add(open_row)
        page.add(actions)

        self._backups_group = Adw.PreferencesGroup(title="Available Backups")
        page.add(self._backups_group)
        self._refresh_backup_list()

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(page)
        return self._build_subpage_shell("Backups", sw)

    def _refresh_backup_list(self) -> None:
        if not self._backups_group:
            return

        self._clear_group_rows(self._backups_group, self._backup_rows)
        bdir = self._backups_dir()
        if not bdir:
            self._backup_rows.append(self._add_info_row(self._backups_group, "No server selected"))
            return

        zips = sorted(bdir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not zips:
            self._backup_rows.append(self._add_info_row(self._backups_group, "No backups yet"))
            return

        for zp in zips:
            row = self._make_backup_row(zp)
            self._backups_group.add(row)
            self._backup_rows.append(row)

    def _make_backup_row(self, zp: Path) -> Adw.ActionRow:
        st = zp.stat()
        row = Adw.ActionRow(title=zp.name)
        row.set_subtitle(f"{_format_size(st.st_size)} · {_format_mtime(st.st_mtime)}")
        row.set_activatable(False)

        restore_btn = self._icon_button(
            "document-revert-symbolic",
            "Restore backup",
            lambda *_p, p=zp: self._confirm_restore_backup(p),
        )
        open_btn = self._icon_button(
            "folder-open-symbolic",
            "Open backups folder",
            lambda *_p: self._open_target(zp.parent),
        )
        delete_btn = self._icon_button(
            "user-trash-symbolic",
            "Delete backup",
            lambda *_p, p=zp: self._confirm_delete_backup(p),
            destructive=True,
        )

        row.add_suffix(restore_btn)
        row.add_suffix(open_btn)
        row.add_suffix(delete_btn)
        return row

    def _on_create_backup(self) -> None:
        if self._backup_busy:
            self._alert("Backup in progress", "Please wait for the current backup task to finish.")
            return
        if self._is_running():
            self._alert("Server is running", "Stop the server before creating a backup.")
            return

        root = self._server_dir()
        bdir = self._backups_dir()
        if not root or not bdir:
            self._alert("No server selected", "Select a server to manage backups.")
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        zp = bdir / f"hosty-backup-{stamp}.zip"
        self._backup_busy = True

        def worker():
            try:
                with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                    for item in root.rglob("*"):
                        if not item.is_file():
                            continue
                        if _is_relative_to(item.resolve(), bdir.resolve()):
                            continue
                        arc = item.relative_to(root)
                        zf.write(item, arcname=str(arc).replace("\\", "/"))

                def ui_ok():
                    self._backup_busy = False
                    self._refresh_backup_list()
                    self._toast(f"Saved {zp.name}")

                GLib.idle_add(ui_ok)
            except Exception as e:
                def ui_err():
                    self._backup_busy = False
                    self._alert("Backup failed", str(e))

                GLib.idle_add(ui_err)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_restore_backup(self, zp: Path) -> None:
        if self._is_running():
            self._alert("Server is running", "Stop the server before restoring a backup.")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Restore backup?")
        dialog.set_body(
            f"Restore “{zp.name}”?\n\n"
            "Current server files will be replaced (backup archives are kept)."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "restore":
                self._restore_backup(zp)

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _restore_backup(self, zp: Path) -> None:
        if self._backup_busy:
            self._alert("Backup task active", "Wait for the active backup task to finish.")
            return

        root = self._server_dir()
        bdir = self._backups_dir()
        if not root or not bdir:
            self._alert("No server selected", "Select a server before restoring a backup.")
            return

        self._backup_busy = True

        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="hosty-restore-") as td:
                    tmp_root = Path(td).resolve()
                    with zipfile.ZipFile(zp, "r") as zf:
                        for info in zf.infolist():
                            candidate = (tmp_root / info.filename).resolve()
                            if not _is_relative_to(candidate, tmp_root):
                                raise RuntimeError("Backup archive contains invalid paths.")
                        zf.extractall(tmp_root)

                    for item in root.iterdir():
                        if item.resolve() == bdir.resolve():
                            continue
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)

                    for item in tmp_root.iterdir():
                        if item.name == "hosty-backups":
                            continue
                        dst = root / item.name
                        if item.is_dir():
                            shutil.copytree(item, dst, dirs_exist_ok=True)
                        else:
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(item, dst)

                def ui_ok():
                    self._backup_busy = False
                    self._rebuild_lists()
                    self._refresh_backup_list()
                    self._toast("Backup restored")

                GLib.idle_add(ui_ok)
            except Exception as e:
                def ui_err():
                    self._backup_busy = False
                    self._alert("Restore failed", str(e))

                GLib.idle_add(ui_err)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_delete_backup(self, zp: Path) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete backup?")
        dialog.set_body(f"Remove “{zp.name}”?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                try:
                    zp.unlink(missing_ok=True)
                    self._refresh_backup_list()
                    self._toast("Backup deleted")
                except OSError as e:
                    self._alert("Could not delete", str(e))

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _push_modrinth_page(self, *_args) -> None:
        page = Adw.NavigationPage(title="Modrinth", child=self._build_modrinth_page())
        self._nav.push(page)

    def _build_modrinth_page(self) -> Gtk.Widget:
        from hosty.backend import modrinth_client

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(False)

        search_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_header.set_hexpand(True)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_placeholder_text("Search Fabric mods…")
        btn = Gtk.Button(label="Search")
        btn.add_css_class("suggested-action")
        search_header.append(entry)
        search_header.append(btn)
        header.set_title_widget(search_header)
        tv.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_start(18)
        outer.set_margin_end(18)
        outer.set_margin_top(12)
        outer.set_margin_bottom(18)

        mc_ver = self._server_info.mc_version if self._server_info else ""

        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        category_items = [
            ("Any category", ""),
            ("Optimization", "optimization"),
            ("Utility", "utility"),
            ("Technology", "technology"),
            ("Adventure", "adventure"),
            ("Decoration", "decoration"),
            ("Magic", "magic"),
            ("Storage", "storage"),
            ("Worldgen", "worldgen"),
            ("Library", "library"),
        ]
        cat_dd = Gtk.DropDown.new_from_strings([x[0] for x in category_items])
        cat_dd.set_valign(Gtk.Align.CENTER)
        filter_row.append(cat_dd)

        sort_items = [
            ("Relevance", "relevance"),
            ("Downloads", "downloads"),
            ("Follows", "follows"),
            ("Newest", "newest"),
            ("Recently updated", "updated"),
        ]
        sort_dd = Gtk.DropDown.new_from_strings([x[0] for x in sort_items])
        sort_dd.set_valign(Gtk.Align.CENTER)
        filter_row.append(sort_dd)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        filter_row.append(spacer)

        current_only = Gtk.CheckButton(label="Only current Minecraft version")
        if mc_ver:
            current_only.set_active(True)
        else:
            current_only.set_sensitive(False)
            current_only.set_active(False)
        current_only.set_halign(Gtk.Align.END)
        filter_row.append(current_only)
        outer.append(filter_row)

        results = Gtk.ListBox()
        results.set_selection_mode(Gtk.SelectionMode.NONE)
        results.add_css_class("boxed-list")
        results.set_vexpand(True)

        pager = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        prev_btn.add_css_class("flat")
        next_btn = Gtk.Button(icon_name="go-next-symbolic")
        next_btn.add_css_class("flat")
        page_label = Gtk.Label(label="Page 1/1", xalign=0.0)
        page_label.add_css_class("dim-label")
        results_label = Gtk.Label(label="", xalign=1.0)
        results_label.add_css_class("dim-label")
        results_label.set_hexpand(True)
        results_label.set_halign(Gtk.Align.END)
        pager.append(prev_btn)
        pager.append(next_btn)
        pager.append(page_label)
        pager.append(results_label)
        outer.append(pager)

        page_size = 10
        state = {"offset": 0, "total": 0, "busy": False}

        def selected_category() -> str:
            idx = int(cat_dd.get_selected())
            if idx < 0 or idx >= len(category_items):
                return ""
            return category_items[idx][1]

        def selected_sort() -> str:
            idx = int(sort_dd.get_selected())
            if idx < 0 or idx >= len(sort_items):
                return "relevance"
            return sort_items[idx][1]

        def update_pager():
            total = max(0, int(state["total"]))
            page = (state["offset"] // page_size) + 1
            max_page = max(1, (total + page_size - 1) // page_size)
            page_label.set_label(f"Page {page}/{max_page}")
            prev_btn.set_sensitive((not state["busy"]) and state["offset"] > 0)
            next_btn.set_sensitive(
                (not state["busy"]) and (state["offset"] + page_size < total)
            )

        def set_busy(busy: bool):
            state["busy"] = busy
            entry.set_sensitive(not busy)
            btn.set_sensitive(not busy)
            cat_dd.set_sensitive(not busy)
            sort_dd.set_sensitive(not busy)
            current_only.set_sensitive((not busy) and bool(self._server_info and self._server_info.mc_version))
            update_pager()

        def clear_results():
            while True:
                r = results.get_row_at_index(0)
                if r is None:
                    break
                results.remove(r)

        def installed_mod_names() -> set[str]:
            root = self._server_dir()
            if not root:
                return set()
            mods_dir = root / "mods"
            if not mods_dir.is_dir():
                return set()
            return {p.name.lower() for p in mods_dir.glob("*.jar")}

        def finish_search(hits, total, err, version, qtxt):
            set_busy(False)
            if err:
                results_label.set_label("Search failed")
                results.append(self._empty_listbox_row(err))
                return
            state["total"] = int(total)
            update_pager()
            results_label.set_label(f"{state['total']:,} results")
            if not hits:
                results.append(self._empty_listbox_row("No results"))
                return

            installed = installed_mod_names()
            for h in hits:
                results.append(self._make_modrinth_row(h, version, installed))

        def do_search(reset: bool = False):
            if reset:
                state["offset"] = 0
            clear_results()
            q = entry.get_text().strip()
            mc_version = self._server_info.mc_version if self._server_info else ""
            use_version = current_only.get_active() and bool(mc_version)
            qtxt = q
            results_label.set_label("Searching…")
            set_busy(True)
            offset = int(state["offset"])
            category = selected_category() or None
            sort_key = selected_sort()

            def thread_fn():
                try:
                    hits, total = modrinth_client.search_mods(
                        qtxt,
                        limit=page_size,
                        offset=offset,
                        sort=sort_key,
                        game_version=(mc_version if use_version else None),
                        category=category,
                        loader="fabric",
                        server_side_only=True,
                    )
                    GLib.idle_add(
                        lambda h=hits, t=total, v=mc_version, qq=qtxt: finish_search(
                            h, t, None, v, qq
                        )
                    )
                except Exception as ex:
                    GLib.idle_add(
                        lambda e=str(ex), v=mc_version, qq=qtxt: finish_search(
                            [], 0, e, v, qq
                        )
                    )

            threading.Thread(target=thread_fn, daemon=True).start()

        def on_prev(*_):
            if state["offset"] >= page_size:
                state["offset"] -= page_size
                do_search(reset=False)

        def on_next(*_):
            if state["offset"] + page_size < state["total"]:
                state["offset"] += page_size
                do_search(reset=False)

        btn.connect("clicked", lambda *_: do_search(reset=True))
        entry.connect("activate", lambda *_: do_search(reset=True))
        prev_btn.connect("clicked", on_prev)
        next_btn.connect("clicked", on_next)
        current_only.connect("toggled", lambda *_: do_search(reset=True))
        cat_dd.connect("notify::selected", lambda *_: do_search(reset=True))
        sort_dd.connect("notify::selected", lambda *_: do_search(reset=True))

        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(results)
        outer.append(sw)

        # Run initial discovery search when opening the page.
        update_pager()
        GLib.idle_add(lambda: do_search(reset=True) or False)
        tv.set_content(outer)
        return tv

    def _empty_listbox_row(self, title: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        label = Gtk.Label(label=title, xalign=0.0)
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

    def _load_icon_async(self, image: Gtk.Image, url: str) -> None:
        def worker():
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Hosty/1.0 (+https://github.com/hosty)"},
                )
                with urllib.request.urlopen(req, timeout=20.0) as resp:
                    data = resp.read()
                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if not pixbuf:
                    return
                scaled = pixbuf.scale_simple(44, 44, GdkPixbuf.InterpType.BILINEAR) or pixbuf
                texture = Gdk.Texture.new_for_pixbuf(scaled)

                def ui_set():
                    image.set_from_paintable(texture)

                GLib.idle_add(ui_set)
            except Exception:
                return

        threading.Thread(target=worker, daemon=True).start()

    def _make_modrinth_row(self, hit, mc_version: str, installed_names: set[str]) -> Gtk.ListBoxRow:
        from hosty.backend import modrinth_client

        row = Gtk.ListBoxRow()
        row.set_activatable(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        outer.set_margin_start(12)
        outer.set_margin_end(12)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)

        icon = Gtk.Image.new_from_icon_name("application-x-addon-symbolic")
        icon.set_pixel_size(44)
        outer.append(icon)
        if hit.icon_url:
            self._load_icon_async(icon, hit.icon_url)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.set_hexpand(True)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=hit.title, xalign=0.0)
        title.add_css_class("heading")
        title.set_wrap(True)
        title.set_hexpand(True)
        title_row.append(title)

        author = Gtk.Label(label=(hit.author or "Unknown"), xalign=1.0)
        author.add_css_class("caption")
        author.add_css_class("dim-label")
        author.set_halign(Gtk.Align.END)
        title_row.append(author)
        content.append(title_row)

        desc_text = (hit.description or "No description available.").strip()
        desc = Gtk.Label(label=desc_text, xalign=0.0)
        desc.set_wrap(True)
        desc.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        desc.set_lines(1)
        desc.set_ellipsize(Pango.EllipsizeMode.END)
        desc.add_css_class("dim-label")
        content.append(desc)

        def chip(text: str, icon_name: str) -> Gtk.Button:
            b = Gtk.Button()
            b.add_css_class("flat")
            b.add_css_class("pill")
            b.set_sensitive(False)
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            ic = Gtk.Image.new_from_icon_name(icon_name)
            ic.set_pixel_size(14)
            hb.append(ic)
            lab = Gtk.Label(label=text)
            lab.add_css_class("caption")
            hb.append(lab)
            b.set_child(hb)
            return b

        category_icons = {
            "optimization": "system-run-symbolic",
            "utility": "applications-utilities-symbolic",
            "technology": "applications-development-symbolic",
            "adventure": "compass-symbolic",
            "decoration": "applications-graphics-symbolic",
            "magic": "starred-symbolic",
            "storage": "drive-harddisk-symbolic",
            "worldgen": "map-symbolic",
            "library": "code-context-symbolic",
            "fabric": "applications-development-symbolic",
        }

        chip_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chip_row.append(chip(f"{_format_compact_count(int(hit.downloads or 0))} downloads", "folder-download-symbolic"))
        for c in hit.categories[:3]:
            key = str(c).strip().lower()
            chip_row.append(chip(str(c), category_icons.get(key, "tag-symbolic")))
        content.append(chip_row)

        version_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        version_label = Gtk.Label(label="Version", xalign=0.0)
        version_label.add_css_class("dim-label")
        version_row.append(version_label)
        version_dd = Gtk.DropDown.new_from_strings(["Checking versions…"])
        version_dd.set_hexpand(True)
        version_row.append(version_dd)
        content.append(version_row)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        install_btn = Gtk.Button(label="Install")
        install_btn.add_css_class("suggested-action")
        install_btn.set_halign(Gtk.Align.START)
        if self._looks_installed(hit, installed_names):
            install_btn.set_label("Installed")
            install_btn.set_sensitive(False)
        btns.append(install_btn)

        open_btn = Gtk.Button(label="Open page")
        open_btn.add_css_class("flat")
        btns.append(open_btn)
        content.append(btns)

        outer.append(content)
        row.set_child(outer)

        version_objs = []

        def on_open_page(*_):
            slug = hit.slug or hit.project_id
            if not _open_uri(f"https://modrinth.com/mod/{slug}"):
                self._alert("Could not open browser", "Unable to open the Modrinth page.")

        def selected_version():
            if not version_objs:
                return None
            idx = int(version_dd.get_selected())
            if idx < 0 or idx >= len(version_objs):
                return None
            return version_objs[idx]

        def on_install(*_b):
            if self._is_running():
                self._alert("Server is running", "Stop the server before installing mods.")
                return
            if not mc_version:
                self._alert("Unknown version", "Could not read Minecraft version for this server.")
                return

            chosen = selected_version()
            if not chosen:
                self._alert("No compatible version", "No compatible server mod version is available.")
                return

            install_btn.set_sensitive(False)

            def ui_no_file():
                install_btn.set_sensitive(True)
                self._alert(
                    "No compatible version",
                    "No Fabric .jar was found for this Minecraft version.",
                )

            def ui_ok(fname: str):
                install_btn.set_label("Installed")
                install_btn.set_sensitive(False)
                self._toast(f"Installed {fname}")
                self._rebuild_lists()

            def ui_err(msg: str):
                install_btn.set_sensitive(True)
                self._alert("Install failed", msg)

            def thread_fn():
                try:
                    root = self._server_dir()
                    if not root:
                        raise RuntimeError("No server selected.")
                    dest = root / "mods" / chosen.filename
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    modrinth_client.download_to(chosen.download_url, dest)
                    GLib.idle_add(lambda f=chosen.filename: ui_ok(f))
                except Exception as e:
                    GLib.idle_add(lambda m=str(e): ui_err(m))

            threading.Thread(target=thread_fn, daemon=True).start()

        def load_versions():
            if not mc_version:
                GLib.idle_add(lambda: version_dd.set_model(Gtk.StringList.new(["No server version"])))
                return
            try:
                versions = modrinth_client.find_compatible_versions(
                    hit.project_id,
                    mc_version,
                    loader="fabric",
                    limit=5,
                )
                if not versions:
                    GLib.idle_add(
                        lambda: version_dd.set_model(Gtk.StringList.new(["No compatible versions"]))
                    )
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

                if not names:
                    GLib.idle_add(
                        lambda: version_dd.set_model(Gtk.StringList.new(["No compatible versions"]))
                    )
                    return

                version_objs.clear()
                version_objs.extend(chosen_for_labels)

                def ui_set_versions():
                    version_dd.set_model(Gtk.StringList.new(names))
                    version_dd.set_selected(0)

                GLib.idle_add(ui_set_versions)

                first = version_objs[0]
                if first.filename.lower() in installed_names:
                    GLib.idle_add(lambda: install_btn.set_label("Installed"))
                    GLib.idle_add(lambda: install_btn.set_sensitive(False))
            except Exception as e:
                GLib.idle_add(
                    lambda m=str(e): version_dd.set_model(
                        Gtk.StringList.new([f"Version lookup failed: {m}"])
                    )
                )

        open_btn.connect("clicked", on_open_page)
        install_btn.connect("clicked", on_install)
        threading.Thread(target=load_versions, daemon=True).start()
        return row

    def _on_open_server_folder(self, *_):
        root = self._server_dir()
        if root:
            self._open_target(root)

    def _on_open_mods_folder(self, *_):
        root = self._server_dir()
        if root:
            d = root / "mods"
            d.mkdir(parents=True, exist_ok=True)
            self._open_target(d)

    def _on_open_backups_folder(self, *_):
        bdir = self._backups_dir()
        if not bdir:
            self._alert("No server selected", "Select a server to open backups.")
            return
        self._open_target(bdir)

    def _open_target(self, path: Path):
        if not _open_path(path):
            self._alert("Could not open path", str(path))

    def _confirm_delete_world(self, path: Path, name: str):
        if self._is_running():
            self._alert("Server is running", "Stop the server before deleting a world.")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete world?")
        dialog.set_body(f"Permanently delete “{name}” and all of its contents?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                try:
                    shutil.rmtree(path, ignore_errors=False)
                    self._rebuild_lists()
                    self._toast(f"Deleted “{name}”")
                except OSError as e:
                    self._alert("Could not delete", str(e))

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _confirm_delete_mod(self, path: Path, name: str):
        if self._is_running():
            self._alert("Server is running", "Stop the server before removing mods.")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete mod?")
        dialog.set_body(f"Remove “{name}”?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                try:
                    path.unlink(missing_ok=True)
                    self._rebuild_lists()
                    self._toast(f"Removed “{name}”")
                except OSError as e:
                    self._alert("Could not delete", str(e))

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _alert(self, title: str, body: str):
        d = Adw.AlertDialog()
        d.set_heading(title)
        d.set_body(body)
        d.add_response("ok", "OK")
        d.present(self.get_root())

    def _toast(self, message: str):
        root = self.get_root()
        if root and hasattr(root, "show_toast"):
            root.show_toast(message)
