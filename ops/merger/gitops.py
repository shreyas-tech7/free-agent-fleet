"""Git helpers for the merger. The merger runs as root, so it never trusts the agents' repository.

How agent work reaches the merger:
  1. A command that runs as the user `fleet` bundles the branches agent/* into one file.
     Any hostile config in the agents' clone can only run code as `fleet`, never as root.
  2. Root copies the file to a root-only folder and fetches from the file into a staging repo.
     A bundle holds only objects and refs. It holds no config and no hooks.
  3. Root checks the objects (transfer.fsckObjects) and builds the merge in the staging repo
     with plumbing commands. It never checks out agent code on the host.
"""
import fnmatch
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

MAX_BUNDLE_BYTES = 80 * 1024 * 1024
BRANCH_RE = re.compile(r"^agent/[a-z0-9][a-z0-9_-]{0,30}/[A-Za-z0-9][A-Za-z0-9._/-]{0,80}$")
MERGER_IDENTITY = ["-c", "user.name=agent-fleet merger", "-c", "user.email=merger@agent-fleet.invalid", "-c", "commit.gpgsign=false"]
SAFE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null"}


class GitError(Exception):
    pass


def git(repo, *args, check=True, input_text=None, timeout=120, extra_env=None, identity=False):
    """Run git against a root-owned repo. Hooks and system config are off. Returns stdout."""
    env = dict(SAFE_ENV)
    env.update(extra_env or {})
    argv = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=always", "-c", "transfer.fsckObjects=true",
            "-c", "gc.auto=0", "-c", "core.fsmonitor=false"]
    if identity:
        argv += MERGER_IDENTITY
    if repo is not None:
        argv += ["--git-dir", str(repo)] if str(repo).endswith(".git") else ["-C", str(repo)]
    argv += list(args)
    proc = subprocess.run(argv, capture_output=True, text=True, input=input_text, env=env, timeout=timeout)  # nosemgrep: dangerous-subprocess-use-audit
    if check and proc.returncode != 0:
        raise GitError("git %s failed: %s" % (args[0], (proc.stderr or proc.stdout).strip()[-300:]))
    return proc.stdout if check else (proc.returncode, proc.stdout, proc.stderr)


def make_bundle_as_fleet(work_clone, out_file, runner=subprocess.run, user="fleet"):
    """Ask the user `fleet` to bundle agent/* branches. Returns True when a bundle exists."""
    cmd = ["git", "-C", str(work_clone), "bundle", "create", str(out_file), "--glob=refs/heads/agent/*"]
    if os.geteuid() == 0:
        env = ["env", "-i", "HOME=/home/%s" % user, "PATH=/usr/local/bin:/usr/bin:/bin", "GIT_TERMINAL_PROMPT=0"]
        cmd = ["runuser", "-u", user, "--"] + env + cmd
    proc = runner(cmd, capture_output=True, text=True, timeout=300)
    return proc.returncode == 0 and pathlib.Path(out_file).exists()


def import_bundle(staging, bundle_src, workdir):
    """Copy the bundle safely, then fetch its agent/* branches into the staging repo. Returns {branch: sha}."""
    staging, workdir = pathlib.Path(staging), pathlib.Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    local = workdir / "incoming.bundle"
    fd = os.open(str(bundle_src), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat_is_regular(st) or st.st_size > MAX_BUNDLE_BYTES:
            raise GitError("the bundle is not a regular file or is too large")
        with os.fdopen(fd, "rb") as src, open(local, "wb") as dst:
            fd = None
            shutil.copyfileobj(src, dst)
    finally:
        if fd is not None:
            os.close(fd)
    os.chmod(local, 0o600)
    if not (staging / "HEAD").exists():
        subprocess.run(["git", "init", "--bare", "--quiet", str(staging)], check=True, env=SAFE_ENV, capture_output=True)
    git(staging, "bundle", "verify", str(local), check=False)
    out = git(staging, "bundle", "list-heads", str(local))
    heads = {}
    for line in out.splitlines():
        sha, _, ref = line.partition(" ")
        if ref.startswith("refs/heads/") and BRANCH_RE.match(ref[len("refs/heads/"):]) and re.fullmatch(r"[0-9a-f]{40}", sha):
            heads[ref[len("refs/heads/"):]] = sha
    if heads:
        refspecs = ["+refs/heads/%s:refs/agent/%s" % (b, b) for b in heads]
        git(staging, "fetch", "--quiet", "--no-tags", str(local), *refspecs, timeout=300)
    local.unlink()
    return heads


def stat_is_regular(st):
    import stat
    return stat.S_ISREG(st.st_mode)


def rev_parse(repo, rev):
    return git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}").strip()


