# n8n

n8n is the workflow engine of the fleet. It does the work that needs no model. That means schedules, event fan-out, health checks, digests, backups, and links to other tools. A run of n8n uses zero model tokens. The Hermes agents do only the work that needs reasoning.

## What n8n does here

| Job | Workflow | How often |
|---|---|---|
| Send each fleet event to the right Slack channel, and copy critical events to your phone | `fleet-event` | on each event |
| Check every service and tell you after two misses in a row | `health-watch` | every 5 minutes |
| Alert you when any other workflow fails. If the alert cannot be sent, it leaves a file | `fleet-error` | on each failure |
| Give the agents four small tools over MCP | `fleet-tools` | on each tool call |
| Read an RSS or Atom feed for the research agent | `fleet-tool-fetch-feed` | on each tool call |
| Write the daily token ledger and warn at 80 percent of a free limit | `token-ledger` | every night at 23:50 |

The JSON of each workflow is in `n8n/workflows/`. A script makes it: `python3 scripts/gen_n8n.py`. Do not edit the JSON by hand. A test fails when a file does not match the script.

## How the parts connect

- A service writes an event file into a spool folder. The event relay sends it to the n8n webhook `fleet-event` with a header token. Only the relay holds that token. If n8n is down, the files wait and the relay tries again.
- n8n sends messages to you through the control bot, not through a Slack credential of its own. The bot holds the Slack tokens. Until the tokens exist, the bot writes each message to a sink file and a log line. See `docs/SLACK.md`.
- n8n calls a small action API on the control bot. It can read the status and the budget, send a message, pause, resume, ask for a brief, queue a Kanban card for a named agent, and ask for an approval. It has no shell.
- The agents reach n8n over MCP at `http://127.0.0.1:5678/mcp/fleet-tools` with a bearer token. Each agent loads only its own tools.
- Outside text from a feed goes through the injection screen on `127.0.0.1:8099` before an agent sees it.
- Each alert of `fleet-error` tries three times. If the control bot still does not take it, the workflow writes one small file to the `unsent` folder. The control bot sends that text to you within a minute of its return and moves the file to `sent`. No file is deleted.

## Where things are

| What | Where |
|---|---|
| Compose file | `n8n/docker-compose.yml` in the repository |
| Settings that are not secret | `/var/lib/agent-fleet-ops/n8n/.env` (mode 600) |
| The encryption key and the owner password hash | `/etc/agent-fleet/secrets/n8n.env` (mode 600) |
| The owner password, the API key, and the action token | `/etc/agent-fleet/secrets/n8n-admin.env` (root only) |
| The database and n8n files | `/var/lib/agent-fleet-ops/n8n/data` |
| The only folder that workflows may read and write | `/var/lib/agent-fleet-ops/n8n/files` |
| Alerts that n8n could not send | `/var/lib/agent-fleet-ops/n8n/files/unsent` |
| Backups | `/var/lib/agent-fleet-ops/n8n/backups` |
| Logs | `fleetctl logs n8n` |

## Open the editor

n8n listens on `127.0.0.1:5678` and nowhere else. Use one of these two ways.

### An SSH tunnel (the simple way)

1. On your PC run: `ssh -L 5678:127.0.0.1:5678 YOUR_USER@YOUR_MACHINE`
2. Open `http://localhost:5678` in your browser.
3. Log in with the owner email and the owner password.
4. To read the password, run this on the machine in your own terminal: `sudo fleetctl secret show N8N_OWNER_PASSWORD`. It works only in a real terminal. It hides the value again when you press Enter.

### Tailscale, to your tailnet only

1. Run `sudo fleetctl n8n serve on`.
2. Open the address that it prints.
3. To turn it off, run `sudo fleetctl n8n serve off`.

Never use `tailscale funnel`. It would open n8n to the whole internet. The `serve` command has no code that can build a funnel command.

## The safety model

- n8n listens on 127.0.0.1 only. It publishes no port. Docker runs it with `network_mode: host`, so it can reach the other fleet services on 127.0.0.1. In this rootless setup the container shares the real machine network. ufw blocks every other inbound port.
- The container has a read-only root, no capabilities, `no-new-privileges`, a memory limit of 1 GiB, and a process limit. It runs as the user 0:0. In rootless Docker that is the unprivileged user fleetops.
- The n8n 2.x secure defaults stay on. Task runners run Code nodes. A Code node cannot read the environment. The Execute Command node and the Local File Trigger node are off.
- Workflows may read and write only one folder, `/home/node/files`.
- n8n sends no telemetry. Version notices, templates, and the hiring banner are off.
- The encryption key never rotates by itself. A new key makes every stored credential unreadable.
- Each webhook and the MCP server need a token. A wrong token gets a 401 or a 403.
- The `fetch_feed` tool reads only https feeds from a short list of hosts. It follows no redirect.
- The Execute Command node and every other legacy node are banned. A test checks the workflow files.

## Commands

| Command | What it does |
|---|---|
| `fleetctl n8n status` | Shows the unit, the version, the workflows, the last backup, and the event spool |
| `fleetctl n8n prepare` | Makes the folders, the secrets, the owner hash, the `.env`, and pulls the pinned image |
| `fleetctl n8n bootstrap` | Makes the API key and the credentials, then imports and publishes the workflows. Safe to run twice |
| `fleetctl n8n smoke [--restart]` | Runs the checks: health, owner login, version, loopback ports, workflows published, webhook token |
| `fleetctl n8n backup` | Makes a backup now |
| `fleetctl n8n restore-test` | Starts a throwaway n8n from the newest backup, checks it, and removes it |
| `fleetctl n8n upgrade VERSION` | Moves to a newer 2.x release with a backup, smoke checks, and a rollback |
| `fleetctl n8n check [--migration]` | Reads the n8n releases and advisories, or the Migration Report |
| `fleetctl n8n serve on\|off` | Owner access over Tailscale |
| `fleetctl n8n mcp-env` | Writes the env file of the n8n-mcp helper for Claude Code |

