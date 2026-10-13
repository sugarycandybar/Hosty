"""Managed-mods dialog shared by the Files and Properties views."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk

from hosty.gtk_ui.dialogs.dialog_common import apply_dialog_size, wrap_in_scrolled


def server_mod_metadata(
    server_manager,
    server_id: str,
    pack_project_id: str | None = None,
) -> dict[str, dict[str, str]]:
    """Merged tracked-mod metadata for a managed-mods dialog.

    Individual mod state first, then the modpack's own file details on
    top (they describe the pack's pinned versions). Best-effort: {} when
    unavailable.
    """
    merged: dict[str, dict[str, str]] = {}
    try:
        if server_manager is not None:
            merged.update(server_manager.mod_file_metadata(server_id) or {})
            if pack_project_id:
                try:
                    merged.update(server_manager.modpack_file_details(server_id, pack_project_id) or {})
                except Exception:
                    pass
    except Exception:
        pass
    return merged


def _open_mod_page(project_id: str) -> None:
    uri = f"https://modrinth.com/mod/{project_id}"
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
        return
    except Exception:
        pass
    try:
        import webbrowser

        if webbrowser.open(uri):
            return
    except Exception:
        pass
    try:
        import subprocess
        import sys

        from hosty.shared.utils.subprocess_utils import hidden_subprocess_kwargs

        cmd = ["open", uri] if sys.platform == "darwin" else ["xdg-open", uri]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            **hidden_subprocess_kwargs(),
        )
    except Exception:
        pass


def show_managed_mods_dialog(
    parent: Gtk.Widget,
    modpack_title: str,
    mods: list[str],
    metadata: dict[str, dict[str, str]] | None = None,
) -> None:
    """Show the mod files managed by a modpack in a standard dialog.

    ``metadata`` maps lowercase filenames to ``{"title", "version",
    "project_id"}`` (see ``ServerManager.mod_file_metadata``); files
    without metadata fall back to their filename.
    """
    lookup = metadata or {}
    cleaned = []
    for item in mods:
        name = str(item).strip()
        if name.startswith("- "):
            name = name[2:].strip()
        if name:
            cleaned.append(name)
    cleaned = sorted(set(cleaned), key=str.lower)

    dialog = Adw.Dialog()
    apply_dialog_size(dialog, modpack_title)

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    header.set_show_start_title_buttons(False)
    header.set_show_end_title_buttons(False)
    close_btn = Gtk.Button(label=_("Close"))
    close_btn.connect("clicked", lambda *_: dialog.close())
    header.pack_start(close_btn)
    toolbar.add_top_bar(header)

    if not cleaned:
        status = Adw.StatusPage()
        status.set_title(modpack_title)
        status.set_description(_("No tracked mod files for this modpack yet."))
        toolbar.set_content(status)
    else:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title=_("Managed mods"),
            description=ngettext("{count} mod file", "{count} mod files", len(cleaned)).format(count=len(cleaned)),
        )
        for name in cleaned:
            meta = lookup.get(name.lower(), {})
            version = str(meta.get("version", "")).strip()
            project_id = str(meta.get("project_id", "")).strip()
            row = Adw.ActionRow(title=name)
            row.set_activatable(False)
            if version:
                row.set_subtitle(version)
            if project_id:
                open_btn = Gtk.Button(icon_name="web-browser-symbolic", valign=Gtk.Align.CENTER)
                open_btn.add_css_class("flat")
                open_btn.set_tooltip_text(_("Open mod page"))
                open_btn.connect("clicked", lambda *_b, pid=project_id: _open_mod_page(pid))
                row.add_suffix(open_btn)
            group.add(row)
        page.add(group)
        toolbar.set_content(wrap_in_scrolled(page))

    dialog.set_child(toolbar)
    dialog.present(parent)


def managed_mods_button(
    parent: Gtk.Widget,
    modpack_title: str,
    mods: list[str],
    metadata: dict[str, dict[str, str]] | None = None,
) -> Gtk.Button:
    """Build a "View managed mods" suffix button for a modpack row."""
    button = Gtk.Button(icon_name="view-list-symbolic", valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.set_tooltip_text(_("View managed mods"))
    button.connect("clicked", lambda *_: show_managed_mods_dialog(parent, modpack_title, mods, metadata))
    return button
