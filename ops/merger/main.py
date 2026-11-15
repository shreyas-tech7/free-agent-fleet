"""Entry point of the merger timer: python3 -m ops.merger.main [--ignore-pause] [--simulate-health-failure]"""
import argparse
import json
import os
import sys

from ops.merger import runner


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ignore-pause", action="store_true", help="run even when the fleet is paused (the dry run uses this)")
    ap.add_argument("--simulate-health-failure", action="store_true", help="make the health check fail, to test the rollback")
    args = ap.parse_args(argv)
    if os.geteuid() != 0 and os.environ.get("FLEET_TESTING") != "1":
        print("The merger must run as root.", file=sys.stderr)
        return 2
    os.umask(0o022)
    os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"      # a root process uses a fixed PATH, runuser lives in /usr/sbin
    merger = runner.Merger(ignore_pause=args.ignore_pause, force_health_failure=args.simulate_health_failure)
    print(json.dumps(merger.run_once(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
