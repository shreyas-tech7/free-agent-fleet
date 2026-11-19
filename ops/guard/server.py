"""The injection screen. A local service on 127.0.0.1:8099.

  POST /scan  {"text": "..."}  ->  {"label": "SAFE"|"INJECTION", "score": 0.0-1.0, "mode": "...", "reasons": [...]}
  POST /scan_blocks {"blocks": ["...", ...]}  ->  {"results": [{"label", "score", "reasons"}, ...]}  (one result per block)
  GET  /health                 ->  {"status": "OK"|"DEGRADED", "mode": "model+rules"|"rules", ...}

The score is the highest of the model score and the rule score.
If the model fails to load, the service uses the rule checks only and reports DEGRADED.
The service never logs the scanned text.
"""
import pathlib
import threading

from ops.fleetlib import config, httpd, log, paths
from ops.guard import rules

LOG = log.get("guard")
HARD_MAX_CHARS = 300_000      # the longest text of one /scan call. The longest post that the Publisher allows has 100,000 characters.


class Screen:
    def __init__(self, threshold=0.90, max_chars=20000, classifier=None, model_id="", force_rules_only=False,
                 block_threshold=0.98, hard_max_chars=HARD_MAX_CHARS):
        self.threshold = threshold
        self.block_threshold = block_threshold
        self.max_chars = max_chars
        self.hard_max_chars = max(int(hard_max_chars), 1000)
        self.classifier = None if force_rules_only else classifier
        self.model_id = model_id
        self.lock = threading.Semaphore(1)       # one model run at a time protects the CPU
        self.load_error = "" if (classifier or force_rules_only) else "no classifier"
        self.recent_errors = 0                   # consecutive model failures

    @property
    def mode(self):
        return "model+rules" if self.classifier is not None else "rules"

    @property
    def status(self):
        return "OK" if self.classifier is not None and self.recent_errors < 3 else "DEGRADED"

    def scan(self, text):
        text = text or ""
        rule_score, reasons = rules.scan(text)
        score = rule_score
        mode = "rules"
        if self.classifier is not None:
            with self.lock:
                try:
                    model_score = self.classifier.injection_score(text)
                    self.recent_errors = 0
                    score = max(score, model_score)
                    mode = "model+rules"
                    if model_score >= self.threshold:
                        reasons = reasons + ["model"]
                except Exception as exc:
                    # The rules still decide. The service reports DEGRADED after three failures in a row.
                    self.recent_errors += 1
                    LOG.error("model_failed", error=type(exc).__name__)
                    reasons = reasons + ["model_error"]
        label = "INJECTION" if score >= self.threshold else "SAFE"
        return {"label": label, "score": round(score, 4), "mode": mode, "reasons": sorted(set(reasons))}


    def scan_blocks(self, blocks):
        """Scan many short blocks, such as the paragraphs of a web page. Each block is judged alone.

        Rules decide first. The model threshold for blocks is higher than for a whole text,
        because a long page has many blocks and one odd block must not condemn the page.
        Blocks shorter than 25 characters get the rules only.
        """
        results = []
        for block in blocks:
            block = block or ""
            rule_score, reasons = rules.scan(block)
            score = rule_score
            if self.classifier is not None and len(block.strip()) >= 25:
                with self.lock:
                    try:
                        model_score = self.classifier.injection_score(block)
                        self.recent_errors = 0
                        score = max(score, model_score)
                        if model_score >= self.block_threshold:
                            reasons = reasons + ["model"]
                    except Exception as exc:
                        self.recent_errors += 1
                        LOG.error("model_failed", error=type(exc).__name__)
                        reasons = reasons + ["model_error"]
            flagged = rule_score >= 1.0 or ("model" in reasons)
            results.append({"label": "INJECTION" if flagged else "SAFE", "score": round(score, 4),
                            "reasons": sorted(set(reasons))})
        return results


def load_screen(cfg):
    threshold = float(config.get(cfg, "guard.threshold", 0.90))
    max_chars = int(config.get(cfg, "guard.max_chars", 20000))
    model_id = config.get(cfg, "guard.model_id", "")
    force = bool(config.get(cfg, "guard.force_rules_only", False))
    classifier = None
    error = ""
    model_dir = paths.ops_state_dir() / "guard" / "model"
    if not force:
        try:
            from ops.guard.model import OnnxClassifier
            classifier = OnnxClassifier(model_dir).load()
            classifier.injection_score("warm up")
        except Exception as exc:
            error = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            classifier = None
    block_threshold = float(config.get(cfg, "guard.block_threshold", 0.98))
    screen = Screen(threshold, max_chars, classifier, model_id, force, block_threshold,
                    int(config.get(cfg, "guard.hard_max_chars", HARD_MAX_CHARS)))
    screen.load_error = error
    return screen


def routes(screen):
    def scan(req):
        body = req.json()
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise httpd.HttpError(400, "the body must be {\"text\": \"...\"}")
        if len(text) > screen.hard_max_chars:
            # One very long text would hold the one model slot for minutes. Every caller treats an error as "not safe".
            LOG.warn("scan_refused", chars=len(text), limit=screen.hard_max_chars)
            raise httpd.HttpError(413, "the text is longer than %d characters" % screen.hard_max_chars)
        result = screen.scan(text)
        LOG.info("scan", chars=len(text), label=result["label"], score=result["score"], mode=result["mode"])
        return 200, result

    def scan_blocks(req):
        body = req.json()
        blocks = body.get("blocks") if isinstance(body, dict) else None
        if not isinstance(blocks, list) or not all(isinstance(b, str) for b in blocks) or len(blocks) > 1000:
            raise httpd.HttpError(400, "the body must be {\"blocks\": [up to 1000 strings]}")
        results = screen.scan_blocks([b[:4000] for b in blocks])
        LOG.info("scan_blocks", blocks=len(blocks), flagged=sum(1 for r in results if r["label"] != "SAFE"))
        return 200, {"results": results, "mode": screen.mode}

    def health(_req):
        info = {"status": screen.status, "mode": screen.mode, "threshold": screen.threshold, "model": screen.model_id}
        if screen.load_error:
            info["detail"] = screen.load_error
        return 200, info

    return {("POST", "/scan"): scan, ("POST", "/scan_blocks"): scan_blocks, ("GET", "/health"): health}


def main():
    cfg = config.load()
    screen = load_screen(cfg)
    if screen.status == "DEGRADED":
        LOG.warn("degraded", detail=screen.load_error or "rules only")
    server = httpd.JsonServer("127.0.0.1", config.port(cfg, "guard"), routes(screen), max_body=2_000_000, log=LOG)
    LOG.info("started", port=server.port, mode=screen.mode)
    server.serve_forever()


if __name__ == "__main__":
    main()
