"""Policy: docs follow rule R9 (Simplified Technical English) and their links work."""
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("lint_docs", ROOT / "scripts" / "lint_docs.py")
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)

# Files that agents or the merger write at run time are left out. Project data and the logs follow no fixed style.
SKIP_NAMES = {"CHANGELOG.md", "IMPROVEMENTS.md"}
DOCS = [p for p in lint.doc_files() if p.relative_to(ROOT).parts[0] != "projects" and p.name not in SKIP_NAMES]
# The public copy has no private folder `public/`. It also leaves out the notes of the owner's machine, the decision log, and the morning steps. See D-063.
PRIVATE_REPOSITORY = (ROOT / "public" / "manifest.txt").is_file()
PRIVATE_ONLY_DOCS = ("HUMAN_STEPS.md", "DECISIONS.md", "MACHINE.md")


def test_the_doc_set_is_not_empty_and_has_the_required_files():
    names = {p.name for p in DOCS}
    required = ["README.md", "ARCHITECTURE.md", "SECURITY.md", "RUNBOOK.md", "SOUL.md"]
    if PRIVATE_REPOSITORY:
        required += ["CONTRIBUTING.md"] + list(PRIVATE_ONLY_DOCS)
    for need in required:
        assert need in names, need
    if PRIVATE_REPOSITORY:
        assert (ROOT / "docs" / "CHANGELOG.md").is_file()
    else:
        assert (ROOT / "CONTRIBUTING.md").is_file()           # the public copy keeps it at the top of the tree


@pytest.mark.parametrize("path", DOCS, ids=[str(p.relative_to(ROOT)) for p in DOCS])
def test_a_doc_has_no_dash_no_semicolon_and_no_long_sentence(path):
    problems = lint.check(path)
    assert not problems, problems[:5]


@pytest.mark.parametrize("path", [p for p in DOCS if p.suffix == ".md" and p.name != "SOUL.md"], ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_point_to_files_that_exist(path):
    text = path.read_text()
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    bad = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        rel = target.split("#", 1)[0]
        if rel and not (path.parent / rel).exists():
            bad.append(target)
    assert not bad, bad


@pytest.mark.skipif(not (ROOT / "docs" / "HUMAN_STEPS.md").is_file(), reason="the public copy has no morning steps file")
def test_the_human_steps_each_have_six_steps_or_fewer():
    text = (ROOT / "docs" / "HUMAN_STEPS.md").read_text()
    for section in re.split(r"(?m)^## ", text)[1:]:
        steps = re.findall(r"(?m)^\d+\. ", section)
        assert len(steps) <= 6, section.splitlines()[0]
