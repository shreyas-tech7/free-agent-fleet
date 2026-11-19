"""The tool catalog: config/catalog.yaml, its checks, and the fleetctl command."""
import copy
import io
import pathlib
import re
import subprocess
import sys

import pytest
import yaml

from ops.fleetctl import catalogcmd, cli, ui
from ops.fleetlib import catalog

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILE = ROOT / "config" / "catalog.yaml"

# The addresses that the owner named on 2026-11-16. Each one must have a rated entry.
OWNER_LIST = """
NousResearch/hermes-agent NousResearch/hermes-agent-self-evolution cheaper-inference/OmniRoute BerriAI/litellm
cheahjs/free-llm-api-resources meta-llama/PurpleLlama cisco-ai-defense/skill-scanner cisco-ai-defense/mcp-scanner
NVIDIA/garak gitleaks/gitleaks google/osv-scanner semgrep/semgrep rtk-ai/rtk JuliusBrussee/caveman blader/humanizer
coreyhaines31/marketingskills gitroomhq/postiz-app obra/superpowers microsoft/playwright-mcp unclecode/crawl4ai
searxng/searxng VoltAgent/awesome-agent-skills LaurieWired/GhidraMCP gabrielekarra/mcp-anything HelpCode-ai/anythingmcp
gaarutyunov/mcp-anything jesse-merhi/rag-anything-mcp Astrosp/Awesome-OSINT-List morluto/rea affaan-m/ECC
""".split()
OTHER = ["https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2", "https://learn-anything.xyz/"]


@pytest.fixture(scope="module")
def entries():
    return catalog.load(FILE)


def test_the_catalog_file_is_valid(entries):
    assert len(entries) >= 32 and catalog.problems_of(yaml.safe_load(FILE.read_text())) == []


@pytest.mark.parametrize("repo", OWNER_LIST)
def test_every_repository_that_the_owner_named_has_an_entry(entries, repo):
    assert catalog.find(entries, "https://github.com/" + repo) is not None


@pytest.mark.parametrize("url", OTHER)
def test_the_other_named_addresses_have_an_entry(entries, url):
    assert catalog.find(entries, url) is not None


def test_find_matches_names_and_the_forms_of_a_github_address(entries):
    want = catalog.find(entries, "ghidra-mcp")
    for form in ("https://github.com/LaurieWired/GhidraMCP", "https://github.com/lauriewired/ghidramcp/", "http://www.github.com/LaurieWired/GhidraMCP.git",
                 "https://github.com/LaurieWired/GhidraMCP/tree/main/src"):
        assert catalog.find(entries, form) is want
    assert catalog.find(entries, "https://github.com/someone/unknown") is None and catalog.find(entries, "") is None
    assert catalog.find(entries, "https://github.com/LaurieWired/GhidraMCP-fork") is None


def test_a_rejected_entry_may_not_be_requested_and_an_unknown_source_may(entries):
    assert catalog.may_request(None) is True
    assert catalog.may_request(catalog.find(entries, "rea")) is False
    assert catalog.may_request(catalog.find(entries, "caveman")) is True


def test_a_vendored_entry_has_its_skill_folder_with_the_same_pinned_commit(entries):
    """The catalog and the UPSTREAM.md records must tell the same story."""
    commits = {}
    for upstream in sorted((ROOT / "skills").glob("*/UPSTREAM.md")):
        text = upstream.read_text()
        source = re.search(r"(?m)^- Source: (\S+)", text).group(1)
        commits.setdefault(catalog._key(source), set()).add(re.search(r"(?m)^- Commit: ([0-9a-f]{40})", text).group(1))
    vendored = [e for e in entries if e["status"] == "vendored"]
    assert len(vendored) >= 5
    for entry in vendored:
        found = commits.get(catalog._key(entry["url"]))
        assert found, "%s is marked vendored, and no skill names it as its source" % entry["name"]
        assert len(found) == 1 and next(iter(found)) in entry["pin"], "%s: the pin differs from UPSTREAM.md" % entry["name"]
    known = {catalog._key(e["url"]) for e in vendored}
    assert set(commits) <= known, "a skill source is missing in the catalog: %s" % sorted(set(commits) - known)


def test_no_agent_uses_a_skill_from_a_rejected_source(entries):
    rejected = {catalog._key(e["url"]) for e in entries if e["rating"] == "reject"}
    for upstream in (ROOT / "skills").glob("*/UPSTREAM.md"):
        source = re.search(r"(?m)^- Source: (\S+)", upstream.read_text()).group(1)
        assert catalog._key(source) not in rejected