def is_ancestor(repo, a, b):
    rc, _o, _e = git(repo, "merge-base", "--is-ancestor", a, b, check=False)
    return rc == 0


def fetch_into(repo, source_repo, *refspecs, trust_source=False):
    """Fetch refs from another repository. With trust_source, git may read a local source that another user owns.
    Only fleetctl update uses this, for a folder that the owner named. The owner is allowed to trust it.

    Git runs `upload-pack` as a child process for a local source. That child ignores safe.directory from the
    environment and from `-c`. So the setting goes on the command that git runs for the remote side. See D-035."""
    args = []
    if trust_source and str(source_repo).startswith("/"):
        src = str(source_repo).rstrip("/")
        trusted = [src] if src.endswith("/.git") else [src, src + "/.git"]
        args = ["--upload-pack", "git " + " ".join("-c safe.directory=%s" % shlex.quote(p) for p in trusted) + " upload-pack"]
    git(repo, "fetch", "--quiet", "--no-tags", *args, str(source_repo), *refspecs, timeout=300)


# State files. Both an agent and the owner's tools (the pivot service) write them, so the two sides can
# collide. A conflict in only these files is resolved for the agent, and the conflicting lines come from the
# agent. A conflict in any other file still rejects the branch. See D-032.
STATE_GLOBS = ("projects/*/STATE.md",)
STATE_MAX_BYTES = 200_000


def _path_matches(path, glob):
    """Match segment by segment, so `*` never crosses a slash."""
    a, b = path.split("/"), glob.split("/")
    return len(a) == len(b) and all(fnmatch.fnmatchcase(x, y) for x, y in zip(a, b))


def _tree_entry(repo, rev, path):
    """(mode, object id) of a path in a commit. None when the path is not there."""
    out = git(repo, "ls-tree", "-z", rev, "--", path)
    head = out.split("\t", 1)[0].split()
    return (head[0], head[2]) if len(head) == 3 else None


def _blob_bytes(repo, oid):
    argv = ["git", "-c", "core.hooksPath=/dev/null"] + (["--git-dir", str(repo)] if str(repo).endswith(".git") else ["-C", str(repo)])
    proc = subprocess.run(argv + ["cat-file", "blob", oid], capture_output=True, env=dict(SAFE_ENV), timeout=60)  # nosemgrep: dangerous-subprocess-use-audit
    return proc.stdout if proc.returncode == 0 else None


def _merge_state_file(repo, base, cand, path):
    """Merge one state file with `git merge-file --theirs`. Returns a blob id, or None when it is not safe to resolve."""
    rc, mb_out, _err = git(repo, "merge-base", base, cand, check=False)
    merge_base = mb_out.strip() if rc == 0 else ""
    ours, theirs = _tree_entry(repo, base, path), _tree_entry(repo, cand, path)
    old = _tree_entry(repo, merge_base, path) if merge_base else None
    if not ours or not theirs or ours[0] != "100644" or theirs[0] != "100644" or (old and old[0] != "100644"):
        return None                      # deleted on one side, a link, or an executable: do not resolve
    with tempfile.TemporaryDirectory() as tmp:
        names = {}
        for label, entry in (("ours", ours), ("base", old), ("theirs", theirs)):
            data = b"" if entry is None else _blob_bytes(repo, entry[1])
            if data is None or len(data) > STATE_MAX_BYTES or b"\0" in data:
                return None
            names[label] = pathlib.Path(tmp) / label
            names[label].write_bytes(data)
        proc = subprocess.run(["git", "merge-file", "-p", "--theirs", str(names["ours"]), str(names["base"]), str(names["theirs"])],
                              capture_output=True, env=dict(SAFE_ENV), timeout=60)
    if proc.returncode != 0:
        return None
    try:
        return git(repo, "hash-object", "-w", "--stdin", input_text=proc.stdout.decode("utf-8")).strip()
    except (UnicodeDecodeError, GitError):
        return None


