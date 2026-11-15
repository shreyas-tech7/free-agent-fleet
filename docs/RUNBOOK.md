# Runbook

Each section has commands that were tested on a real machine. Run them as your own user. `fleetctl` asks for `sudo` when it needs it.

## Look at the state

```bash
fleetctl status             # what runs, the pause state, keys, budget, the active project
fleetctl doctor             # check every part. Exit code 0 means all parts are OK or waiting for keys
fleetctl logs router -n 50  # the log of one service. Names: router guard searxng reader publisher deployer gateway control
fleetctl brief              # the latest daily brief
```

Doctor shows four states. OK means the part works. WAITING_FOR_KEYS means it works and waits for a key or a step of yours. DEGRADED means it works with less safety or speed. FAIL means it is broken.

## Pause

Pause stops new work. Work in progress ends at its next step.

```bash
fleetctl pause              # or send /pause to the control bot
fleetctl resume             # or send /resume
```

A pause makes the flag `/var/lib/agent-fleet/PAUSED`. A hook in Hermes blocks every tool call and every model turn while the flag exists. The pause also stops the Kanban dispatcher and the cron jobs. The Publisher, the Deployer, and the merger refuse to act while the flag exists.

After the install the fleet is paused with the reason `waiting_for_keys`. `fleetctl resume` refuses until a model key exists. Use `fleetctl resume --force` only for tests.

## Stop

Stop is the hard stop. It stops every service of the fleet.

```bash
fleetctl stop               # or send /stop. The control bot keeps running
fleetctl start              # or send /start
```

## Revoke

Use this when you think a key leaked. It prints every key and grant with the page where you revoke it. It prints no secret value.

```bash
fleetctl revoke-checklist
fleetctl stop
```

Work through the list from top to bottom. Then add new keys with `fleetctl secret set NAME` or `fleetctl setup`. Make new local tokens with `fleetctl secret rotate PUBLISHER_API_TOKEN` (and the other local tokens in `fleetctl secret list`).

## Roll back a change

The merger rolls back a change by itself when the health check fails. It files a ticket for the orchestrator. To undo a merged change by hand:

```bash
sudo git -C /opt/agent-fleet/repo log --oneline -10      # find the merge commit
sudo git -C /opt/agent-fleet/repo revert -m 1 MERGE_COMMIT
fleetctl doctor
```

Restart a service after the revert if it changed: `sudo systemctl restart agent-fleet-publisher`.

The agents keep their own clone at `/var/lib/agent-fleet/work/agent-fleet`. The merger moves it to the new main only after the health check passes. If the clone ever drifts away from the live main, the next sync resets main and keeps the old commits on a branch named `rolled-back/<sha>`. Run the sync by hand with `sudo python3 -m ops.profiles.sync --no-botmode`.

## Pivot

A pivot archives the active project and its board and starts a new project at the Discover stage. Nothing is deleted. The folder moves to `projects/_graveyard/` with a lessons file. The board moves to the Hermes board archive.

```bash
fleetctl pivot                                  # start the next project-NNN
fleetctl pivot my-new-idea --reason "why"       # start a named project
fleetctl pivot --archive-only                   # archive and start nothing
fleetctl project status                         # stage, gate numbers, missed gates
```

The orchestrator pivots by itself when a gate is missed twice. It writes a request file. A root service reads the file within a minute and checks every field. One pivot is allowed in 24 hours.

## Update

```bash
cd ~/agent-fleet && git pull          # in the clone that you installed from
fleetctl update                       # the new code goes through the merger gates
```

The update passes the same gates as an agent change, except gate G3. G3 protects the locked core from agents. You are the owner, so you may change it. If a gate fails, nothing changes. If the health check fails, the merger rolls back.

## Keys

```bash
fleetctl secret list                  # names and state. Never a value
fleetctl secret set GROQ_API_KEY      # the value is typed hidden
fleetctl secret check                 # one free test call for each key that is set
fleetctl router approve groq          # after you read the data terms of the provider
```

## Back up and restore

