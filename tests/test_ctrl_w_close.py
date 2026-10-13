"""Tests for Ctrl+W closing dialogs.

Dialogs are separate toplevels, so the main window's ``win.close-window``
accelerator never reaches them. ``add_ctrl_w_close`` attaches a
dialog-local shortcut instead. These tests run without a display: they
verify the wiring (trigger + close behavior), while real key dispatch is
covered by manual Xvfb verification, not CI.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from hosty.gtk_ui.dialogs import dialog_common as dialog_common_mod  # noqa: E402
from hosty.gtk_ui.dialogs.dialog_common import add_ctrl_w_close, install_ctrl_w_for_dialogs  # noqa: E402


def test_ctrl_w_shortcut_trigger():
    dialog = Adw.AlertDialog()
    shortcut = add_ctrl_w_close(dialog)

    assert Gtk.ShortcutTrigger.to_string(shortcut.get_trigger()) == "<Control>w"
    assert isinstance(shortcut.get_action(), Gtk.CallbackAction)


def test_ctrl_w_shortcut_closes_dialog():
    dialog = Adw.AlertDialog()
    closed = []
    dialog.close = lambda: closed.append(True)  # noqa: E731
    shortcut = add_ctrl_w_close(dialog)

    handled = shortcut.get_action().activate(Gtk.ShortcutActionFlags.EXCLUSIVE, dialog)

    assert handled is True
    assert closed == [True]


def test_ctrl_w_close_is_resilient():
    """A failing close() must not break shortcut handling."""

    class _BrokenDialog(Adw.AlertDialog):
        def close(self):  # noqa: D102
            raise RuntimeError("boom")

    dialog = _BrokenDialog()
    shortcut = add_ctrl_w_close(dialog)

    handled = shortcut.get_action().activate(Gtk.ShortcutActionFlags.EXCLUSIVE, dialog)

    assert handled is True


def test_install_hooks_present_idempotently():
    """install_ctrl_w_for_dialogs() wires present() once; presenting attaches the shortcut."""
    previous = Adw.Dialog.present
    install_ctrl_w_for_dialogs()
    install_ctrl_w_for_dialogs()
    try:
        assert Adw.Dialog.present is not previous

        dialog = Adw.AlertDialog()
        try:
            dialog.present()
        except Exception:
            pass
        assert getattr(dialog, "_hosty_ctrl_w_installed", False) is True

        # Re-presenting must not attach a second shortcut.
        controllers_before = len(list(dialog.observe_controllers()))
        try:
            dialog.present()
        except Exception:
            pass
        assert len(list(dialog.observe_controllers())) == controllers_before
    finally:
        Adw.Dialog.present = previous
        dialog_common_mod._dialog_present_orig = None