def test_an_installed_entry_has_a_row_in_the_lock_file(entries):
    lock = (ROOT / "vendor" / "LOCK.md").read_text().lower()
    for entry in entries:
        if entry["status"] == "installed":
            assert catalog._key(entry["url"]).split("/")[-1] in lock or entry["name"] in lock, entry["name"]


BAD = [
    (lambda e: e.update(rating="maybe"), "rating"),
    (lambda e: e.update(status="soon"), "status"),
    (lambda e: e.update(kind="thing"), "kind"),
    (lambda e: e.update(url="http://example.org/x"), "https"),
    (lambda e: e.update(used_by=["nobody"]), "used_by"),
    (lambda e: e.pop("why"), "why"),
    (lambda e: e.update(name="Bad Name"), "name"),
    (lambda e: e.update(status="vendored", pin=""), "pin"),
    (lambda e: e.update(rating="reject", status="installed", pin="1"), "rejected"),
    (lambda e: e.update(rating="reject", status="none", used_by=["build"]), "rejected"),
]


@pytest.mark.parametrize("change,word", BAD)
def test_a_bad_entry_is_named(change, word):
    data = {"entries": [{"name": "demo", "url": "https://example.org/demo", "kind": "tool", "license": "MIT", "rating": "watch",
                         "status": "none", "used_by": [], "why": "A test.", "next": "None."}]}
    assert catalog.problems_of(data) == []
    bad = copy.deepcopy(data)
    change(bad["entries"][0])
    assert any(word in p for p in catalog.problems_of(bad))


def test_duplicates_and_broken_files_are_refused(tmp_path):
    one = {"name": "demo", "url": "https://github.com/a/b", "kind": "tool", "license": "MIT", "rating": "watch", "status": "none",
           "used_by": [], "why": "A test.", "next": "None."}
    assert any("twice" in p for p in catalog.problems_of({"entries": [one, dict(one, name="other", url="https://github.com/A/B/")]}))
    assert any("twice" in p for p in catalog.problems_of({"entries": [one, dict(one, url="https://github.com/a/c")]}))
    assert catalog.problems_of([]) and catalog.problems_of({"entries": []}) and catalog.problems_of({"entries": ["x"]})
    with pytest.raises(catalog.CatalogError):
        catalog.load(tmp_path / "missing.yaml")
    (tmp_path / "bad.yaml").write_text("entries: [")
    with pytest.raises(catalog.CatalogError):
        catalog.load(tmp_path / "bad.yaml")


# ---------------------------------------------------------------- the command
def run_cli(argv, monkeypatch):
    monkeypatch.setenv("FLEET_REPO_ROOT", str(ROOT))
    out = io.StringIO()
    code = cli.main(argv, console=ui.Console(stdout=out))
    return code, out.getvalue()


def test_the_command_lists_shows_and_checks(monkeypatch):
    code, text = run_cli(["catalog", "list", "--rating", "reject"], monkeypatch)
    assert code == 0 and "ghidra-mcp" in text and "hermes-agent" not in text
    code, text = run_cli(["catalog", "show", "https://github.com/JuliusBrussee/caveman"], monkeypatch)
    assert code == 0 and "rating:  trial" in text and "v4.1.0" in text
    code, text = run_cli(["catalog", "show", "https://github.com/nobody/nothing"], monkeypatch)
    assert code == 1 and "No entry" in text
    assert run_cli(["catalog", "check"], monkeypatch)[0] == 0
    assert run_cli(["catalog"], monkeypatch)[0] == 0


def test_the_catalog_command_needs_no_root():
    assert "catalog" in cli.NO_ROOT


def test_a_closed_pipe_is_not_an_error():
    """`fleetctl catalog list | head -1` printed two traces before. A reader that goes away is normal."""
    code = "import sys; sys.path.insert(0, %r)\nfrom ops.fleetctl import cli\nsys.exit(cli.main(['catalog', 'list']))" % str(ROOT)
    proc = subprocess.Popen([sys.executable, "-B", "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT))  # nosemgrep: dangerous-subprocess-use-audit
    proc.stdout.readline()
    proc.stdout.close()
    err = proc.stderr.read().decode()
    assert proc.wait(timeout=30) == 0 and "Traceback" not in err and "BrokenPipe" not in err
