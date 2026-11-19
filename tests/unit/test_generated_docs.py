"""Files that a script makes from the registry must match the registry. The docs linter must read prose only."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location("%s_for_generated_docs_test" % name, ROOT / "scripts" / ("%s.py" % name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generated_docs_match_the_secrets_registry(capsys):
    """A new secret in config/secrets.yaml must reach .env.example and the routing table. Run scripts/gen_docs.py. See F-20."""
    assert load("gen_docs").main(["--check"]) == 0, capsys.readouterr().err


def test_the_third_party_list_matches_the_lock_file():
    if not (ROOT / "public" / "overlay").is_dir():
        pytest.skip("the public copy ships the list at its root and has no overlay folder")
    assert load("gen_third_party").main(["--check"]) == 0, "run scripts/gen_third_party.py"


def test_the_linter_skips_a_yaml_block_at_the_top(tmp_path):
    lint = load("lint_docs")
    doc = tmp_path / "STATE.md"
    doc.write_text("---\nslug: demo\nnote: a; b\n" + "".join("k%d: v\n" % i for i in range(60)) + "---\n\n# Title\n\nA short sentence.\n")
    assert lint.check(doc) == []
    doc.write_text("# Title\n\n---\n\nOne; two.\n")                       # a rule line in the body is not a YAML block
    assert [msg for _no, msg in lint.check(doc)] == ["semicolon"]
    assert lint.front_matter_end(["---", "a: 1"]) == 0 and lint.front_matter_end([]) == 0
