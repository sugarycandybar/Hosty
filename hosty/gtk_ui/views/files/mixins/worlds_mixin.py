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

class WorldsMixin:
    def _make_world_row(self, path: Path) -> Adw.ActionRow:
        dims = _world_dimension_dirs(path)
        row = Adw.ActionRow(title=path.name)
        dim_count = len(dims)
        row.set_subtitle(f"{dim_count} dimension{'s' if dim_count != 1 else ''}")
        row.set_activatable(True)
        row.connect("activated", lambda *_p, p=path: self._open_world_dialog(p))
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
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        return row

    def _open_world_dialog(self, world_path: Path) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading(world_path.name)
        dialog.set_body("Manage dimensions.")
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(6)
        content.set_margin_bottom(6)

        group = Adw.PreferencesGroup(title="Dimensions")
        dims = _world_dimension_dirs(world_path)
        if not dims:
            none_row = Adw.ActionRow(title="No dimension folders found")
            none_row.set_activatable(False)
            group.add(none_row)
        else:
            world_root = world_path.resolve()
            for label, dim_path in dims:
                row = Adw.ActionRow(title=label)
                row.set_activatable(False)

                open_dim_btn = self._icon_button(
                    "folder-open-symbolic",
                    "Open dimension folder",
                    lambda *_p, p=dim_path: self._open_target(p),
                )
                row.add_suffix(open_dim_btn)

                # Deleting the world root dimension would remove the full world.
                if dim_path.resolve() != world_root:
                    delete_btn = self._icon_button(
                        "user-trash-symbolic",
                        "Delete dimension",
                        lambda *_p, w=world_path, p=dim_path, n=label: self._confirm_delete_dimension(w, p, n),
                        destructive=True,
                    )
                    row.add_suffix(delete_btn)

                group.add(row)

        content.append(group)
        dialog.set_extra_child(content)
        dialog.present(self.get_root())

    def _confirm_delete_dimension(self, world_path: Path, dim_path: Path, name: str):
        if self._is_running():
            self._alert("Server is running", "Stop the server before deleting a dimension.")
            return

        if dim_path.resolve() == world_path.resolve():
            self._alert("Cannot delete world root", "Use Delete world to remove the entire world.")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete dimension?")
        dialog.set_body(f"Delete dimension “{name}”? This cannot be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "delete":
                self._soft_delete_with_undo(
                    dim_path,
                    f"dimension \"{name}\"",
                    on_refresh=self._rebuild_lists,
                )

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

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
                self._soft_delete_with_undo(
                    path,
                    f"world \"{name}\"",
                    on_refresh=self._rebuild_lists,
                )

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