def _resolve_state_conflicts(repo, base, cand, conflicted_tree, names, state_globs):
    """The merge conflicts in `names`. Return a tree with each state file resolved, or None when any other file conflicts."""
    if not names or not all(any(_path_matches(n, g) for g in state_globs) for n in names):
        return None
    updates = {}
    for name in names:
        blob = _merge_state_file(repo, base, cand, name)
        if blob is None:
            return None
        updates[name] = blob
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(pathlib.Path(tmp) / "index")}
        git(repo, "read-tree", conflicted_tree, extra_env=env)
        for name, blob in updates.items():
            git(repo, "update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, name), extra_env=env)
        return git(repo, "write-tree", extra_env=env).strip()


def merge_commit(repo, base, cand, message, state_globs=STATE_GLOBS):
    """Build the merge of cand into base with plumbing only. Returns (commit_sha, None) or (None, reason).

    A conflict that touches only state files is resolved in favor of the agent. Any other conflict rejects the branch."""
    rc, out, err = git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", base, cand, check=False)
    parts = out.split("\0")
    tree = parts[0].strip()
    if rc == 1 and tree:
        resolved = _resolve_state_conflicts(repo, base, cand, tree, [p for p in parts[1:] if p], state_globs)
        if resolved is None:
            return None, "the branch does not merge cleanly with the live code"
        tree = resolved
    elif rc != 0 or not tree:
        return None, "the branch does not merge cleanly with the live code"
    if is_ancestor(repo, cand, base):
        return None, "the branch has no new commits"
    sha = git(repo, "commit-tree", tree, "-p", base, "-p", cand, "-m", message, identity=True).strip()
    return sha, None


def changed_entries(repo, base, new):
    """List of dicts: status, path, old_mode, new_mode. Renames count as a delete plus an add."""
    out = git(repo, "diff-tree", "-r", "--no-renames", "--raw", "-z", base, new)
    parts = out.split("\0")
    entries, i = [], 0
    while i < len(parts) and parts[i]:
        meta = parts[i]
        path = parts[i + 1] if i + 1 < len(parts) else ""
        m = re.match(r":(\d+) (\d+) [0-9a-f]+ [0-9a-f]+ ([A-Z])", meta)
        if m:
            entries.append({"old_mode": m.group(1), "new_mode": m.group(2), "status": m.group(3), "path": path})
        i += 2
    return entries


def read_blob(repo, rev, path, limit=2_000_000):
    rc, out, _e = git(repo, "show", "%s:%s" % (rev, path), check=False)
    return out[:limit] if rc == 0 else None


def blob_size(repo, rev, path):
    rc, out, _e = git(repo, "cat-file", "-s", "%s:%s" % (rev, path), check=False)
    return int(out.strip()) if rc == 0 and out.strip().isdigit() else None


def export_tree(repo, rev, dest):
    """Write the tree of rev to dest without running any agent code. Uses git archive and a safe tar read."""
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    env = dict(SAFE_ENV)
    with tempfile.TemporaryFile() as tmp:
        argv = ["git", "-c", "core.hooksPath=/dev/null"] + (["--git-dir", str(repo)] if str(repo).endswith(".git") else ["-C", str(repo)])
        subprocess.run(argv + ["archive", "--format=tar", rev], stdout=tmp, check=True, env=env, timeout=300)  # nosemgrep: dangerous-subprocess-use-audit
        tmp.seek(0)
        with tarfile.open(fileobj=tmp, mode="r:") as tar:
            for member in tar:
                name = os.path.normpath(member.name)
                if name.startswith("..") or os.path.isabs(name):
                    continue
                if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
                    continue
                if member.islnk():
                    continue
                member.uid = member.gid = 0
                member.mode = 0o755 if member.isdir() else ((member.mode & 0o555) | 0o444)
                tar.extract(member, dest, filter="tar")
    return dest
