# Slack

Slack is the main place where you talk to the fleet. You get three apps in one workspace: Fleet HQ, Hermes, and GitHub. Fleet HQ is the control room. Hermes is the orchestrator agent. GitHub shows commits and pull requests.

Until the Slack tokens exist, the fleet writes each Slack message to a sink file and a log line. Nothing is lost. `fleetctl doctor` shows WAITING_FOR_KEYS for Slack.

## What the free plan allows

- 10 apps, and 90 days of message history. The fleet uses 3 apps.
- An app that you build and install in your own workspace keeps the normal rate limits. Never distribute these apps.
- Slack's agent view and assistant view need a paid plan. The manifests leave them out.

## Why Socket Mode

Fleet HQ and Hermes use Socket Mode. They open an outbound connection to Slack. You need no public address, no tunnel, and no open port.

Slack splits the events of one app across all of its connections. So never connect two processes with the same app token. Each app has its own token pair, and each pair belongs to one process.

## Set up the apps (about 15 minutes)

1. Make a Slack workspace, or pick one that you own.
2. Open https://api.slack.com/apps and choose **Create New App**, then **From a manifest**.
3. Pick the workspace. Paste the whole text of `slack/fleet-hq.manifest.json`. Choose **Create**.
4. Choose **Install to Workspace** and allow it. Open **OAuth & Permissions** and copy the **Bot User OAuth Token**. It starts with `xoxb-`. Save it in your own terminal with `fleetctl secret set SLACK_HQ_BOT_TOKEN`.
5. Open **Basic Information**, then **App-Level Tokens**. Choose **Generate Token and Scopes**. Name it `socket`. Add the scope `connections:write`. Copy the token. It starts with `xapp-`. Save it with `fleetctl secret set SLACK_HQ_APP_TOKEN`.
6. Repeat steps 2 to 5 for `slack/hermes.manifest.json`. The secrets are `SLACK_HERMES_BOT_TOKEN` and `SLACK_HERMES_APP_TOKEN`.
7. Add the GitHub app from the Slack app directory. Pick the repositories that you want to see.

Never paste a token into a chat. `fleetctl secret set` asks for the value with hidden input.

## Let the fleet set up the channels

Run `fleetctl slack bootstrap`. It makes these channels, sets each topic, and saves each channel ID in the fleet config:

| Channel | What it holds |
|---|---|
| #hq | Talk to the fleet. Pinned help |
| #brief | The daily brief |
| #approvals | Approval cards with Approve and Reject buttons |
| #alerts | Alerts that need a look |
| #board | One message for each Kanban card |
| #agents | Short notes from the agents |
| #n8n | Workflow and backup news |
| #github | Commits and pull requests |
| #portfolio | The weekly portfolio log |

It also pins a help note in #hq and adds bookmarks to n8n, the Hermes dashboard, and the GitHub repositories. Then it invites the Hermes bot to #hq, publishes App Home, and posts one test message in each channel.

## Commands

Type `/fleet` in any channel.

| Command | What it does |
|---|---|
| `/fleet status` | What runs, the pause state, keys, budget, and the active project |
| `/fleet pause` and `/fleet resume` | Stop new work, and start it again |
| `/fleet stop` | Stop every service. It asks you to confirm with a button |
| `/fleet brief` | Make the daily brief now |
| `/fleet board` | The open cards, grouped by agent |
| `/fleet approvals` | Open approvals, with buttons |
| `/fleet ask AGENT TEXT` | Queue a card for one agent |
| `/fleet run WORKFLOW` | Run one named n8n workflow now. The names are `health-watch` and `token-ledger` |
| `/fleet tokens` | Today's token use against the free limits |
| `/fleet help` | This list |

Hermes owns `/status` and `/stop`. They talk to the orchestrator and not to the kill switch. Use `/fleet stop` for the kill switch.

Fleet HQ answers only your Slack member ID. Anyone else gets no answer.

The Hermes manifest leaves out `/yolo` and `/approvals`. They change the approval posture, which the fleet pins. You can still answer one pending approval with `/approve` and `/deny`.

## Hermes settings

Set these once. `fleetctl slack bootstrap` writes the channel ID.

- `SLACK_ALLOWED_USERS` holds your member ID.
- `SLACK_ALLOW_BOTS` is `none`.
- Hermes answers only when you mention it in a channel.
- `SLACK_HOME_CHANNEL` is the ID of #hq.

## Quiet hours

From 22:00 to 07:00 in your time zone, only critical alerts post live. Everything else waits and goes into the daily brief. Change the hours in `config/owner.yaml`.

## Agent names

Fleet HQ posts as each agent, with its own name and icon:

| Agent | Icon |
|---|---|
| Orchestrator | :compass: |
| Research | :mag: |
| Build | :hammer_and_wrench: |
| Marketing | :mega: |
| Feedback | :speech_balloon: |
| Security | :shield: |
| Improver | :seedling: |

## More agents later

Each agent can get its own Hermes app. The manifests of the six other agents are ready in `slack/agents/`. To make one again, run `fleetctl slack add-agent NAME` inside your clone of the repository. Do not install the extra apps until you need them. The free plan allows 10 apps.

## Problems

| Problem | What to do |
|---|---|
| `/fleet` says that the app did not respond | Check `fleetctl logs control`. The bot may have no Slack tokens yet |
| Messages go to the sink and not to Slack | The tokens are not set, or `slack.sink_mode` is true. Run `fleetctl doctor` |
| Buttons do nothing | Interactivity must be on. It is on in the manifest. Reinstall the app if you changed it |
| Two processes fight over events | Two processes use the same app token. Stop the extra one |
