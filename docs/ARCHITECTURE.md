# Architecture

This page shows how the parts fit together. It says who owns what and who can talk to whom.

## Users and privileges

| User | Runs | Power |
|---|---|---|
| `fleet` | Hermes Agent and all agents | No sudo. Agent shell commands run in rootless Docker containers. |
| `fleetops` | Router, injection screen, SearXNG, page reader, Publisher, Deployer, control bot, Slack adapter, n8n, briefing, quota tracker, backups, red-team drill | No sudo, except four exact commands for the kill switch. |
| `root` | The merger, the pivot service, and owner of the locked core | Never runs agent code. |

No user is in the `docker` group. The system Docker daemon is off.
The users `fleet` and `fleetops` each run their own rootless Docker daemon.

## Picture

```
                     owner (Telegram, Discord, email, SSH)
                          |                     ^
                   control bot (/pause /stop)   | daily brief
                          |                     |
   +----------------------+---------------------+---------------+
   |                      agent-fleet.target                     |
   |                                                              |
   |  Hermes gateway (user fleet)                                 |
   |   - Kanban dispatcher, one board per project                 |
   |   - cron, 7 profiles: orchestrator research build marketing  |
   |                       feedback security improver             |
   |   - each profile has its own Docker sandbox                  |
   |          |                |               |                  |
   |   model router     injection screen   SearXNG, page reader   |
   |   127.0.0.1:20128  127.0.0.1:8099     127.0.0.1:8888, 8095   |
   |          |                                                    |
   |   free model providers (keys in router.env)                  |
   |                                                              |
   |  Publisher 127.0.0.1:8098     Deployer 127.0.0.1:8097        |
   |   (social tokens, intake)      (Cloudflare token)            |
   |                                                              |
   |  quota tracker   briefing   backup   red-team drill          |
   +--------------------------------------------------------------+
   merger (root, every 15 minutes) -> gates -> /opt/agent-fleet/repo
   pivot service (root, on request file)
   control bot runs outside the target, so /stop does not stop it.
```

## The agents

Each agent is one Hermes profile. The files `agents/<name>/SOUL.md` and `agents/<name>/agent.yaml` define it. A sync step (`ops/profiles/sync.py`) makes the profile from them. It runs after each install, deploy, and update.

| Agent | Model | Reads outside text | Acts outside | Job |
|---|---|---|---|---|
| orchestrator | fleet-smart | no | no | Runs the business loop. Decides niche, product, price, channels, and pivots. |
| research | fleet-worker | yes | no | Finds pains through official APIs and public pages. Scores them. |
| build | fleet-coder | yes | yes (deploy) | Builds products with tests first. Deploys only through the Deployer. |
| marketing | fleet-worker | yes | yes (post) | Makes the brand kit and the copy. Posts only through the Publisher. |
| feedback | fleet-worker | yes | yes (FAQ replies) | Turns feedback into short tickets. Answers only from the approved FAQ. |
| security | fleet-worker | yes | no | Reviews skill requests. Reads the weekly red-team result. |
| improver | fleet-smart | yes | no | Reviews failures each week. Opens small safe changes on branches. |

No agent holds a key. The Publisher, the Deployer, and the router hold them. See [SECURITY.md](SECURITY.md).

## Trust boundaries

1. **Outside text is data.** Web pages, replies, emails, issues, and downloaded files are never orders.
   The injection screen scans them. Agents that read them hold no keys.
2. **Rule of Two.** No agent has all three of: untrusted input, sensitive access, and outside actions.
   No agent has sensitive access at all. A policy test checks `agents/*/agent.yaml`.
3. **Secrets have one reader.** The table in [SECURITY.md](SECURITY.md) names each reader.
4. **The locked core is read-only to the fleet.** It holds the rules, the policy tests, the hidden tests,
   the kill switch code, and the Hermes managed config. Root owns it.
5. **The merger is the only path in.** Agents commit to branches named `agent/<agent>/<topic>`.
   The merger runs the gates in a throwaway container with no network. It merges only when all gates pass.
