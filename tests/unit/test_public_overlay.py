"""The public tree. These tests build the real export tree and check what a stranger would read. See D-051."""
import importlib.util
import pathlib
import re

import pytest
import yaml

from ops.fleetctl import cli
from ops.public import export, scans

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "public" / "overlay"
needs_git = pytest.mark.skipif(not (ROOT / ".git").exists(), reason="the export reads the git index")
# The public copy has the export tool but not the private `public/` folder with its manifest and overlay. These tests check that folder. See D-063.
pytestmark = pytest.mark.skipif(not (ROOT / "public" / "manifest.txt").is_file(), reason="this tree has no public/ folder")

spec = importlib.util.spec_from_file_location("lint_docs", ROOT / "scripts" / "lint_docs.py")
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    out = tmp_path_factory.mktemp("public-tree")
    export.build_tree(ROOT, out, ROOT / "public" / "manifest.txt", OVERLAY)
    return out


def overlay_docs():
    return sorted(p for p in OVERLAY.rglob("*.md") if p.name != "STATE.md")        # a STATE.md starts with front matter, which is data


def test_the_overlay_has_the_files_that_the_prompt_asks_for():
    for rel in ("README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", "THIRD_PARTY.md", "SECURITY.md", "CONTRIBUTING.md", "docker-compose.yml",
                ".github/workflows/ci.yml", "docs/PROMPT_FOR_YOUR_AGENT.md", "docs/PROVIDERS.md", "templates/project/STATE.md", "templates/brand-kit/brand.yaml"):
        assert (OVERLAY / rel).is_file(), rel


OVERLAY_DOCS = overlay_docs()


@pytest.mark.parametrize("path", OVERLAY_DOCS, ids=[str(p.relative_to(OVERLAY)) for p in OVERLAY_DOCS])
def test_an_overlay_doc_follows_the_docs_style(path):
    assert lint.check(path) == [], lint.check(path)[:3]


@needs_git
def test_every_relative_link_in_the_public_docs_works_in_the_public_tree(tree):
    bad = []
    for path in sorted(tree.rglob("*.md")):
        if "skills" in path.relative_to(tree).parts or "tests" in path.relative_to(tree).parts:
            continue
        text = re.sub(r"```.*?```", "", path.read_text(errors="replace"), flags=re.S)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            rel = target.split("#", 1)[0]
            if rel and not (path.parent / rel).exists():
                bad.append("%s -> %s" % (path.relative_to(tree), target))
    assert not bad, bad[:8]


@needs_git
def test_the_public_docs_name_only_real_fleetctl_commands(tree):
    parser = cli.build_parser()
    commands = set(next(a for a in parser._actions if getattr(a, "choices", None) and "status" in a.choices).choices)
    bad = []
    for name in ("README.md", "AGENTS.md", "docs/PROMPT_FOR_YOUR_AGENT.md", "docs/PROVIDERS.md", "SECURITY.md"):
        for command in re.findall(r"`fleetctl ([a-z][a-z0-9-]*)", (tree / name).read_text()):
            if command not in commands:
                bad.append("%s: fleetctl %s" % (name, command))
    assert not bad, bad


@needs_git
def test_the_public_tree_holds_no_file_from_the_hard_deny_list(tree):
    assert [str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file() and export.denied(str(p.relative_to(tree)))] == []


@needs_git
def test_the_public_tree_passes_the_generic_pattern_scan(tree):
    allow = scans.load_allow(ROOT / "public" / "scan-allow.txt")
    found = scans.scan_tree(tree, terms=(), allow=allow)
    assert found == [], found[:10]


def test_the_third_party_list_matches_the_lock_file():
    spec2 = importlib.util.spec_from_file_location("gen_tp", ROOT / "scripts" / "gen_third_party.py")
    gen = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(gen)
    assert (OVERLAY / "THIRD_PARTY.md").read_text() == gen.render(), "run: python3 scripts/gen_third_party.py"


def test_the_license_is_mit_for_the_contributors():
    text = (OVERLAY / "LICENSE").read_text()
    assert text.startswith("MIT License") and "Copyright (c) the free-agent-fleet contributors" in text


def test_ci_pins_every_action_to_a_commit_and_runs_the_four_checks():
    text = (OVERLAY / ".github" / "workflows" / "ci.yml").read_text()
    uses = re.findall(r"uses: (\S+)", text)
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses), uses
    jobs = yaml.safe_load(text)["jobs"]
    assert set(jobs) == {"shellcheck", "tests", "secrets-and-patterns", "osv"}
    for needle in ("shellcheck", "pytest", "gitleaks", "osv-scanner", "public_scan.py", "sha256sum -c"):
        assert needle in text


def test_the_compose_file_binds_loopback_only_and_holds_no_secret():
    data = yaml.safe_load((OVERLAY / "docker-compose.yml").read_text())
    searx = data["services"]["searxng"]
    assert all(p.startswith("127.0.0.1:") for p in searx["ports"])
    assert data["services"]["n8n"]["network_mode"] == "host" and data["services"]["n8n"]["environment"]["N8N_LISTEN_ADDRESS"] == "127.0.0.1"
    assert "${N8N_ENCRYPTION_KEY:?" in (OVERLAY / "docker-compose.yml").read_text()


def test_the_agent_instructions_hold_the_rules_of_the_prompt():
    text = (OVERLAY / "AGENTS.md").read_text()
    for needle in ("Never ask the human to paste a secret", "fleetctl secret set", "Never make anything public", "Never spend money", "dry-run", "exact clicks"):
        assert needle in text, needle
    assert (OVERLAY / "CLAUDE.md").read_text().startswith("@AGENTS.md")
