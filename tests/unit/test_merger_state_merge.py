"""The merger resolves a conflict that touches only state files (projects/*/STATE.md). See D-032.

These tests use real git. They prove the old behavior (the branch is rejected) and the fix.
"""
import os
import pathlib
import subprocess

import pytest

from ops.merger import gitops

OLD = "---\nslug: p\nstage: discover\nmissed_gates: 0\nmetrics:\n  candidates: 0\n---\n# p\n"


def sh(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


def put(repo, rel, text):
    p = pathlib.Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo, msg):
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", msg)
    return sh(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    put(r, "README.md", "hello\n")
    commit(r, "init")
    return r


def two_sides(repo, live_files, agent_files):
    """A live commit and an agent commit, both built on the same base. Returns (live sha, agent sha)."""
    base = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "-b", "agent", base)
    for rel, text in agent_files.items():
        put(repo, rel, text)
    agent = commit(repo, "agent")
    sh(repo, "checkout", "-q", "main")
    for rel, text in live_files.items():
        put(repo, rel, text)
    live = commit(repo, "live")
    return live, agent


def tree_text(repo, rev, path):
    return sh(repo, "show", "%s:%s" % (rev, path))


def test_the_old_behavior_rejected_an_add_add_conflict_on_a_state_file(repo):
    live, agent = two_sides(repo, {"projects/p/STATE.md": OLD}, {"projects/p/STATE.md": "---\nproject: p\n---\nagent\n"})
    sha, reason = gitops.merge_commit(repo, live, agent, "m", state_globs=())
    assert sha is None and "does not merge cleanly" in reason


def test_an_add_add_conflict_on_a_state_file_takes_the_agent_version(repo):
    agent_text = "---\nproject: p\nbrief:\n  achieved: ok\n---\nagent\n"
    live, agent = two_sides(repo, {"projects/p/STATE.md": OLD, "docs/live.md": "live only\n"},
                            {"projects/p/STATE.md": agent_text, "docs/agent.md": "agent only\n"})
    sha, reason = gitops.merge_commit(repo, live, agent, "merge it")
    assert reason is None and sha
    assert tree_text(repo, sha, "projects/p/STATE.md") == agent_text.rstrip("\n")
    assert tree_text(repo, sha, "docs/live.md") == "live only" and tree_text(repo, sha, "docs/agent.md") == "agent only"
    assert sh(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:] == [live, agent]      # a real merge of both sides


def test_a_content_conflict_keeps_disjoint_live_edits_and_takes_the_agent_for_conflicting_lines(repo):
    base_text = "a: 1\nb: 1\nc: 1\nd: 1\ne: 1\nf: 1\ng: 1\nh: 1\n"
    put(repo, "projects/p/STATE.md", base_text)
    commit(repo, "state")
    live_text = base_text.replace("b: 1", "b: LIVE").replace("h: 1", "h: LIVE")
    agent_text = base_text.replace("b: 1", "b: AGENT").replace("e: 1", "e: AGENT")
    live, agent = two_sides(repo, {"projects/p/STATE.md": live_text}, {"projects/p/STATE.md": agent_text})
    sha, reason = gitops.merge_commit(repo, live, agent, "m")
    assert reason is None
    merged = tree_text(repo, sha, "projects/p/STATE.md").splitlines()
    assert "b: AGENT" in merged and "b: LIVE" not in merged        # conflicting line: the agent wins
    assert "h: LIVE" in merged and "e: AGENT" in merged            # disjoint edits from both sides stay


def test_a_conflict_in_a_code_file_still_rejects(repo):
    put(repo, "ops/x.py", "v = 0\n")
    commit(repo, "x")
    live, agent = two_sides(repo, {"ops/x.py": "v = 1\n"}, {"ops/x.py": "v = 2\n"})
    sha, reason = gitops.merge_commit(repo, live, agent, "m")
    assert sha is None and "does not merge cleanly" in reason


def test_a_state_conflict_together_with_a_code_conflict_rejects(repo):
    put(repo, "ops/x.py", "v = 0\n")
    commit(repo, "x")
    live, agent = two_sides(repo, {"ops/x.py": "v = 1\n", "projects/p/STATE.md": OLD}, {"ops/x.py": "v = 2\n", "projects/p/STATE.md": "agent\n"})
    assert gitops.merge_commit(repo, live, agent, "m")[0] is None


def test_the_state_glob_does_not_cross_directories(repo):
    live, agent = two_sides(repo, {"projects/p/deep/STATE.md": OLD, "STATE.md": OLD}, {"projects/p/deep/STATE.md": "agent\n", "STATE.md": "agent\n"})
    assert gitops.merge_commit(repo, live, agent, "m")[0] is None
    assert gitops._path_matches("projects/p/STATE.md", "projects/*/STATE.md")
    assert not gitops._path_matches("projects/p/q/STATE.md", "projects/*/STATE.md")
    assert not gitops._path_matches("x/projects/p/STATE.md", "projects/*/STATE.md")


def test_a_link_or_an_executable_state_file_is_not_resolved(repo):
    base = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "-b", "agent", base)
    p = pathlib.Path(repo) / "projects/p"
    p.mkdir(parents=True)
    os.symlink("/etc/passwd", p / "STATE.md")
    agent = commit(repo, "link")
    sh(repo, "checkout", "-q", "main")
    put(repo, "projects/p/STATE.md", OLD)
    live = commit(repo, "live")
    assert gitops.merge_commit(repo, live, agent, "m")[0] is None


def test_a_deleted_state_file_on_one_side_is_not_resolved(repo):
    put(repo, "projects/p/STATE.md", OLD)
    commit(repo, "state")
    base = sh(repo, "rev-parse", "HEAD")
    sh(repo, "checkout", "-q", "-b", "agent", base)
    put(repo, "projects/p/STATE.md", OLD.replace("discover", "validate"))
    agent = commit(repo, "edit")
    sh(repo, "checkout", "-q", "main")
    (pathlib.Path(repo) / "projects/p/STATE.md").unlink()
    live = commit(repo, "delete")
    assert gitops.merge_commit(repo, live, agent, "m")[0] is None


def test_a_huge_state_file_is_not_resolved(repo):
    live, agent = two_sides(repo, {"projects/p/STATE.md": OLD}, {"projects/p/STATE.md": "x\n" * (gitops.STATE_MAX_BYTES // 2 + 10)})
    assert gitops.merge_commit(repo, live, agent, "m")[0] is None


def test_a_clean_merge_is_untouched_by_the_state_rule(repo):
    live, agent = two_sides(repo, {"docs/a.md": "a\n"}, {"projects/p/STATE.md": OLD})
    sha, reason = gitops.merge_commit(repo, live, agent, "m")
    assert reason is None and tree_text(repo, sha, "projects/p/STATE.md") == OLD.rstrip("\n")


def test_a_branch_with_no_new_commits_is_still_reported(repo):
    base = sh(repo, "rev-parse", "HEAD")
    put(repo, "docs/a.md", "a\n")
    live = commit(repo, "live")
    sha, reason = gitops.merge_commit(repo, live, base, "m")
    assert sha is None and reason == "the branch has no new commits"