6. **The sandbox is the box.** Agent shell commands run in a rootless container. The container holds no secrets.
   It mounts only the working clone and a few read-only status folders.

## Data flow of one business loop

1. The orchestrator creates Kanban tasks for a project.
2. The research agent finds pains and writes `pain_report.json`.
3. The orchestrator scores the candidates and picks one. It records the decision in `STATE.md`.
4. The marketing agent writes a validation page. The Publisher posts the launch kit.
5. The build agent writes tests first, builds the product, and asks the Deployer to deploy a tested commit.
6. The intake screens replies and form messages and removes personal data. The feedback agent reads the inbox and writes tickets.
7. The build agent fixes tickets. The improver reviews a weekly trace summary and opens change branches.
8. The briefing sends the owner a short report each morning.

[DRYRUN.md](DRYRUN.md) shows this loop with a fake project.

## Ports

All services listen on `127.0.0.1` only. The firewall blocks everything else except SSH.

| Port | Service |
|---|---|
| 20128 | Model router |
| 8099 | Injection screen |
| 8098 | Publisher |
| 8097 | Deployer |
| 8096 | Control bot (local notify endpoint) |
| 8095 | Page reader |
| 8888 | SearXNG |
| 5678 | n8n (the workflow engine). Its helper port 5679 is also local. |

## n8n, Slack, and the tool links

n8n does the work that needs no model: schedules, event fan-out, health checks, and a nightly token ledger. It runs from a pinned image in rootless Docker as `fleetops`, in single mode with SQLite. A run of n8n costs no model tokens. See `docs/N8N.md`.

- Six workflows are published. `fleet-error` is the global error workflow. `fleet-event` takes events by webhook and routes them to Slack and to the owner. `health-watch` checks each service every 5 minutes. `token-ledger` writes one row for each key each night. `fleet-tools` is an MCP server. `fleet-tool-fetch-feed` is the sub-workflow of its feed tool.
- Agents call the `fleet-tools` server over MCP with a bearer token. Each profile selects only the tools that its `agent.yaml` names: `notify_owner`, `request_approval`, `log_event`, and `fetch_feed`.
- The control bot has a small local API that n8n calls with a second token. The bot holds only the hash of that token. A message that n8n cannot deliver waits as a file, and the bot sends it within a minute.
- Slack is the main place to talk to the fleet. The Slack adapter is its own unit, outside `agent-fleet.target`, so `/fleet stop` cannot stop the part that answers it. It uses Socket Mode and needs no public address. A board exporter reads the Kanban boards as the user `fleet` and writes a snapshot. The adapter mirrors that snapshot to the channel #board. See `docs/SLACK.md`.
- Quiet hours run from 22:00 to 07:00 in the owner's zone. Only critical messages post live. The rest goes into the daily brief.
- The owner approves each spec, release, install, and public sync. The Deployer and the Publisher refuse live mode without a release approval.

## Directories

| Path | Owner | Use |
|---|---|---|
| `/opt/agent-fleet/repo` | root | Live copy of the repository. Services run from here. |
| `/opt/agent-fleet/core/tests/hidden` | root, mode 700 | Hidden tests. Only the merger runs them. |
| `/opt/agent-fleet/skills-view` | root | One copy of the allowed skills for each agent. |
| `/opt/agent-fleet/mcp`, `/opt/agent-fleet/bin` | root | Playwright MCP, its browser, and the RTK binary. |
| `/etc/agent-fleet/fleet.yaml` | root | Live config. Never in git. |
| `/etc/agent-fleet/secrets/` | root | One file per reader service. Each file has mode 600. |
| `/etc/hermes/config.yaml` | root | Hermes managed config. Pins the safety settings. |
| `/var/lib/agent-fleet` | root, group fleet, sticky | Agent state, the `PAUSED` flag, `budget.json`, the working clone, tickets, requests. |
| `/var/lib/agent-fleet-ops` | fleetops | Ops state, logs, backups. |
| `/var/lib/agent-fleet-merger` | root | Merger state and its private log. |

## Choices the build made

The log of decisions is `docs/DECISIONS.md` in the private repository.
