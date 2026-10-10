"""
Manage Playit Tunnel dialog.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

from hosty.gtk_ui.dialogs.dialog_common import (
    apply_dialog_size,
    make_action_button,
    make_dialog_header,
)


class ManagePlayitTunnelDialog(Adw.Dialog):
    """Dialog to manage a playit tunnel (show details, edit local port, regenerate, delete)."""

    __gsignals__ = {
        "regenerate": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "delete": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "port-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, tunnel_name: str, connection_type: str, local_port: int, domain: str):
        super().__init__()

        apply_dialog_size(self, _("Manage {} Tunnel").format(tunnel_name))

        # Parse domain if it contains a remote port (format: "domain:port")
        remote_port = local_port
        display_domain = domain
        if ":" in domain:
            parts = domain.rsplit(":", 1)
            display_domain = parts[0]
            try:
                remote_port = int(parts[1])
            except (ValueError, IndexError):
                pass

        self._tunnel_name = tunnel_name
        self._local_port = local_port

        self._toolbar_view = Adw.ToolbarView()

        header, close_btn, self._save_btn = make_dialog_header(
            cancel_label=_("Close"),
            primary_label=_("Save"),
        )
        # Disabled until the port actually differs from the current one.
        self._save_btn.set_sensitive(False)
        self._save_btn.connect("clicked", self._on_save_port)
        close_btn.connect("clicked", lambda *_: self.close())

        self._toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(title=_("Tunnel Details"))

        # Connection Type
        type_row = Adw.ActionRow(title=_("Connection type"), subtitle=connection_type)
        group.add(type_row)

        # Local Port - editable with SpinButton (+/-)
        local_port_row = Adw.ActionRow(title=_("Local port"))
        self._port_spin = Gtk.SpinButton.new_with_range(1.0, 65535.0, 1.0)
        self._port_spin.set_value(float(local_port))
        self._port_spin.set_valign(Gtk.Align.CENTER)
        self._port_spin.connect("value-changed", self._on_port_value_changed)
        local_port_row.add_suffix(self._port_spin)
        group.add(local_port_row)

        # Playit port (show remote port for tunnel endpoint)
        port_row = Adw.ActionRow(title=_("Playit port"), subtitle=str(remote_port))
        group.add(port_row)

        # Playit Domain
        domain_row = Adw.ActionRow(title=_("Playit Domain"), subtitle=display_domain)
        group.add(domain_row)

        page.add(group)
        page.set_vexpand(True)

        action_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        action_box.set_margin_top(12)
        action_box.set_margin_bottom(18)
        action_box.set_margin_start(24)
        action_box.set_margin_end(24)
        action_box.set_halign(Gtk.Align.CENTER)

        # Regenerate button
        regen_btn = make_action_button(_("Regenerate Domain"))
        regen_btn.connect("clicked", self._on_regenerate)
        action_box.append(regen_btn)

        # Delete button
        delete_btn = make_action_button(_("Delete Tunnel"), style="destructive-action")
        delete_btn.connect("clicked", self._on_delete)
        action_box.append(delete_btn)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(page)
        content_box.append(action_box)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(content_box)

        self._toolbar_view.set_content(scrolled)
        self.set_child(self._toolbar_view)

    def _on_port_value_changed(self, _spin):
        self._save_btn.set_sensitive(int(self._port_spin.get_value()) != self._local_port)

    def _on_save_port(self, *_args):
        new_port = int(self._port_spin.get_value())
        if new_port != self._local_port:
            self.emit("port-changed", new_port)
        self.close()

    def _on_regenerate(self, *_args):
        self.emit("regenerate")
        self.close()

    def _on_delete(self, *_args):
        self.emit("delete")
        self.close()
