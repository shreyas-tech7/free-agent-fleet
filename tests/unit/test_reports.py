import pytest

from ops.fleetlib import reports

GOOD = """Done: The landing page passes all 14 tests.
Next: The orchestrator asks for release approval.
Blocked: none
Needs you: Approve the release in #approvals."""


def test_a_good_report_is_read_in_label_order():
    r = reports.parse(GOOD)
    assert list(r) == ["Done", "Next", "Blocked", "Needs you"]
    assert r["Done"] == "The landing page passes all 14 tests." and r["Blocked"] == "none"
    assert reports.needs_owner(r) and not reports.is_blocked(r)


def test_labels_work_in_any_case_with_bullets_and_extra_text_around():
    r = reports.parse("Finished the card.\n- done: A\n* NEXT: B\n  blocked: C\nneeds you: D\nThanks.")
    assert r == {"Done": "A", "Next": "B", "Blocked": "C", "Needs you": "D"}


def test_a_part_that_runs_over_more_lines_is_joined():
    r = reports.parse("Done: First part\n  and second part.\nNext: x\nBlocked: none\nNeeds you: none")
    assert r["Done"] == "First part and second part."


@pytest.mark.parametrize("empty", ["none", "None.", "n/a", "nothing", "-", ""])
def test_an_empty_part_reads_as_none(empty):
    r = reports.parse("Done: x\nNext: y\nBlocked: %s\nNeeds you: %s" % (empty, empty))
    assert r["Blocked"] == "none" and r["Needs you"] == "none" and not reports.needs_owner(r)


@pytest.mark.parametrize("text", ["", None, "Done: x\nNext: y\nBlocked: none", "just a note", "Done: x\nNext: y\nNeeds you: none"])
def test_a_note_with_a_missing_label_is_not_a_report(text):
    assert reports.parse(text) is None
    assert not reports.needs_owner(None) and not reports.is_blocked(None)


def test_dashes_and_semicolons_are_fixed_and_long_parts_are_cut():
    r = reports.parse("Done: A — B; C\nNext: " + "x" * 600 + "\nBlocked: none\nNeeds you: none")
    assert r["Done"] == "A, B. C" and len(r["Next"]) == reports.MAX_LINE


def test_the_first_copy_of_a_label_wins_and_a_second_one_is_ignored():
    r = reports.parse("Done: first\nDone: second\nNext: n\nBlocked: none\nNeeds you: none")
    assert r["Done"] == "first"


def test_render_makes_bold_labels_and_an_optional_title():
    r = reports.parse(GOOD)
    text = reports.render(r, "Build landing page")
    assert text.splitlines()[0] == "*Build landing page*" and "*Done:* The landing page passes all 14 tests." in text and len(text.splitlines()) == 5
    assert len(reports.render(r).splitlines()) == 4
