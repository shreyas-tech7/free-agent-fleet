"""fleetctl catalog: read the tool catalog. It changes nothing and needs no root."""
from ops.fleetlib import catalog


def cmd_list(args, console):
    entries = catalog.load()
    wanted = getattr(args, "rating", None)
    rows = [e for e in entries if not wanted or e["rating"] == wanted]
    console.say("%-30s %-7s %-10s %-13s %s" % ("NAME", "RATING", "STATUS", "KIND", "USED BY"))
    for e in sorted(rows, key=lambda e: (catalog.RATINGS.index(e["rating"]), e["name"])):
        console.say("%-30s %-7s %-10s %-13s %s" % (e["name"], e["rating"], e["status"], e["kind"], ", ".join(e["used_by"]) or "-"))
    console.say("")
    console.say("%d of %d entries. Run: fleetctl catalog show NAME" % (len(rows), len(entries)))
    return 0


def cmd_show(args, console):
    entry = catalog.find(catalog.load(), args.name)
    if entry is None:
        console.say("No entry for %s. An unknown source is not rated. It needs the full install pipeline." % args.name)
        return 1
    for key in ("name", "url", "kind", "license", "rating", "status", "pin"):
        if entry.get(key):
            console.say("%-8s %s" % (key + ":", entry[key]))
    console.say("%-8s %s" % ("used by:", ", ".join(entry["used_by"]) or "no agent"))
    console.say("%-8s %s" % ("why:", entry["why"]))
    console.say("%-8s %s" % ("next:", entry["next"]))
    return 0


def cmd_check(args, console):
    try:
        entries = catalog.load()
    except catalog.CatalogError as exc:
        console.say("FAIL: %s" % exc)
        return 1
    console.say("OK: %d entries" % len(entries))
    return 0


def dispatch(args, console, runner=None):
    action = getattr(args, "catalog_command", None) or "list"
    try:
        if action == "list":
            return cmd_list(args, console)
        if action == "show":
            return cmd_show(args, console)
        if action == "check":
            return cmd_check(args, console)
    except catalog.CatalogError as exc:
        console.say("Error: %s" % exc)
        return 1
    return None
