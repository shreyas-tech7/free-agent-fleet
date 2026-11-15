import pytest

from ops.fleetlib import housestyle as hs


@pytest.mark.parametrize("text,problem", [
    ("The build passed — all 40 tests.", "dash"),
    ("Pages 10–20 changed.", "dash"),
    ("The build passed; the deploy waits.", "semicolon"),
    ("It is important to note that the deploy waits.", "banned phrase: it is important to note"),
    ("This result is a testament to the tests.", "banned phrase: testament"),
    ("The tests are not just fast, but also clear.", "banned phrase: not just ... but also"),
    ("A pivotal step.", "banned phrase: pivotal"),
    ("A seamless install.", "banned phrase: seamless"),
    ("Seamlessly done.", "banned phrase: seamless"),
    ("We delve into the logs.", "banned phrase: delve"),
    ("She delved deep.", "banned phrase: delve"),
    ("The site was built by the build agent.", "passive voice: was built by"),
    ("The bug is quickly fixed by the patch.", "passive voice: is quickly fixed by"),
])
def test_each_banned_thing_is_found(text, problem):
    assert problem in hs.check(text)


@pytest.mark.parametrize("text", [
    "The build passed. All 40 tests pass.",
    "Research found 3 candidate pains. Two have 10 or more complaints.",
    "Run `fleetctl doctor; fleetctl test` to check. The page https://example.com/a;b is not checked.",
    "```\nA — B; C\n```\nPlain words after the code.",
    "The test not just once. Then we run it but also later.",
    "Not just. But also.",
    "",
])
def test_good_text_and_code_pass(text):
    assert hs.check(text) == []


def test_a_long_sentence_is_a_problem_and_a_short_one_is_not():
    long = " ".join(["word"] * 40) + "."
    assert hs.check(long) == ["sentence of 40 words"]
    assert hs.check(" ".join(["word"] * 32) + ".") == []
    assert hs.check("Short. " + long) == ["sentence of 40 words"]


def test_fix_repairs_dashes_and_semicolons_and_keeps_code_and_addresses():
    assert hs.fix("The build passed — all 40 tests.") == "The build passed, all 40 tests."
    assert hs.fix("Pages 10–20 changed; the rest did not.") == "Pages 10-20 changed. The rest did not."
    assert hs.fix("Done;") == "Done."
    assert hs.fix("Run `a; b` now; then wait.") == "Run `a; b` now. Then wait."
    assert hs.fix("See https://example.com/a;b—c now") == "See https://example.com/a;b—c now"
    assert hs.fix("```\nx; y—z\n```\nok; fine") == "```\nx; y—z\n```\nok. Fine"


def test_clean_returns_the_fixed_text_and_what_is_left():
    text, left = hs.clean("A seamless build — done; shipped.")
    assert text == "A seamless build, done. Shipped." and left == ["banned phrase: seamless"]
    assert hs.clean("All good.") == ("All good.", [])


def test_fix_is_idempotent():
    once = hs.fix("a — b; c – d")
    assert hs.fix(once) == once and hs.check(once) == []
