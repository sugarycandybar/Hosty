"""Tests for the NetherNet migration notice shown on the Connect tab."""

from hosty.gtk_ui.views.connect.mixins.playit_mixin import PlayitMixin


class _View(PlayitMixin):
    def __init__(self, cfg):
        self._cfg = cfg


def _view(cfg):
    return _View(cfg)


def _cfg(**overrides):
    cfg = {
        "bedrock_endpoint": "tunnel.tun.ply.gg:19132",
        "bedrock_nethernet_token": "pnx1.token",
        "bedrock_nethernet_migrated": True,
        "bedrock_nethernet_notice_seen": False,
    }
    cfg.update(overrides)
    return cfg


def test_notice_shows_for_migrated_nethernet_tunnel():
    assert PlayitMixin._nethernet_notice_should_show(_view(_cfg())) is True


def test_notice_hidden_once_seen():
    assert PlayitMixin._nethernet_notice_should_show(_view(_cfg(bedrock_nethernet_notice_seen=True))) is False


def test_notice_hidden_without_bedrock_tunnel():
    assert PlayitMixin._nethernet_notice_should_show(_view(_cfg(bedrock_endpoint=""))) is False


def test_notice_hidden_without_nethernet_token():
    """An old RakNet tunnel has no token and should not show the notice."""
    assert PlayitMixin._nethernet_notice_should_show(_view(_cfg(bedrock_nethernet_token=""))) is False


def test_notice_hidden_for_a_fresh_nethernet_tunnel():
    """Only tunnels changed by the migration get the notice."""
    assert PlayitMixin._nethernet_notice_should_show(_view(_cfg(bedrock_nethernet_migrated=False))) is False


def test_refresh_reveals_banner_according_to_config():
    class _Banner:
        revealed = None

        def set_revealed(self, revealed):
            self.revealed = revealed

    view = _view(_cfg())
    view._nethernet_banner = _Banner()

    PlayitMixin._refresh_nethernet_banner(view)
    assert view._nethernet_banner.revealed is True

    view._cfg = _cfg(bedrock_nethernet_notice_seen=True)
    PlayitMixin._refresh_nethernet_banner(view)
    assert view._nethernet_banner.revealed is False
