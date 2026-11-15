"""The weekly portfolio log. A systemd timer starts it every Sunday at 18:00 in the owner's time zone. See D-048.

It reads the facts of the last 7 days. It asks fleet-worker for a short entry in the house style.
With no model, or when the model text breaks a hard rule, plain code writes the entry.
It appends the entry to PORTFOLIO_LOG.md, posts it to Slack #portfolio (not to the owner's chats), and sends one fleet event.
`fleetctl portfolio sync` copies the log into docs/PORTFOLIO_LOG.md of the private repository.
"""
import json
import pathlib
import sys

from ops.briefing import collect, render
from ops.briefing import main as briefing
from ops.fleetlib import config, events, housestyle, jsonio, log, paths, schema

LOG = log.get("portfolio")
WEEK = 7 * 86400
MAX_WORDS = 120
MODEL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["entry"],
                "properties": {"entry": {"type": "string", "minLength": 20, "maxLength": 1200}}}


def log_file():
    return paths.ops_state_dir() / "portfolio" / "PORTFOLIO_LOG.md"


def week_facts(cfg, now=None):
    """What the fleet did in the last 7 days, as numbers and project stages."""
    import time
    now = now or time.time()
    since = now - WEEK
    ops = paths.ops_state_dir()
    today = collect.collect(cfg, now)
    return {
        "week_ending": today["date"],
        "merged": collect.count_log(ops / "merger" / "merges.log", since, status="merged"),
        "rolled_back": collect.count_log(ops / "merger" / "merges.log", since, status="rolled_back"),
        "deploys_dry_run": collect.count_log(ops / "deployer" / "deploys.log", since, status="dry_run"),
        "deploys_live": collect.count_log(ops / "deployer" / "deploys.log", since, status="deployed"),
        "posts_dry_run": collect.count_log(ops / "publisher" / "posts.log", since, status="dry_run"),
        "posts_sent": collect.count_log(ops / "publisher" / "posts.log", since, status="sent"),
        "projects": {slug: {"stage": st.get("stage", "unknown")} for slug, st in today["projects"].items()},
        "tickets_open": today["tickets_open"],
        "paused": today["paused"],
    }


def plain_entry(f):
    parts = ["The fleet merged %d changes and rolled back %d." % (f["merged"], f["rolled_back"])]
    if f["deploys_live"]:
        parts.append("It deployed %d times." % f["deploys_live"])
    else:
        parts.append("It prepared %d deploys in dry-run and deployed nothing." % f["deploys_dry_run"])
    parts.append("It sent %d posts and held %d in dry-run." % (f["posts_sent"], f["posts_dry_run"]))
    if f["projects"]:
        parts.append("Projects: %s." % ", ".join("%s at %s" % (slug, p["stage"]) for slug, p in sorted(f["projects"].items())))
    else:
        parts.append("No project is active.")
    parts.append("Open tickets: %d." % f["tickets_open"])
    if f["paused"]:
        parts.append("The fleet is paused.")
    return " ".join(parts)


def model_prompt(f):
    return ("Write the weekly portfolio log entry for the owner of a small online business that AI agents run. "
            "Use only the facts below. Do not invent numbers, links, customers, or claims. Report misses first. "
            "House style: active voice, plain words, short and long sentences mixed, an honest tone. "
            "No dashes, no semicolons, no exclamation marks. Never write: it is important to note, testament, pivotal, seamless, delve. "
            "%d words or fewer. Answer with JSON only: {\"entry\": \"...\"}.\n\nFacts:\n%s" % (MAX_WORDS, json.dumps(f, sort_keys=True)))


def entry_text(f, ask_model=None):
    """Return (text, source). The model text is fixed for dashes and semicolons. A banned phrase or an overlong entry means plain code."""
    if ask_model is not None:
        try:
            raw = ask_model(model_prompt(f)) or ""
            obj = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
            if not schema.validate(obj, MODEL_SCHEMA):
                text = housestyle.fix(" ".join(obj["entry"].split()))
                if not [p for p in housestyle.check(text) if p.startswith("banned phrase")] and render.words(text) <= MAX_WORDS:
                    return text, "model"
        except Exception:                              # a bad answer is not an error. Plain code writes the entry.
            pass
    return plain_entry(f), "plain"


def append(date, text, path=None):
    """Add the entry to the log. A second run for the same week changes nothing. Returns True when it wrote."""
    path = pathlib.Path(path or log_file())
    head = "## Week ending %s" % date
    old = path.read_text() if path.exists() else "# Portfolio log\n\nWhat the fleet shipped each week. Newest entries come last.\n"
    if head in old:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonio.atomic_write(path, old.rstrip("\n") + "\n\n%s\n\n%s\n" % (head, text), mode=0o644)
    return True


def main(argv=None):
    cfg = config.load()
    facts = week_facts(cfg)
    text, source = entry_text(facts, ask_model=lambda p: briefing.ask_router(cfg, p))
    wrote = append(facts["week_ending"], text)
    result = {"source": source, "words": render.words(text), "wrote": wrote}
    if wrote:
        result["slack"] = briefing.send_chat(cfg, "*Portfolio, week ending %s*\n%s" % (facts["week_ending"], text), slack_channel="portfolio", chat=False)
        events.emit("portfolio.weekly", "The portfolio log for the week ending %s is written (%d words, %s)." % (facts["week_ending"], result["words"], source),
                    source="briefing", fields={"words": result["words"], "source": source})
    LOG.info("portfolio", **{k: str(v)[:80] for k, v in result.items()})
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
