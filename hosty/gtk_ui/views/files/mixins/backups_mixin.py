"""
FilesView — folders, worlds, backups, and Modrinth integration (per selected server).
"""
from __future__ import annotations

import json
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import uuid

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, Gdk, GdkPixbuf

from hosty.shared.backend.server_manager import ServerManager, ServerInfo



from ..utils import *

class BackupsMixin:
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
            subtitle="Back up world folders only",
        )
        create_row.add_prefix(Gtk.Image.new_from_icon_name("document-save-symbolic"))
        self._backup_spinner = Gtk.Spinner()
        self._backup_spinner.set_spinning(False)
        self._backup_spinner.set_visible(False)
        create_row.add_suffix(self._backup_spinner)
        create_row.set_activatable(True)
        create_row.connect("activated", lambda *_: self._on_create_backup())
        actions.add(create_row)
        self._create_backup_row = create_row

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
        delete_btn = self._icon_button(
            "user-trash-symbolic",
            "Delete backup",
            lambda *_p, p=zp: self._confirm_delete_backup(p),
            destructive=True,
        )

        row.add_suffix(restore_btn)
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
        if self._backup_spinner:
            self._backup_spinner.set_visible(True)
            self._backup_spinner.start()
        if self._create_backup_row:
            self._create_backup_row.set_subtitle("Creating backup...")

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
                    if self._backup_spinner:
                        self._backup_spinner.stop()
                        self._backup_spinner.set_visible(False)
                    if self._create_backup_row:
                        self._create_backup_row.set_subtitle("Back up world folders only")
                    self._refresh_backup_list()
                    self._toast(f"Saved {zp.name}")

                GLib.idle_add(ui_ok)
            except Exception as e:
                def ui_err():
                    self._backup_busy = False
                    if self._backup_spinner:
                        self._backup_spinner.stop()
                        self._backup_spinner.set_visible(False)
                    if self._create_backup_row:
                        self._create_backup_row.set_subtitle("Back up world folders only")
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
            "This replaces only world folders contained in the backup."
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

                    extracted_worlds = [
                        item for item in tmp_root.iterdir()
                        if item.is_dir() and (item / "level.dat").exists()
                    ]
                    if not extracted_worlds:
                        raise RuntimeError("This backup does not contain any world data.")

                    for item in extracted_worlds:
                        dst = root / item.name
                        if dst.is_dir():
                            shutil.rmtree(dst, ignore_errors=True)
                        shutil.copytree(item, dst, dirs_exist_ok=True)

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
                self._soft_delete_with_undo(
                    zp,
                    f"backup \"{zp.name}\"",
                    on_refresh=self._refresh_backup_list,
                )

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _on_open_backups_folder(self, *_):
        bdir = self._backups_dir()
        if not bdir:
            self._alert("No server selected", "Select a server to open backups.")
            return
        self._open_target(bdir)

