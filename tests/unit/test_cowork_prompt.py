"""The prompt for Claude Cowork. See docs/COWORK_PROMPT.md and D-060."""
import importlib.util
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "COWORK_PROMPT.md"


def generator():
    spec = importlib.util.spec_from_file_location("gen_docs_for_test", ROOT / "scripts" / "gen_docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_prompt_is_the_generated_one():
    assert DOC.read_text() == generator().render_cowork()


def test_both_manifests_are_inside_the_prompt_exactly():
    text = DOC.read_text()
    for name in ("fleet-hq", "hermes"):
        manifest = json.loads((ROOT / "slack" / ("%s.manifest.json" % name)).read_text())
        assert json.dumps(manifest, indent=2) in text, name
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
    assert len(blocks) == 2 and all(json.loads(b) for b in blocks)


def test_the_prompt_gives_cowork_the_four_required_rules():
    text = DOC.read_text()
    assert "Do not ask questions." in text
    assert "Never read, copy, or type a token." in text and "Never take a screenshot of a token screen." in text
    assert "Stop at each token screen." in text and "`fleetctl secret set NAME`" in text and "your own SSH window" in text
    assert "write a short prompt for Claude Code" in text and "10 lines or less" in text


def test_each_token_step_names_the_secret_that_the_owner_sets():
    text = DOC.read_text()
    for name in ("SLACK_HQ_BOT_TOKEN", "SLACK_HQ_APP_TOKEN", "SLACK_HERMES_BOT_TOKEN", "SLACK_HERMES_APP_TOKEN"):
        assert "fleetctl secret set %s" % name in text
    import yaml
    registry = {s["name"] for s in yaml.safe_load((ROOT / "config" / "secrets.yaml").read_text())["secrets"]}
    assert {"SLACK_HQ_BOT_TOKEN", "SLACK_HQ_APP_TOKEN", "SLACK_HERMES_BOT_TOKEN", "SLACK_HERMES_APP_TOKEN"} <= registry


def test_the_prompt_holds_no_token_and_makes_no_account_and_no_payment():
    text = DOC.read_text()
    assert not re.search(r"xox[abp]-[A-Za-z0-9-]{8,}|xapp-[A-Za-z0-9-]{8,}", text)
    assert "Do not create an account. Do not add a payment method." in text


def test_the_prompt_does_not_change_the_approval_posture():
    text = DOC.read_text()
    assert '"/yolo"' not in text and '"/approvals"' not in text
