"""Print one config value for shell scripts.  Usage: python3 -m ops.fleetlib.cfgget KEY [DEFAULT]

Lists and mappings print as JSON. Booleans print as true or false.
"""
import json
import sys

from . import config


def main(argv):
    if not argv:
        print("usage: cfgget KEY [DEFAULT]", file=sys.stderr)
        return 2
    value = config.get(config.load(), argv[0], argv[1] if len(argv) > 1 else None)
    if value is None:
        return 1
    if isinstance(value, bool):
        print("true" if value else "false")
    elif isinstance(value, (list, dict)):
        print(json.dumps(value))
    else:
        print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