`fleetctl doctor` has an n8n section. It checks the unit, `/healthz`, the version, the published workflows, the event relay, and the newest backup.

## Backups and restore

A timer makes a backup every night at 02:20. A backup is one folder. It holds the workflows as JSON, the credentials encrypted with the n8n key, a safe online copy of the SQLite database, and a manifest with the SHA-256 of each file. The key itself is never in a backup. The newest 7 stay. A good or failed backup sends a fleet event.

The credential files are not secret on their own, but never put them in git. Keep the encryption key safe. Without it, no backup can restore the credentials.

To prove that a backup works, run `sudo fleetctl n8n restore-test`. It starts a throwaway n8n on ports 5688 and 5689 from the newest backup, with the same key. It checks that the workflows and the credentials load and that the credentials decrypt. Then it removes the container and its folder.

To restore for real:

1. Run `sudo fleetctl stop`.
2. Copy `database.sqlite` from the backup folder to `/var/lib/agent-fleet-ops/n8n/data/` as the user fleetops.
3. Run `sudo fleetctl start`.
4. Run `sudo fleetctl n8n smoke`.

## Upgrades

The image is pinned by version and by digest. The fleet never upgrades n8n by itself. A weekly job (Monday 09:20) reads the releases and the published security advisories. It sends one event. The event names the newest 2.x release that is at least 3 days old, the releases that still wait, and any critical or high advisory that the running version still has.

To move, run `sudo fleetctl n8n upgrade VERSION`. The command refuses next, beta, and 3.x. It refuses a release under 3 days old unless you pass `--allow-young` for a critical advisory. It makes a backup, pulls the image by digest, checks its labels, switches the pin, restarts n8n, and runs the smoke checks. If a check fails, it puts the old database and the old pin back. The failed database stays in the backups folder. Afterwards, put the new pin in `vendor/pins.env` through the merger.

A monthly job (the 1st at 09:40) reads the n8n Migration Report and sends the counts as an event. n8n 3.0 is a one-way move, so wait for it to be stable.

## n8n-mcp for Claude Code

The n8n-mcp helper gives Claude Code knowledge of n8n nodes and a way to validate workflows. It is for Claude Code only. Fleet agents never get it. It passed the install gate. See D-042 and `docs/UPGRADES.md`.

To set it up, run these steps on the machine as the owner:

1. Run `scripts/n8n-mcp-install.sh`. It installs the pinned version from `vendor/n8n-mcp/` with install scripts off.
2. Run `sudo fleetctl n8n mcp-env`. It writes `~/.config/n8n-mcp/env` with mode 600. Never paste the key into a chat.
3. Run `claude mcp add --scope user n8n-mcp -- /path/to/agent-fleet/scripts/n8n-mcp-run.sh`. Use the real path of your clone.
4. Start a new Claude Code session. The server loads in new sessions only.

The wrapper sets stdio mode, quiet logs, no telemetry, and `WEBHOOK_SECURITY_MODE=moderate`. It turns off the tools that can delete and the audit tool. To turn one back on, edit `DISABLED_TOOLS` in `scripts/n8n-mcp-run.sh` after you read why it is off in D-042.

## Troubleshooting

| Problem | What to do |
|---|---|
| `fleetctl doctor` says n8n is not healthy | Run `fleetctl logs n8n`. Check memory with `free -h`. The limit is 1 GiB |
| The doctor says there is no API key | Run `sudo fleetctl n8n bootstrap` |
| The doctor says alerts wait for the control bot | Run `fleetctl logs control`. Start the bot. It sends the files within a minute |
| Events wait in the spool | n8n is down, or the token differs. Run `fleetctl n8n smoke`. The relay sends them when n8n is back |
| A workflow is not published | Run `sudo fleetctl n8n bootstrap`. It publishes each workflow again |
| You lost the owner password | Run `sudo fleetctl secret rotate N8N_OWNER_PASSWORD`. It makes a new password and a new hash |
| A restore test fails on the key | The backup came from another key. Use the key that made it |
| The doctor says `n8n_action_token` fails | The control bot refuses the token of n8n. It holds an old copy of its secret file. Run `sudo systemctl restart agent-fleet-control` |
| A tool answers "Authorization failed" | The same cause. Run `fleetctl doctor` and read the line `n8n_action_token` |
| The `fetch_feed` tool refuses every address | Run `fleetctl n8n selftest`. The tool accepts only the hosts on its list, and only plain https addresses with no port and no login |
| `fleetctl n8n serve on` times out | Serve is not enabled for your tailnet. Run `tailscale serve --bg --https=8443 http://127.0.0.1:5678`, open the link that it prints, enable Serve, and run the command again |
| A bootstrap says "has no published version" | n8n shows a new publication late. The client waits and tries again. If it still fails, run `sudo fleetctl n8n bootstrap` once more |

## License

n8n uses the Sustainable Use License. Internal use for your own business is allowed. Do not offer n8n to others as a hosted service. Enterprise features need a paid license and stay off. See `docs/UPGRADES.md`.
