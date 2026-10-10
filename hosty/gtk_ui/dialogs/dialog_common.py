"""
Shared sizing and layout helpers for setup/creation dialogs.

All multi-step wizard-style dialogs (Create Server, Playit setup, Manage
tunnel, Icon picker, Update version, Mod updates, ...) should use these
helpers so sizes and recurring patterns (header, scrolled form page,
centered progress page) stay cohesive.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

# Single cohesive size for all setup/creation dialogs. 400px (the old
# Create Server width) felt very narrow for ComboRow/EntryRow forms, so
# every dialog now shares this wider geometry.
DIALOG_CONTENT_WIDTH = 560
DIALOG_CONTENT_HEIGHT = 600

# Shared margins for progress/status content.
PROGRESS_MARGIN_START = 24
PROGRESS_MARGIN_END = 24


def apply_dialog_size(
    dialog: Adw.Dialog,
    title: str,
    width: int = DIALOG_CONTENT_WIDTH,
    height: int = DIALOG_CONTENT_HEIGHT,
) -> None:
    """Apply the shared title + content size to a dialog."""
    dialog.set_title(title)
    dialog.set_content_width(width)
    dialog.set_content_height(height)


def make_dialog_header(
    cancel_label: str = "Cancel",
    primary_label: str = "Next",
) -> tuple[Adw.HeaderBar, Gtk.Button, Gtk.Button]:
    """Build a cohesive wizard header (Cancel/Back left, primary right).

    Window controls are hidden so every wizard dialog has the same chrome.
    Returns (header, cancel_button, primary_button).
    """
    header = Adw.HeaderBar()
    header.set_show_start_title_buttons(False)
    header.set_show_end_title_buttons(False)

    cancel_btn = Gtk.Button(label=cancel_label)
    header.pack_start(cancel_btn)

    primary_btn = Gtk.Button(label=primary_label)
    primary_btn.add_css_class("suggested-action")
    header.pack_end(primary_btn)

    return header, cancel_btn, primary_btn


def wrap_in_scrolled(page: Gtk.Widget) -> Gtk.ScrolledWindow:
    """Wrap a PreferencesPage in a ScrolledWindow with shared policy."""
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_child(page)
    return scrolled


def build_progress_page(
    icon_name: str = "folder-download-symbolic",
    title: str = "",
    description: str = "",
) -> tuple[Gtk.Widget, Adw.StatusPage, Gtk.ProgressBar, Gtk.Label]:
    """Build a unified centered progress page.

    Returns (container, status_page, progress_bar, detail_label).
    Detail label is centered and dimmed, matching Create Server style.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.CENTER)
    box.set_hexpand(True)
    box.set_margin_start(PROGRESS_MARGIN_START)
    box.set_margin_end(PROGRESS_MARGIN_END)

    status = Adw.StatusPage()
    status.set_icon_name(icon_name)
    status.set_title(title)
    status.set_description(description)
    status.set_vexpand(False)
    status.set_hexpand(True)

    progress_bar = Gtk.ProgressBar()
    progress_bar.set_show_text(True)
    progress_bar.set_hexpand(True)
    progress_bar.set_margin_start(12)
    progress_bar.set_margin_end(12)
    progress_bar.add_css_class("hosty-progress")

    progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    progress_box.set_hexpand(True)
    progress_box.append(progress_bar)

    detail_label = Gtk.Label(label="")
    detail_label.add_css_class("dim-label")
    detail_label.set_wrap(True)
    detail_label.set_justify(Gtk.Justification.CENTER)
    detail_label.set_halign(Gtk.Align.CENTER)
    progress_box.append(detail_label)

    status.set_child(progress_box)
    box.append(status)
    return box, status, progress_bar, detail_label


def make_action_button(
    label: str,
    style: str | None = None,
    min_width: int = 280,
    min_height: int = 40,
) -> Gtk.Button:
    """Build a cohesive full-width-ish pill action button for dialogs."""
    btn = Gtk.Button(label=label)
    btn.add_css_class("pill")
    if style:
        btn.add_css_class(style)
    btn.set_halign(Gtk.Align.CENTER)
    btn.set_size_request(min_width, min_height)
    return btn
