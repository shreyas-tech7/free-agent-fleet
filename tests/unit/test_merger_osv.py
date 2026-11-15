"""The offline vulnerability databases of the gate. The gate container runs as nobody and must be able to read them. See D-044."""
import os
import stat
import subprocess

import pytest

from ops.merger import container


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def fake_download(files=("osv-scalibr/npm/all.zip", "osv-scalibr/PyPI/all.zip")):
    """A runner that acts like osv-scanner with --download-offline-databases. The folder comes from the environment setting in the command."""
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        target = next(a.split("=", 1)[1] for a in cmd if a.startswith("OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY="))
        for rel in files:
            path = os.path.join(target, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(b"zip")
            os.chmod(path, 0o600)
        return Done()
    runner.calls = calls
    return runner


def test_a_fresh_cache_is_made_readable_even_when_the_top_folder_is_mode_750(tmp_path):
    cache = tmp_path / "osv-cache"
    (cache / "osv-scalibr" / "npm").mkdir(parents=True)
    (cache / "osv-scalibr" / "npm" / "all.zip").write_bytes(b"zip")
    (cache / ".refreshed").write_text("1")
    os.chmod(cache, 0o750)
    os.chmod(cache / "osv-scalibr", 0o700)
    os.chmod(cache / "osv-scalibr" / "npm" / "all.zip", 0o600)
    out = container.refresh_osv(cache, runner=fake_download(), now=lambda: cache.joinpath(".refreshed").stat().st_mtime + 60)
    assert out == "fresh"
    assert mode(cache) == 0o755 and mode(cache / "osv-scalibr") == 0o755 and mode(cache / "osv-scalibr" / "npm" / "all.zip") == 0o644


def test_a_stale_cache_is_downloaded_made_readable_and_stamped(tmp_path):
    cache = tmp_path / "osv-cache"
    runner = fake_download()
    out = container.refresh_osv(cache, runner=runner, now=lambda: 1_800_000_000)
    assert out == "refreshed" and len(runner.calls) == 1
    cmd = runner.calls[0]
    assert "--download-offline-databases" in cmd and "--offline-vulnerabilities" in cmd and "--no-resolve" in cmd
    assert mode(cache) == 0o755 and mode(cache / "osv-scalibr" / "PyPI" / "all.zip") == 0o644
    assert (cache / ".refreshed").read_text() == "1800000000"


def test_an_old_stamp_starts_a_new_download_and_a_young_one_does_not(tmp_path):
    cache = tmp_path / "osv-cache"
    t0 = 1_800_000_000
    container.refresh_osv(cache, runner=fake_download(), now=lambda: t0)
    os.utime(cache / ".refreshed", (t0, t0))                    # the stamp file has the time of the fake clock
    runner = fake_download()
    later = t0 + container.OSV_MAX_AGE + 5
    assert container.refresh_osv(cache, runner=runner, now=lambda: later) == "refreshed" and len(runner.calls) == 1
    os.utime(cache / ".refreshed", (later, later))
    quiet = fake_download()
    assert container.refresh_osv(cache, runner=quiet, now=lambda: later + 60) == "fresh" and quiet.calls == []


def test_a_download_that_makes_no_database_is_reported_with_the_reason_and_not_stamped(tmp_path):
    cache = tmp_path / "osv-cache"

    def broken(cmd, **kw):
        return Done(1, "", "could not reach the database host")
    out = container.refresh_osv(cache, runner=broken, now=lambda: 1_800_000_000)
    assert out.startswith("failed:") and "could not reach" in out and not (cache / ".refreshed").exists()


def test_make_readable_sets_directories_to_755_and_files_to_644(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "f").write_text("x")
    for p in (tmp_path, tmp_path / "a", tmp_path / "a" / "b"):
        os.chmod(p, 0o700)
    os.chmod(tmp_path / "a" / "b" / "f", 0o600)
    container.make_readable(tmp_path)
    assert [mode(p) for p in (tmp_path, tmp_path / "a", tmp_path / "a" / "b", tmp_path / "a" / "b" / "f")] == [0o755, 0o755, 0o755, 0o644]
