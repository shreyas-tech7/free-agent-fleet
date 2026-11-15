"""The private owner file. See D-046."""
import pathlib

import pytest
import yaml

from ops.fleetctl import cli, ownercmd
from ops.fleetlib import config, owner, paths

ROOT = pathlib.Path(__file__).resolve().parents[2]

GOOD = {"owner": {"name": "Test Owner", "email": "owner@example.com", "timezone": "America/Chicago"},
        "brief": {"time": "07:30", "max_words": 120, "channels": ["slack", "telegram"]},
        "quiet_hours": {"start": "23:00", "end": "06:00"},
        "channels": {"main": "slack", "backups": ["telegram"]}}


class Say:
    def __init__(self):
        self.lines = []

    def say(self, text):
        self.lines.append(text)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    return path


def test_the_template_and_the_private_file_are_valid():
    for name in ("templates/owner.example.yaml", "config/owner.yaml"):
        if name == "config/owner.yaml" and not (ROOT / name).exists():
            continue                                  # the public copy has the template only. See D-063.
        assert owner.validate(owner.read(ROOT / name)) == [], name


def test_the_template_holds_no_real_name_or_address():
    text = (ROOT / "templates" / "owner.example.yaml").read_text()
    assert "Your Name" in text and "you@example.com" in text


@pytest.mark.parametrize("change,problem", [
    (lambda d: d["owner"].update(name=""), "owner.name is empty"),
    (lambda d: d["owner"].update(email="not-an-address"), "owner.email does not look like an address"),
    (lambda d: d["owner"].update(timezone="Mars/Base"), "owner.timezone is not a time zone name"),
    (lambda d: d["brief"].update(time="7:30"), "brief.time must look like 07:00"),
    (lambda d: d["brief"].update(max_words=5), "brief.max_words must be a whole number from 30 to 300"),
    (lambda d: d["brief"].update(max_words=True), "brief.max_words must be a whole number from 30 to 300"),
    (lambda d: d["brief"].update(channels=["fax"]), "brief.channels holds an unknown channel"),
    (lambda d: d["quiet_hours"].update(end="25:00"), "quiet_hours.end must look like 22:00"),
    (lambda d: d["channels"].update(main="fax"), "channels.main must be one of: slack, telegram, discord, email"),
    (lambda d: d["channels"].update(backups=["slack"]), "channels.backups must hold telegram or discord, and not the main channel"),
    (lambda d: d.update(extra={}), "unknown sections: extra"),
])
def test_each_problem_is_named_without_repeating_a_value(change, problem):
    data = yaml.safe_load(yaml.safe_dump(GOOD))
    change(data)
    errors = owner.validate(data)
    assert problem in errors
    assert not any("Test Owner" in e or "owner@example.com" in e for e in errors)


def test_a_good_file_maps_onto_the_config_keys():
    cfg = owner.to_config(GOOD)
    assert cfg["owner"] == {"name": "Test Owner", "email": "owner@example.com", "timezone": "America/Chicago"}
    assert cfg["briefing"] == {"time": "07:30", "max_words": 120, "telegram": True, "discord": False, "email": {"enabled": False}}
    assert cfg["slack"]["quiet_hours"] == {"start": "23:00", "end": "06:00"} and cfg["channels"] == {"main": "slack", "backups": ["telegram"]}


def test_config_load_layers_defaults_then_the_owner_file_then_the_live_file(fleet_env):
    base = config.load()
    assert base["owner"]["name"] == "Your Name" and base["briefing"]["time"] == "07:00" and base["channels"]["main"] == "slack"
    write(paths.owner_config(), GOOD)
    layered = config.load()
    assert layered["owner"]["name"] == "Test Owner" and layered["briefing"]["time"] == "07:30" and layered["briefing"]["email"]["smtp_port"] == 587
    paths.config_file().write_text("briefing:\n  time: '08:00'\n")
    assert config.load()["briefing"]["time"] == "08:00" and config.load()["owner"]["name"] == "Test Owner"


def test_a_missing_or_broken_owner_file_changes_nothing(fleet_env):
    assert config.load() == config.load()
    assert config.load()["owner"]["name"] == "Your Name"
    paths.owner_config().write_text("owner: [unclosed")
    assert config.load()["owner"]["name"] == "Your Name"
    bad = yaml.safe_load(yaml.safe_dump(GOOD))
    bad["owner"]["timezone"] = "Mars/Base"
    write(paths.owner_config(), bad)
    assert config.load()["owner"]["name"] == "Your Name"


def test_the_owner_file_sits_beside_the_live_config_unless_the_environment_says_otherwise(fleet_env, monkeypatch, tmp_path):
    assert paths.owner_config() == pathlib.Path(paths.config_file()).parent / "owner.yaml"
    monkeypatch.setenv("FLEET_OWNER_CONFIG", str(tmp_path / "elsewhere.yaml"))
    assert paths.owner_config() == tmp_path / "elsewhere.yaml"


def test_check_names_problems_and_prints_no_value(fleet_env):
    say = Say()
    args = cli.build_parser().parse_args(["owner", "check"])
    assert ownercmd.cmd_check(args, say) == 1 and "no owner file" in say.lines[0]
    bad = yaml.safe_load(yaml.safe_dump(GOOD))
    bad["owner"]["email"] = "nope"
    write(paths.owner_config(), bad)
    say = Say()
    assert ownercmd.cmd_check(args, say) == 1 and "Problem: owner.email does not look like an address" in say.lines and "Test Owner" not in "".join(say.lines)
    write(paths.owner_config(), GOOD)
    say = Say()
    assert ownercmd.cmd_check(args, say) == 0 and say.lines == ["The owner file is valid."]


def test_apply_copies_a_valid_file_with_mode_644_and_refuses_a_bad_one(fleet_env, tmp_path):
    source = write(tmp_path / "src" / "owner.yaml", GOOD)
    args = cli.build_parser().parse_args(["owner", "apply", "--file", str(source)])
    say = Say()
    assert ownercmd.cmd_apply(args, say) == 0
    assert yaml.safe_load(paths.owner_config().read_text()) == GOOD and oct(paths.owner_config().stat().st_mode & 0o777) == "0o644"
    bad = yaml.safe_load(yaml.safe_dump(GOOD))
    bad["brief"]["time"] = "later"
    write(source, bad)
    before = paths.owner_config().read_text()
    say = Say()
    assert ownercmd.cmd_apply(args, say) == 1 and paths.owner_config().read_text() == before and any("Not applied" in l for l in say.lines)
    say = Say()
    args = cli.build_parser().parse_args(["owner", "apply", "--file", str(tmp_path / "missing.yaml")])
    assert ownercmd.cmd_apply(args, say) == 1
