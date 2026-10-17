"""Tests for the double-open guard in the Adw.Dialog.present hook.

A fast double-click on a button that opens a dialog used to create the
dialog twice (two ``clicked`` signals before the modal dialog grabbed
input). The shared ``_present_dialog`` helper suppresses a present while a
dialog of the same class and title is already on screen. These tests drive
that helper directly, with a fake present implementation, so they need no
display.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import pytest  # noqa: E402
from gi.repository import Adw  # noqa: E402

from hosty.gtk_ui.dialogs import dialog_common as dialog_common_mod  # noqa: E402
from hosty.gtk_ui.dialogs.dialog_common import _present_dialog  # noqa: E402


class _Recorder:
    """Stand-in for ``Adw.Dialog.present`` that records calls."""

    def __init__(self):
        self.calls = []

    def __call__(self, dialog, parent=None):
        self.calls.append(dialog)


@pytest.fixture(autouse=True)
def _clean_registry():
    dialog_common_mod._open_dialogs.clear()
    yield
    dialog_common_mod._open_dialogs.clear()


def test_same_dialog_opened_twice_is_suppressed():
    """The double-click scenario: a second identical dialog is not shown."""
    present = _Recorder()
    first = Adw.AlertDialog.new("Confirm", "Are you sure?")
    second = Adw.AlertDialog.new("Confirm", "Are you sure?")

    _present_dialog(first, None, present)
    _present_dialog(second, None, present)

    assert present.calls == [first]


def test_different_titles_still_stack():
    """Distinct dialogs (e.g. a confirm followed by an error) are allowed."""
    present = _Recorder()
    confirm = Adw.AlertDialog.new("Confirm", "Are you sure?")
    error = Adw.AlertDialog.new("Something failed", "Details")

    _present_dialog(confirm, None, present)
    _present_dialog(error, None, present)

    assert present.calls == [confirm, error]


def test_custom_dialog_same_type_and_title_suppressed():
    present = _Recorder()
    first = Adw.Dialog()
    first.set_title("Create Server")
    second = Adw.Dialog()
    second.set_title("Create Server")

    _present_dialog(first, None, present)
    _present_dialog(second, None, present)

    assert present.calls == [first]


def test_same_dialog_reopens_after_close():
    """Closing the dialog releases the guard so it can be opened again."""
    present = _Recorder()
    first = Adw.AlertDialog.new("Confirm", "Are you sure?")
    _present_dialog(first, None, present)

    first.emit("closed")

    second = Adw.AlertDialog.new("Confirm", "Are you sure?")
    _present_dialog(second, None, present)

    assert present.calls == [first, second]


def test_failed_present_is_retriable():
    """A present that raises must not leave the dialog stuck open."""

    def failing(_dialog, _parent=None):
        raise RuntimeError("no display")

    dialog = Adw.AlertDialog.new("Confirm", "Are you sure?")
    with pytest.raises(RuntimeError):
        _present_dialog(dialog, None, failing)

    assert dialog_common_mod._open_dialogs == []

    present = _Recorder()
    _present_dialog(dialog, None, present)
    assert present.calls == [dialog]


def test_ctrl_w_still_installed_by_helper():
    dialog = Adw.AlertDialog.new("Confirm", "Are you sure?")
    _present_dialog(dialog, None, _Recorder())
    assert getattr(dialog, "_hosty_ctrl_w_installed", False) is True
