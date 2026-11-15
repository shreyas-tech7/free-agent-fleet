"""The page reader. A local service on 127.0.0.1:8095.

  POST /read  {"url": "https://example.com/page", "max_chars": 20000}
    ok:      {"status": "ok", "url", "title", "markdown", "chars", "truncated", "guard": {...}, "converter"}
    blocked: {"status": "blocked", "reason": "..."}     (reason names the rule. It never holds page text.)
  GET  /health

Research agents read public pages only through this service. It fetches safely (see fetch.py),
converts the page, and scans the result with the injection screen. A page that the screen flags
goes to quarantine and its text is not returned.
"""
import hashlib
import json
import re
import threading
import time

from ops.fleetlib import config, httpc, httpd, jsonio, log, paths
from ops.reader import convert as converter
from ops.reader import fetch, netguard

LOG = log.get("reader")
DEFAULT_MAX_CHARS = 20000
HARD_MAX_CHARS = 60000
REMOVED = "[removed by the injection screen]"
BLOCK_LEN = 500
MAX_FLAGGED_SHARE = 0.30        # more flagged blocks than this and the whole page is quarantined
BATCH = 200


def split_blocks(markdown, max_len=BLOCK_LEN):
    """Cut Markdown into blocks, as the author wrote them. Long blocks are cut at sentence ends.
    Returns a list of (display_text, scan_text). The scan text has link targets removed."""
    blocks = []
    for raw in re.split(r"\n\s*\n", markdown):
        block = raw.strip()
        while len(block) > max_len:
            cut = max(block.rfind(". ", 0, max_len), block.rfind("\n", 0, max_len), block.rfind(" ", 0, max_len))
            cut = cut if cut > max_len // 3 else max_len
            blocks.append(block[:cut + 1].strip())
            block = block[cut + 1:].strip()
        if block:
            blocks.append(block)
    return [(b, re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", b)) for b in blocks]


class Reader:
    def __init__(self, fetcher, guard_url, quarantine_dir, prefer_crawl4ai=True):
        self.fetcher = fetcher
        self.guard_url = guard_url
        self.quarantine_dir = quarantine_dir
        self.prefer_crawl4ai = prefer_crawl4ai
        self.slots = threading.Semaphore(2)

    def _guard_blocks(self, scan_texts):
        """Return one verdict per block, or None when the injection screen cannot be used."""
        results = []
        for i in range(0, len(scan_texts), BATCH):
            try:
                resp = httpc.post(self.guard_url, json_body={"blocks": scan_texts[i:i + BATCH]}, timeout=300)
                if resp.status != 200:
                    return None
                part = resp.json().get("results")
                if not isinstance(part, list) or len(part) != len(scan_texts[i:i + BATCH]):
                    return None
                results += part
            except (httpc.TransportError, ValueError):
                return None
        return results

    def _quarantine(self, url, verdict, text):
        digest = hashlib.sha256((url + text[:200]).encode()).hexdigest()[:16]
        name = "%d-%s.json" % (int(time.time()), digest)
        record = {"url": url, "guard": verdict, "snippet": text[:500], "ts": int(time.time())}
        try:
            jsonio.write_json(self.quarantine_dir / name, record, mode=0o600)
        except OSError:
            pass
        return name

    def read(self, url, max_chars=DEFAULT_MAX_CHARS):
        max_chars = max(500, min(int(max_chars or DEFAULT_MAX_CHARS), HARD_MAX_CHARS))
        with self.slots:
            try:
                page = self.fetcher.fetch(url)
            except netguard.Blocked as exc:
                return {"status": "blocked", "reason": exc.reason}
            title, markdown, name = converter.convert(
                page.body, page.content_type, page.url, page.headers.get("content-type", ""), self.prefer_crawl4ai)
            if not markdown.strip():
                return {"status": "blocked", "reason": "empty_page"}
            truncated = page.truncated or len(markdown) > max_chars
            markdown = markdown[:max_chars]
            blocks = split_blocks((title + "\n\n" + markdown) if title else markdown)
            verdicts = self._guard_blocks([scan for _display, scan in blocks])
            if verdicts is None:
                return {"status": "blocked", "reason": "guard_unavailable"}
            flagged = [i for i, v in enumerate(verdicts) if v["label"] != "SAFE"]
            hidden = any("hidden_unicode" in v.get("reasons", []) for v in verdicts)
            if hidden or (blocks and len(flagged) / len(blocks) > MAX_FLAGGED_SHARE):
                worst = max(verdicts, key=lambda v: v.get("score", 0))
                ref = self._quarantine(page.url, worst, markdown)
                LOG.warn("quarantined", url=page.url, flagged=len(flagged), blocks=len(blocks), hidden=hidden, ref=ref)
                return {"status": "blocked", "reason": "quarantined",
                        "guard": {"flagged_blocks": len(flagged), "blocks": len(blocks), "hidden_unicode": hidden}}
            kept = [REMOVED if i in flagged else display for i, (display, _scan) in enumerate(blocks)]
            clean = "\n\n".join(kept)
            if flagged:
                LOG.warn("redacted", url=page.url, flagged=len(flagged), blocks=len(blocks))
            return {"status": "ok", "url": page.url, "title": title[:300], "markdown": clean, "chars": len(clean),
                    "truncated": truncated, "converter": name, "redacted": len(flagged), "blocks": len(blocks),
                    "guard": {"label": "SAFE" if not flagged else "REDACTED"}}


def build(cfg):
    denylist = netguard.load_denylist(paths.repo_root() / "config" / "reader_denylist.txt")
    ua = config.get(cfg, "reader.user_agent", "agent-fleet-reader/0.1")
    fetcher = fetch.SafeFetcher(user_agent=ua, denylist=denylist,
                                host_delay=float(config.get(cfg, "reader.host_delay_seconds", 2.0)))
    guard_url = "http://127.0.0.1:%d/scan_blocks" % config.port(cfg, "guard")
    return Reader(fetcher, guard_url, paths.ops_state_dir() / "reader" / "quarantine")


def routes(reader):
    def read(req):
        body = req.json()
        url = body.get("url") if isinstance(body, dict) else None
        if not isinstance(url, str):
            raise httpd.HttpError(400, "the body must be {\"url\": \"...\"}")
        result = reader.read(url, body.get("max_chars"))
        LOG.info("read", status=result["status"], reason=result.get("reason"))
        return 200, result

    def health(_req):
        return 200, {"status": "OK", "converter": "crawl4ai" if reader.prefer_crawl4ai else "fallback"}

    return {("POST", "/read"): read, ("GET", "/health"): health}


def main():
    cfg = config.load()
    reader = build(cfg)
    server = httpd.JsonServer("127.0.0.1", config.port(cfg, "reader"), routes(reader), max_body=20_000, log=LOG)
    LOG.info("started", port=server.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