The nightly backup holds configs, skills, memory, Kanban data, and projects. It holds no secrets. Keep the last 14.

```bash
fleetctl backup                       # make one now
ls /var/lib/agent-fleet-ops/backups   # the archives and their SHA-256 files
```

To restore, stop the fleet, unpack the archive into a new folder, and copy back what you need. Keys are not in the archive. Add them again with `fleetctl setup`.

## Dry run

The dry run puts a fake project through every stage with the mock model. It touches no real service and posts nothing.

```bash
sudo python3 -m ops.dryrun.run        # about 15 minutes. See docs/DRYRUN.md for a sample result
```

The driver stops the merger timer while it runs, so that no timer run takes the merger lock. It starts the timer again at the end, even after a failure. If someone kills the run, start the timer by hand: `sudo systemctl start agent-fleet-merger.timer`. Do not deploy while a dry run is active.


## n8n and Slack

Run these as the owner. Each command uses sudo where it needs it.

| Task | Command |
|---|---|
| See what runs | `fleetctl n8n status` |
| Check the whole link between n8n and the fleet | `sudo fleetctl n8n selftest` |
| Check n8n after a change, with a restart | `sudo fleetctl n8n smoke --restart` |
| Make a backup now | `sudo fleetctl n8n backup` |
| Prove that the newest backup restores | `sudo fleetctl n8n restore-test` |
| Move to a newer 2.x release | `sudo fleetctl n8n upgrade VERSION` |
| Make the Slack channels | `fleetctl slack bootstrap` |
| See which Slack tokens exist | `fleetctl slack status` |
| Put one note in the next brief | `fleetctl brief --note "TEXT"` |

Restart rules:

1. Before you restart Hermes, the router, or the control bot, run `fleetctl pause`. Wait until no worker runs. Restart. Run `fleetctl resume`.
2. Do not restart a service from 06:40 to 07:10 in the owner's zone. The brief goes out then.
3. A service that reads one secret file sees the copy from its start. After a new token, restart that service. `fleetctl secret rotate` does it for the tokens.
4. If `fleetctl doctor` says that the control bot refuses the token of n8n, run `sudo systemctl restart agent-fleet-control`.

## Uninstall

Back up first with `fleetctl backup`. These steps remove the services. They do not delete your backups.

```bash
fleetctl stop
sudo systemctl disable --now agent-fleet.target agent-fleet-control.service
sudo rm /etc/systemd/system/agent-fleet* /etc/sudoers.d/agent-fleet-control /usr/local/bin/fleetctl
sudo systemctl daemon-reload
```

Then remove the data you no longer want: `/opt/agent-fleet`, `/etc/agent-fleet`, `/etc/hermes`, `/var/lib/agent-fleet`, and `/var/lib/agent-fleet-ops`. Remove the users with `sudo loginctl disable-linger fleet fleetops` and `sudo userdel -r fleet`, `sudo userdel -r fleetops`. Revoke your keys on each provider page first.

## Restore the old Hermes setup

The build archived the old setup before it removed it. The archive is `~/fleet-backup-DATE.tar.gz`. It has mode 600 and holds old keys.

```bash
tar -xzf ~/fleet-backup-DATE.tar.gz -C ~
systemctl --user daemon-reload
```

## Problems

| Sign | What to do |
|---|---|
| `doctor` shows FAIL for a service | Run `fleetctl logs NAME` for that service. Then `sudo systemctl restart agent-fleet-NAME`. |
| The guard shows DEGRADED | The model did not load. The rule checks still run. Check the free disk space and restart `agent-fleet-guard`. |
| `fleetctl resume` refuses | The fleet waits for a model key. Run `fleetctl setup`. |
| A post is not sent | Read `ls /var/lib/agent-fleet/outbox`. The Publisher is in dry-run mode until you switch it. |
| The merger rejects a branch | `/var/lib/agent-fleet/status/merger.json` shows pass or fail for each gate. The details are in `/var/lib/agent-fleet-merger/merges.log` (root only). |
