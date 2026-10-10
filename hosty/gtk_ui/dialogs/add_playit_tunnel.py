"""
Add Playit Tunnel dialog.

Small chooser shown from the Playit group "+" button. Each available tunnel
kind is a row describing what setting it up involves; choosing one emits
``add-requested`` with the kind key (``"bedrock"`` or ``"voicechat"``) so the
caller can run that kind's normal setup flow.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk


class AddPlayitTunnelDialog(Adw.Dialog):
    """Chooser for adding an optional playit tunnel (Bedrock / Voice Chat)."""

    __gsignals__ = {
        "add-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, kinds: list[tuple[str, str]]):
        """Build the dialog.

        ``kinds`` is a list of ``(kind_key, title)`` tuples. Tapping a row
        (or its Add button) emits ``add-requested``; any required setup
        steps are explained by the normal per-kind setup flow that follows.
        """
        super().__init__()

        self.set_title(_("Add tunnel"))
        self.set_content_width(420)

        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)

        close_btn = Gtk.Button(label=_("Close"))
        close_btn.connect("clicked", lambda *_: self.close())
        header.pack_start(close_btn)

        toolbar_view.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        for kind_key, title in kinds:
            row = Adw.ActionRow(title=title)
            add_btn = Gtk.Button(label=_("Add"), valign=Gtk.Align.CENTER)
            add_btn.connect("clicked", self._on_add_clicked, kind_key)
            row.add_suffix(add_btn)
            row.set_activatable(True)
            row.connect("activated", self._on_add_clicked, kind_key)
            group.add(row)
        page.add(group)

        # No ScrolledWindow: with one or two rows the dialog sizes to its
        # content naturally instead of collapsing short.
        toolbar_view.set_content(page)
        self.set_child(toolbar_view)

    def _on_add_clicked(self, _widget, kind_key: str = ""):
        if kind_key:
            self.emit("add-requested", kind_key)
        self.close()
