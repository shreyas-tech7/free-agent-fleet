"""Policy: gitleaks finds no secret in the repository."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_gitleaks_finds_nothing():
    binary = shutil.which("gitleaks")
    if not binary:
        pytest.fail("gitleaks is not installed. Run install/19-tools.sh.")
    # Scan the files on disk. When the folder is a git repo, scan its history too.
    # Scan from the repository root with a relative path. The allowlist in .gitleaks.toml matches relative paths.
    cmd = [binary, "detect", "--source", ".", "--no-banner", "--redact", "--exit-code", "1"]
    if (ROOT / ".gitleaks.toml").exists():
        cmd += ["--config", ".gitleaks.toml"]
    if not (ROOT / ".git").exists():
        cmd.append("--no-git")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert out.returncode == 0, "gitleaks found a possible secret:\n" + out.stdout[-1500:] + out.stderr[-500:]


def test_gitleaks_would_catch_a_planted_secret(tmp_path):
    binary = shutil.which("gitleaks")
    if not binary:
        pytest.fail("gitleaks is not installed")
    planted = tmp_path / "x.txt"
    planted.write_text('token = "ghp_%s"\n' % ("aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5"))
    out = subprocess.run([binary, "detect", "--source", str(tmp_path), "--no-git", "--no-banner", "--redact"],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode != 0, "the scanner did not flag a planted token"
