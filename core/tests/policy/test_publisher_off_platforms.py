"""Policy: the Publisher refuses platforms that are off, and no platform is on by default."""
import yaml

from ops.fleetlib import config
from ops.publisher import adapters as adp
from ops.publisher import core


class NeverCalled:
    def request(self, *a, **k):
        raise AssertionError("an adapter made a network call for a platform that is off")


def publisher(tmp_path, cfg):
    adapters = {n: adp.ADAPTERS[n](cfg["publisher"]["platforms"][n], {}, NeverCalled()) for n in core.PLATFORMS}
    return core.Publisher(cfg, adapters, lambda t: {"label": "SAFE"}, tmp_path / "s", tmp_path / "o")


def test_the_default_config_turns_every_platform_off():
    platforms = config.defaults()["publisher"]["platforms"]
    assert set(platforms) == set(core.PLATFORMS)
    assert not any(p["enabled"] for p in platforms.values())
    assert config.defaults()["publisher"]["mode"] == "dry-run"


def test_every_platform_that_is_off_is_refused_in_both_modes(tmp_path):
    for mode in ("dry-run", "live"):
        cfg = config.defaults()
        cfg["publisher"]["mode"] = mode
        pub = publisher(tmp_path, cfg)
        for name in core.PLATFORMS:
            out = pub.post({"platform": name, "text": "Hello from the studio.", "project": "demo"})
            assert out == {"status": "blocked", "reason": "platform_off"}, (mode, name)
        assert not (tmp_path / "o").exists()


def test_x_and_reddit_have_adapters_but_cannot_be_ready_without_api_access():
    cfg = config.defaults()["publisher"]["platforms"]
    assert adp.ADAPTERS["reddit"](cfg["reddit"], {"REDDIT_CLIENT_SECRET": "s" * 30}).ready()[0] is False
    assert adp.ADAPTERS["x"](cfg["x"], {"X_ACCESS_TOKEN": "t" * 30}).ready()[0] is False   # needs the label confirmation
