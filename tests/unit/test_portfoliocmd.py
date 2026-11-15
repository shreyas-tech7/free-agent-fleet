import pytest

from ops.briefing import weekly
from ops.fleetctl import cli, portfoliocmd

SRC = weekly.HEADER_TEXT if hasattr(weekly, "HEADER_TEXT") else "# Portfolio log\n\nWhat the fleet shipped each week. Newest entries come last.\n"
W1 = "## Week ending 2026-11-15\n\nFirst entry."
W2 = "## Week ending 2026-11-22\n\nSecond entry."


class Say:
    def __init__(self):
        self.lines = []

    def say(self, text):
        self.lines.append(text)


def test_sections_are_read_in_file_order():
    assert list(portfoliocmd.sections(SRC + "\n" + W1 + "\n\n" + W2 + "\n")) == ["2026-11-15", "2026-11-22"]
    assert portfoliocmd.sections(SRC) == {}


def test_merge_adds_only_the_weeks_that_the_target_lacks_and_keeps_the_order():
    text, added = portfoliocmd.merge(SRC + "\n" + W1 + "\n\n" + W2 + "\n", SRC + "\n" + W1 + "\n")
    assert added == 1 and text.count("## Week ending") == 2 and text.index("2026-11-15") < text.index("2026-11-22") and text.endswith("Second entry.\n")
    again, added = portfoliocmd.merge(SRC + "\n" + W1 + "\n\n" + W2 + "\n", text)
    assert added == 0 and again == text


def test_merge_into_an_empty_target_starts_with_the_header():
    text, added = portfoliocmd.merge(SRC + "\n" + W1 + "\n", "")
    assert added == 1 and text.startswith("# Portfolio log") and "First entry." in text


def test_sync_copies_new_weeks_into_docs_and_is_safe_to_repeat(fleet_env, tmp_path):
    weekly.append("2026-11-15", "First entry.")
    repo = tmp_path / "clone"
    (repo / "docs").mkdir(parents=True)
    args = cli.build_parser().parse_args(["portfolio", "sync", "--repo", str(repo)])
    say = Say()
    assert portfoliocmd.cmd_sync(args, say) == 0 and say.lines[0].startswith("Added 1 week to")
    weekly.append("2026-11-22", "Second entry.")
    say = Say()
    assert portfoliocmd.cmd_sync(args, say) == 0 and say.lines[0].startswith("Added 1 week to")
    say = Say()
    assert portfoliocmd.cmd_sync(args, say) == 0 and say.lines == ["The repository file is up to date."]
    assert (repo / "docs" / "PORTFOLIO_LOG.md").read_text().count("## Week ending") == 2


def test_sync_says_what_is_missing(fleet_env, tmp_path):
    args = cli.build_parser().parse_args(["portfolio", "sync", "--repo", str(tmp_path / "nowhere")])
    say = Say()
    assert portfoliocmd.cmd_sync(args, say) == 1 and "no portfolio log" in say.lines[0]
    weekly.append("2026-11-15", "x")
    say = Say()
    assert portfoliocmd.cmd_sync(args, say) == 1 and "Pass --repo" in say.lines[0]
