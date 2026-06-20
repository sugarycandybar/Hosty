"""
FilesView -- folders, worlds, backups, and Modrinth integration (per selected server).
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, GLib, Gtk

from hosty.shared.backend.server_manager import ServerManager

from ..utils import *


class BackupsMixin:
    def _backups_dir(self) -> Path | None:
        root = self._server_dir()
        if not root:
            return None
        d = root / "hosty-backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _build_subpage_shell(self, title: str, content: Gtk.Widget, show_controls: bool = False) -> Gtk.Widget:
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(show_controls)

        title_lbl = Gtk.Label(label=title)
        title_lbl.add_css_class("heading")
        header.set_title_widget(title_lbl)

        tv.add_top_bar(header)
        tv.set_content(content)
        return tv

    def _push_backups_page(self, *_args) -> None:
        show_fullscreen = self._push_fullscreen_page_cb is not None
        page = Adw.NavigationPage(title=_("Backups"), child=self._build_backups_page(show_controls=show_fullscreen))

        if show_fullscreen:
            self._push_fullscreen_page_cb(page)
        else:
            self._nav.push(page)

    def _build_backups_page(self, show_controls: bool = False) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        actions = Adw.PreferencesGroup(title=_("Actions"))
        create_row = Adw.ActionRow(
            title=_("Create world backup"),
        )
        create_row.add_prefix(Gtk.Image.new_from_icon_name("document-save-symbolic"))
        create_row.set_activatable(True)
        create_row.connect("activated", lambda *_: self._on_create_backup())
        actions.add(create_row)
        self._create_backup_row = create_row

        full_row = Adw.ActionRow(
            title=_("Create full backup"),
            subtitle=_("Back up the entire server folder, including mods and executables"),
        )
        full_row.add_prefix(Gtk.Image.new_from_icon_name("drive-harddisk-symbolic"))
        self._full_backup_spinner = Gtk.Spinner()
        self._full_backup_spinner.set_spinning(False)
        self._full_backup_spinner.set_visible(False)
        full_row.add_suffix(self._full_backup_spinner)
        full_row.set_activatable(True)
        full_row.connect("activated", lambda *_: self._on_create_full_backup())
        actions.add(full_row)
        self._full_backup_row = full_row

        open_row = Adw.ActionRow(title=_("Open backups folder"))
        open_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        open_row.set_activatable(True)
        open_row.connect("activated", self._on_open_backups_folder)
        actions.add(open_row)
        page.add(actions)

        self._backups_group = Adw.PreferencesGroup(title=_("Available Backups"))
        page.add(self._backups_group)
        self._refresh_backup_list()

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(page)
        return self._build_subpage_shell(_("Backups"), sw, show_controls=show_controls)

    def _refresh_backup_list(self) -> None:
        if hasattr(self, "_refresh_backups_row_subtitle"):
            try:
                self._refresh_backups_row_subtitle()
            except Exception:
                pass

        if not self._backups_group:
            return

        self._clear_group_rows(self._backups_group, self._backup_rows)
        bdir = self._backups_dir()
        if not bdir:
            self._backup_rows.append(self._add_info_row(self._backups_group, _("No server selected")))
            return

        zips = sorted(bdir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not zips:
            self._backup_rows.append(self._add_info_row(self._backups_group, _("No backups yet")))
            return

        for zp in zips:
            row = self._make_backup_row(zp)
            self._backups_group.add(row)
            self._backup_rows.append(row)

    def _make_backup_row(self, zp: Path) -> Adw.ActionRow:
        st = zp.stat()
        row = Adw.ActionRow(title=zp.name)

        # Check if it's a full backup and has a version in the filename
        version_str = ""
        if zp.name.startswith("hosty-full-backup-"):
            version = ServerManager.backup_game_version(zp)
            if version:
                version_str = _(" Version {} ·").format(version)

        row.set_subtitle(f"{_format_size(st.st_size)} ·{version_str} {_format_mtime(st.st_mtime)}")
        row.set_activatable(False)

        restore_btn = self._icon_button(
            "document-revert-symbolic",
            _("Restore backup"),
            lambda *_p, p=zp: self._confirm_restore_backup(p),
        )
        delete_btn = self._icon_button(
            "user-trash-symbolic",
            _("Delete backup"),
            lambda *_p, p=zp: self._confirm_delete_backup(p),
            destructive=True,
        )

        row.add_suffix(restore_btn)
        row.add_suffix(delete_btn)
        return row

    def _on_create_backup(self) -> None:
        if self._backup_busy:
            self._alert(_("Backup in progress"), _("Please wait for the current backup task to finish."))
            return
        if self._is_running():
            self._alert(_("Server is running"), _("Stop the server before creating a backup."))
            return

        root = self._server_dir()
        bdir = self._backups_dir()
        if not root or not bdir:
            self._alert(_("No server selected"), _("Select a server to manage backups."))
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        zp = bdir / f"hosty-backup-{stamp}.zip"
        self._backup_busy = True
        if self._create_backup_row:
            self._create_backup_row.set_subtitle(_("Creating backup..."))

        def worker():
            try:
                worlds = _world_dirs(root)
                if not worlds:
                    raise RuntimeError("No world folder found to back up.")

                with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                    for world_dir in worlds:
                        for item in world_dir.rglob("*"):
                            if not item.is_file():
                                continue
                            arc = item.relative_to(root)
                            zf.write(item, arcname=str(arc).replace("\\", "/"))

                def ui_ok():
                    self._backup_busy = False
                    if self._create_backup_row:
                        self._create_backup_row.set_subtitle(_("Back up world folders only"))
                    self._refresh_backup_list()
                    self._toast(_("Saved {}").format(zp.name))

                GLib.idle_add(ui_ok)
            except Exception as e:
                err_msg = str(e)

                def ui_err(msg: str = err_msg):
                    self._backup_busy = False
                    if self._create_backup_row:
                        self._create_backup_row.set_subtitle(_("Back up world folders only"))
                    self._alert(_("Backup failed"), msg)

                GLib.idle_add(ui_err)

        threading.Thread(target=worker, daemon=True).start()

    def _on_create_full_backup(self) -> None:
        if self._backup_busy:
            self._alert(_("Backup in progress"), _("Please wait for the current backup task to finish."))
            return
        if self._is_running():
            self._alert(_("Server is running"), _("Stop the server before creating a full backup."))
            return
        if not self._server_info or not self._server_manager:
            self._alert(_("No server selected"), _("Select a server to manage backups."))
            return

        self._backup_busy = True
        if self._full_backup_spinner:
            self._full_backup_spinner.set_visible(True)
            self._full_backup_spinner.start()
        if self._full_backup_row:
            self._full_backup_row.set_subtitle(_("Creating full backup..."))

        server_id = self._server_info.id

        def worker():
            ok, msg = self._server_manager.create_full_backup(server_id)

            def done():
                self._backup_busy = False
                if self._full_backup_spinner:
                    self._full_backup_spinner.stop()
                    self._full_backup_spinner.set_visible(False)
                if self._full_backup_row:
                    self._full_backup_row.set_subtitle(
                        _("Back up the entire server folder, including mods and executables")
                    )
                self._refresh_backup_list()
                if ok:
                    self._toast(_("Saved {}").format(msg))
                else:
                    self._alert(_("Full backup failed"), msg)
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_restore_backup(self, zp: Path) -> None:
        if self._is_running():
            self._alert(_("Server is running"), _("Stop the server before restoring a backup."))
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Restore backup?"))

        is_full = zp.name.startswith("hosty-full-backup-")
        body_text = _("Restore “{}”?\n\n").format(zp.name)
        if is_full:
            body_text += _(
                "WARNING: This is a full backup. Restoring it will"
                " completely replace ALL server configuration, mods, and world files."
            )
            backup_version = ServerManager.backup_game_version(zp)
            current_version = self._server_info.mc_version if self._server_info else ""
            if backup_version and current_version and ServerManager.is_version_older(backup_version, current_version):
                body_text += _(
                    "\n\nDowngrade warning: this backup is for Minecraft {backup_version}, "
                    "but this server is currently on Minecraft {current_version}."
                ).format(backup_version=backup_version, current_version=current_version)
        else:
            body_text += _("This replaces only world folders contained in the backup.")

        dialog.set_body(body_text)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
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
            self._alert(_("Backup task active"), _("Wait for the active backup task to finish."))
            return

        root = self._server_dir()
        bdir = self._backups_dir()
        if not root or not bdir:
            self._alert(_("No server selected"), _("Select a server before restoring a backup."))
            return

        self._backup_busy = True

        def worker():
            try:
                with tempfile.TemporaryDirectory(prefix="hosty-restore-") as td:
                    tmp_root = Path(td).resolve()
                    with zipfile.ZipFile(zp, "r") as zf:
                        for info in zf.infolist():
                            candidate = (tmp_root / info.filename).resolve()
                            if hasattr(candidate, "is_relative_to") and not candidate.is_relative_to(tmp_root):
                                raise RuntimeError("Backup archive contains invalid paths.")
                            elif not str(candidate).startswith(str(tmp_root)):
                                raise RuntimeError("Backup archive contains invalid paths.")
                        zf.extractall(tmp_root)

                    is_full = zp.name.startswith("hosty-full-backup-")
                    if is_full:
                        # Nuke everything in root except hosty-backups, then copy all
                        for item in root.iterdir():
                            if item.name == "hosty-backups":
                                continue
                            if item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)
                            else:
                                item.unlink(missing_ok=True)

                        for item in tmp_root.iterdir():
                            dst = root / item.name
                            if item.is_dir():
                                shutil.copytree(item, dst, dirs_exist_ok=True)
                            else:
                                shutil.copy2(item, dst)
                    else:
                        extracted_worlds = _world_dirs(tmp_root)
                        if not extracted_worlds:
                            raise RuntimeError("This backup does not contain any world data.")

                        level_name = "world"
                        for item in root.iterdir():
                            if not item.is_dir():
                                continue
                            if (
                                (item / "level.dat").exists()
                                or item.name.casefold() == level_name.casefold()
                                or any(
                                    (item / marker).exists()
                                    for marker in (
                                        "region",
                                        "data",
                                        "playerdata",
                                        "poi",
                                        "entities",
                                        "stats",
                                        "advancements",
                                        "dimensions",
                                        "DIM-1",
                                        "DIM1",
                                        "session.lock",
                                        "uid.dat",
                                    )
                                )
                            ):
                                shutil.rmtree(item, ignore_errors=True)

                        for item in extracted_worlds:
                            dst = root / "world"
                            if dst.is_dir():
                                shutil.rmtree(dst, ignore_errors=True)
                            shutil.copytree(item, dst, dirs_exist_ok=True)

                def ui_ok():
                    self._backup_busy = False
                    self._rebuild_lists()
                    self._refresh_backup_list()
                    self._toast(_("Backup restored"))

                GLib.idle_add(ui_ok)
            except Exception as e:
                err_msg = str(e)

                def ui_err(msg: str = err_msg):
                    self._backup_busy = False
                    self._alert(_("Restore failed"), msg)

                GLib.idle_add(ui_err)

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_delete_backup(self, zp: Path) -> None:
        self._soft_delete_with_undo(
            zp,
            _('backup "{}"').format(zp.name),
            on_refresh=self._refresh_backup_list,
        )

    def _on_open_backups_folder(self, *_):
        bdir = self._backups_dir()
        if not bdir:
            self._alert(_("No server selected"), _("Select a server to open backups."))
            return
        self._open_target(bdir)
