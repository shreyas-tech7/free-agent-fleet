"""The installer: every script parses, follows the house rules, and the numbered steps are in a sane order."""
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "install").glob("*.sh")) + [ROOT / "install.sh"]
STEPS = sorted((ROOT / "install").glob("[0-9][0-9]-*.sh"))


@pytest.mark.parametrize("path", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_each_script_parses(path):
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0


@pytest.mark.parametrize("path", STEPS + [ROOT / "install" / "root-tasks.sh"], ids=[p.name for p in STEPS] + ["root-tasks.sh"])
def test_each_step_stops_on_error_and_checks_for_root(path):
    text = path.read_text()
    assert "set -euo pipefail" in text
    assert "need_root" in text or 'id -u' in text


def test_the_step_numbers_are_unique_and_ordered_by_dependency():
    numbers = [int(p.name[:2]) for p in STEPS]
    assert numbers == sorted(set(numbers))
    names = {p.name[:2]: p.name for p in STEPS}
    # repository before core, core before Hermes, Hermes before profiles, units before the launcher, launcher before the bringup
    order = ["20", "30", "40", "41", "42", "50", "60", "61", "62", "70"]
    assert [n for n in order if n in names] == order
    assert numbers.index(20) < numbers.index(30) < numbers.index(40) < numbers.index(42) < numbers.index(60) < numbers.index(70)


def test_no_step_holds_a_secret_or_a_personal_value():
    for path in SCRIPTS:
        text = path.read_text()
        assert not re.search(r"(?i)(password|token|secret|api[_-]?key)\s*=\s*['\"][A-Za-z0-9_\-]{12,}['\"]", text), path.name
        assert "@gmail.com" not in text and not re.search(r"/home/(?!fleet\b|fleetops\b|node\b|user\b)[a-z]", text), path.name


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
@pytest.mark.parametrize("path", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_shellcheck_finds_no_error(path):
    out = subprocess.run(["shellcheck", "-S", "error", "-x", str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-500:]


def test_the_installer_lists_all_steps_in_order():
    out = subprocess.run(["bash", str(ROOT / "install.sh"), "--list"], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0
    listed = out.stdout.split()
    assert listed == [str(p.relative_to(ROOT)) for p in STEPS]
