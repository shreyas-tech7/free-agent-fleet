"""The scans of the public export. Any hit fails the export. See D-051.

A finding names the file, the line, and the rule. It never shows the matched text, because the text may be a secret or a private name.
The deny list is a file outside both repositories, ~/.config/agent-fleet/private-terms.txt, with mode 600.
"""
import json
import pathlib
import re
import subprocess

MAX_FILE_BYTES = 2_000_000
MIN_TERM = 4
ALLOWED_EMAIL = re.compile(r"(@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|noreply\.github\.com|localhost)$|\.(?:invalid|example|test|localhost)$|^git@github\.com$)", re.I)
ALLOWED_HOMES = {"fleet", "fleetops", "node", "user", "you", "yourname", "your-name", "example", "runner", "debian", "ubuntu", "USER", "someone", "alice", "bob",
                 "name", "username", "owner", "test", "tester", "me"}

EMAIL = re.compile(r"(?<![\w.%+:/-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
HOME = re.compile(r"/home/([A-Za-z_][A-Za-z0-9_.-]*)")
WINDOWS = re.compile(r"\b[A-Za-z]:\\+Users\\+([^\\\s\"'`]+)")
RULES = [
    ("private-ip", re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b")),
    ("tailscale-ip", re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b")),
    ("tailnet-name", re.compile(r"\b[A-Za-z0-9-]+\.ts\.net\b")),
    ("slack-id", re.compile(r"\b[UWTCBG]0[A-Z0-9]{7,11}\b")),
    ("discord-id", re.compile(r"(?<![0-9A-Za-z_.:-])\d{17,20}(?![0-9A-Za-z_.])")),       # a number on its own. Digits inside a hash or a name are not an ID
    ("telegram-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}|\bxapp-[A-Za-z0-9-]{10,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----")),
]


class ScanError(Exception):
    pass


def load_terms(path):
    """The deny list. Short terms are ignored, because they match too much."""
    path = pathlib.Path(path)
    try:
        lines = path.read_text().splitlines()
    except OSError:
        raise ScanError("the deny list %s cannot be read. Run: fleetctl public terms" % path) from None
    terms = sorted({line.strip().lower() for line in lines if line.strip() and not line.startswith("#") and len(line.strip()) >= MIN_TERM})
    if not terms:
        raise ScanError("the deny list has no usable term. Run: fleetctl public terms")
    return terms


def load_allow(path):
    """Reviewed false positives. One `path|rule` pair on each line. The path may use * patterns."""
    out = []
    try:
        for line in pathlib.Path(path).read_text().splitlines():
            if line.strip() and not line.startswith("#") and "|" in line:
                out.append(tuple(part.strip() for part in line.split("|", 1)))
    except OSError:
        pass
    return out


def allowed(rel, rule, allow):
    import fnmatch
    return any(fnmatch.fnmatch(rel, pattern) and rule == r for pattern, r in allow)


def is_text(data):
    return b"\x00" not in data[:4096]


def scan_text(rel, text, terms=(), allow=()):
    """Return [(rel, line, rule)] for one file."""
    found = []
    lowered_terms = list(terms)
    for number, line in enumerate(text.splitlines(), 1):
        hits = set()
        low = line.lower()
        if any(term in low for term in lowered_terms):
            hits.add("deny-list")
        for match in EMAIL.finditer(line):
            if not ALLOWED_EMAIL.search(match.group(0)):
                hits.add("email")
        for match in HOME.finditer(line):
            if match.group(1) not in ALLOWED_HOMES:
                hits.add("home-path")
        for match in WINDOWS.finditer(line):
            if match.group(1).lower() not in ("yourname", "user", "you", "<user>", "%username%"):
                hits.add("windows-path")
        for rule, pattern in RULES:
            if pattern.search(line):
                hits.add(rule)
        for rule in sorted(hits):
            if not allowed(rel, rule, allow):
                found.append((rel, number, rule))
    return found


def scan_tree(root, terms=(), allow=()):
    """Scan every text file and every file name under root. Returns [(rel, line, rule)]. Line 0 means the name."""
    root = pathlib.Path(root)
    found = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts):
        rel = str(path.relative_to(root))
        low = rel.lower()
        if any(term in low for term in terms) and not allowed(rel, "deny-list", allow):
            found.append((rel, 0, "deny-list"))
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if not is_text(data):
            continue
        found += scan_text(rel, data.decode("utf-8", "replace"), terms, allow)
    return found


def run_gitleaks(root, runner=subprocess.run, report_path=None):
    """Run gitleaks without git history. Returns [(rel, line, rule)]. The secret text is redacted by the tool and never read."""
    import tempfile
    root = pathlib.Path(root)
    with tempfile.TemporaryDirectory() as tmp:
        report = pathlib.Path(report_path or pathlib.Path(tmp) / "gitleaks.json")
        # Run inside the tree with --source . so that file paths are relative. The allowlist of .gitleaks.toml matches relative paths.
        proc = runner(["gitleaks", "detect", "--no-git", "--source", ".", "--redact", "--no-banner", "--report-format", "json", "--report-path", str(report)],
                      capture_output=True, text=True, timeout=600, cwd=str(root))
        if proc.returncode not in (0, 1):
            raise ScanError("gitleaks failed to run (exit %d)" % proc.returncode)
        try:
            rows = json.loads(report.read_text() or "[]")
        except (OSError, ValueError):
            rows = []
    return [(str(pathlib.Path(r.get("File", "")).relative_to(root)) if str(r.get("File", "")).startswith(str(root)) else str(r.get("File", "?")).lstrip("./"),
             int(r.get("StartLine", 0)), "gitleaks:" + str(r.get("RuleID", "?"))) for r in rows]


def run_osv(root, runner=subprocess.run):
    """Run OSV-Scanner on the lock files and requirement files of the tree. Returns [(rel, 0, 'osv:<package>')]."""
    root = pathlib.Path(root)
    proc = runner(["osv-scanner", "scan", "source", "-r", "--format", "json", str(root)], capture_output=True, text=True, timeout=900)
    if proc.returncode not in (0, 1):
        raise ScanError("osv-scanner failed to run (exit %d)" % proc.returncode)
    text = proc.stdout
    try:
        data = json.loads(text[text.index("{"):]) if "{" in text else {}
    except ValueError:
        raise ScanError("osv-scanner gave no JSON") from None
    found = []
    for result in data.get("results", []):
        source = str((result.get("source") or {}).get("path", "?"))
        rel = source[len(str(root)) + 1:] if source.startswith(str(root)) else source
        for package in result.get("packages", []):
            if package.get("vulnerabilities"):
                found.append((rel, 0, "osv:%s" % (package.get("package") or {}).get("name", "?")))
    return found


def summarize(findings):
    """{rule: count}"""
    out = {}
    for _rel, _line, rule in findings:
        out[rule] = out.get(rule, 0) + 1
    return dict(sorted(out.items()))
