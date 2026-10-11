"""Offline tests for Modrinth version-direction labels (no network, no display)."""

from __future__ import annotations

import builtins


def _ensure_gettext():
    if not hasattr(builtins, "_"):
        builtins._ = lambda s: s  # noqa: E731
    if not hasattr(builtins, "ngettext"):
        builtins.ngettext = lambda singular, plural, n: singular if n == 1 else plural  # noqa: E731


def test_newer_selection_is_update():
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.modrinth_mixin import ModrinthMixin

    ordered = ["v-new", "v-mid", "v-old"]
    assert ModrinthMixin._update_direction_label("v-old", "v-new", ordered) == "Update"
    assert ModrinthMixin._update_direction_label("v-mid", "v-new", ordered) == "Update"


def test_older_selection_is_downgrade():
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.modrinth_mixin import ModrinthMixin

    ordered = ["v-new", "v-mid", "v-old"]
    assert ModrinthMixin._update_direction_label("v-new", "v-old", ordered) == "Downgrade"
    assert ModrinthMixin._update_direction_label("v-new", "v-mid", ordered) == "Downgrade"


def test_unknown_direction_defaults_to_update():
    _ensure_gettext()
    from hosty.gtk_ui.views.files.mixins.modrinth_mixin import ModrinthMixin

    ordered = ["v-new", "v-old"]
    assert ModrinthMixin._update_direction_label("v-elsewhere", "v-new", ordered) == "Update"
    assert ModrinthMixin._update_direction_label("", "v-new", ordered) == "Update"
    assert ModrinthMixin._update_direction_label("v-old", "v-new", []) == "Update"
    assert ModrinthMixin._update_direction_label("v-old", "v-new", None) == "Update"
