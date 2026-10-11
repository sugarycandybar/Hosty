"""Offline tests for search-row install button decisions (no network, no display).

The row button answers only "is it on disk": Installed (any recognized
build) or Install. Version precision (Update/Downgrade) lives on the detail
page. One helper decides for initial paint refresh, async lookup, and
back-navigation refresh alike.
"""

from __future__ import annotations

import builtins
import json
import types


def _ensure_gettext():
    if not hasattr(builtins, "_"):
        builtins._ = lambda s: s  # noqa: E731
    if not hasattr(builtins, "ngettext"):
        builtins.ngettext = lambda singular, plural, n: singular if n == 1 else plural  # noqa: E731


def _hit(pid="P7", slug="lithium", title="Lithium"):
    return types.SimpleNamespace(
        project_id=pid,
        slug=slug,
        title=title,
        description="",
        downloads=1,
        icon_url=None,
        project_type="mod",
    )


def _version(version_id="new-id", filename="lithium-0.25.3.jar"):
    return types.SimpleNamespace(
        version_id=version_id,
        version_number="0.25.3",
        filename=filename,
        game_versions=["1.21.1"],
        loaders=["fabric"],
    )


def _stub(tmp_path, tracked=None, dependents=None):
    from hosty.gtk_ui.views.files.mixins.modrinth_mixin import ModrinthMixin
    from hosty.gtk_ui.views.files.mixins.mods_mixin import ModsMixin

    if tracked is None:
        tracked = {}
    (tmp_path / ".hosty-mod-installs.json").write_text(json.dumps({"mods": tracked}), encoding="utf-8")
    stub = types.SimpleNamespace()
    stub._server_dir = lambda: tmp_path
    stub._individual_mod_state_path = types.MethodType(ModsMixin._individual_mod_state_path, stub)
    stub._read_individual_mod_state = types.MethodType(ModsMixin._read_individual_mod_state, stub)
    stub._is_modpack_installed = lambda pid: False
    stub._is_datapack_installed = lambda pid: False
    stub._is_plugin_installed = lambda pid: False
    stub._is_mod_present = types.MethodType(ModsMixin._is_mod_present, stub)
    stub._looks_installed = types.MethodType(ModrinthMixin._looks_installed, stub)
    stub._dependency_dependents = lambda filename: list(dependents or [])
    stub._decide_row_install_button = types.MethodType(ModrinthMixin._decide_row_install_button, stub)
    return stub


def _decide(stub, hit=None, best=None, names=None, **flags):
    return stub._decide_row_install_button(
        hit or _hit(),
        best,
        names if names is not None else set(),
        is_modpack=flags.get("is_modpack", False),
        is_datapack=flags.get("is_datapack", False),
        is_plugin=flags.get("is_plugin", False),
        btn_label=flags.get("btn_label", "Install"),
    )


def test_current_file_on_disk_is_installed(tmp_path):
    _ensure_gettext()
    stub = _stub(tmp_path)
    assert _decide(stub, best=_version(), names={"lithium-0.25.3.jar"}) == ("Installed", False)


def test_older_tracked_file_on_disk_is_installed(tmp_path):
    """Installing an older build must read Installed, not Install."""
    _ensure_gettext()
    stub = _stub(
        tmp_path,
        tracked={
            "P7": {
                "title": "Lithium",
                "version_id": "old-id",
                "version_number": "0.25.2",
                "filename": "lithium-0.25.2.jar",
            }
        },
    )
    assert _decide(stub, best=_version(), names={"lithium-0.25.2.jar"}) == ("Installed", False)


def test_manual_drop_matching_slug_is_installed(tmp_path):
    _ensure_gettext()
    stub = _stub(tmp_path)
    assert _decide(stub, best=_version(), names={"lithium-0.25.2.jar"}) == ("Installed", False)


def test_nothing_on_disk_is_install(tmp_path):
    _ensure_gettext()
    stub = _stub(tmp_path)
    assert _decide(stub, best=_version(), names={"sodium-1.0.jar"}) == ("Install", True)


def test_unknown_best_disables_button(tmp_path):
    _ensure_gettext()
    stub = _stub(tmp_path)
    assert _decide(stub, best=None, names=set()) == ("Install", False)


def test_tracked_types_read_installed(tmp_path):
    _ensure_gettext()
    stub = _stub(tmp_path)
    stub._is_modpack_installed = lambda pid: True
    assert _decide(stub, is_modpack=True) == ("Installed", False)
    stub._is_modpack_installed = lambda pid: False
    stub._is_datapack_installed = lambda pid: True
    assert _decide(stub, is_datapack=True) == ("Installed", False)
    stub._is_datapack_installed = lambda pid: False
    stub._is_plugin_installed = lambda pid: True
    assert _decide(stub, is_plugin=True) == ("Installed", False)


def test_dependency_file_shows_dependency(tmp_path):
    _ensure_gettext()
    stub = _stub(tmp_path, dependents=["some-mod"])
    assert _decide(stub, best=_version(), names={"lithium-0.25.3.jar"}) == ("Dependency", False)
