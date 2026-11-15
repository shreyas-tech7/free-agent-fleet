# Prompt for Claude Cowork: the Slack apps

This file is made by `scripts/gen_docs.py`. Do not edit it by hand. The two manifests inside it are copies of `slack/fleet-hq.manifest.json` and `slack/hermes.manifest.json`.

## Before you paste

1. Make a Slack workspace on the free plan, or pick one that you own. Sign in to it in the browser that Cowork uses.
2. Open your own SSH window to the fleet machine. You will paste the tokens there, never into Cowork.
3. Paste everything below the line into Claude Cowork.

---

You help the owner set up Slack for a fleet of AI agents. You work in the browser. You make two Slack apps from manifests, install them, and add the GitHub app to Slack.

## Rules

1. Do not ask questions. If a step is not possible, skip it and write the reason in your final report.
2. Never read, copy, or type a token. A token starts with `xoxb-` or `xapp-`. Never put a token in a message, a file, or a note. Never take a screenshot of a token screen. Never describe it.
3. Stop at each token screen. Say to the owner: "Copy this token now. Paste it in your own SSH window with `fleetctl secret set NAME`." Use the name that the step gives. Wait until the owner says that it is done. Then go to the next step.
4. Do not create an account. Do not add a payment method. Do not change the plan. Use the free plan only.
5. Work only in the workspace that the owner is signed in to. Do not invite anyone. Do not post a message. Do not change a setting that the steps do not name.
6. Use only slack.com and api.slack.com. Use github.com only in Step C, for the sign-in of the GitHub app.
7. At the end, write a short prompt for Claude Code. It has 10 lines or less. It lists what you finished, what you skipped and why, and what the owner must do next.

## Step A. The Fleet HQ app

1. Open https://api.slack.com/apps. Choose **Create New App**, then **From a manifest**.
2. Pick the workspace. Choose the JSON tab. Paste the whole manifest named `fleet-hq` from the end of this prompt. Choose **Next**, then **Create**.
3. Choose **Install to Workspace**. Allow it.
4. Open **OAuth & Permissions**. Stop at the **Bot User OAuth Token**. Tell the owner to run `fleetctl secret set SLACK_HQ_BOT_TOKEN`. Wait.
5. Open **Basic Information**. Scroll to **App-Level Tokens**. Choose **Generate Token and Scopes**. Name it `socket`. Add the scope `connections:write`. Choose **Generate**. Stop at the token. Tell the owner to run `fleetctl secret set SLACK_HQ_APP_TOKEN`. Wait.

## Step B. The Hermes app

1. Go back to https://api.slack.com/apps. Choose **Create New App**, then **From a manifest**.
2. Pick the same workspace. Choose the JSON tab. Paste the whole manifest named `hermes` from the end of this prompt. Choose **Next**, then **Create**.
3. Choose **Install to Workspace**. Allow it.
4. Open **OAuth & Permissions**. Stop at the **Bot User OAuth Token**. Tell the owner to run `fleetctl secret set SLACK_HERMES_BOT_TOKEN`. Wait.
5. Open **Basic Information**. Scroll to **App-Level Tokens**. Choose **Generate Token and Scopes**. Name it `socket`. Add the scope `connections:write`. Choose **Generate**. Stop at the token. Tell the owner to run `fleetctl secret set SLACK_HERMES_APP_TOKEN`. Wait.

## Step C. The GitHub app

1. In the workspace, open the Slack App Directory at https://slack.com/apps. Search for **GitHub**. Choose the app that GitHub Inc. publishes.
2. Choose **Add to Slack**. Allow it. Sign in to GitHub in the window that opens, as the owner. Do not type a password. If a login is needed, stop and tell the owner to do it.
3. Choose only the repositories that the owner names. If the owner names none, choose none.
4. Do not subscribe any channel yet. The channel `#github` does not exist until the owner runs `fleetctl slack bootstrap`.

## Step D. The final report

Write the short prompt for Claude Code now. Use this form:

```text
Cowork finished: (the steps that are done)
Cowork skipped: (the steps that were not done, each with one reason)
Owner next: run fleetctl slack bootstrap, then fleetctl config set slack.sink_mode false, then fleetctl doctor
```

## Manifests

### Manifest named `fleet-hq`

```json
{
  "display_information": {
    "name": "Fleet HQ",
    "description": "The control room of the agent fleet. Status, approvals, the board, and the kill switch.",
    "background_color": "#1c1b22"
  },
  "features": {
    "app_home": {
      "home_tab_enabled": true,
      "messages_tab_enabled": false,
      "messages_tab_read_only_enabled": true
    },
    "bot_user": {
      "display_name": "Fleet HQ",
      "always_online": true
    },
    "slash_commands": [
      {
        "command": "/fleet",
        "description": "Control the agent fleet",
        "usage_hint": "status | pause | resume | stop | brief | board | approvals | ask AGENT TEXT | run WORKFLOW | tokens | help",
        "should_escape": false
      }
    ]
  },
  "oauth_config": {
    "scopes": {
      "bot": [
        "chat:write",
        "chat:write.customize",
        "commands",
        "channels:read",
        "channels:manage",
        "channels:join",
        "pins:write",
        "bookmarks:write"
      ]
    }
  },
  "settings": {
    "event_subscriptions": {
      "bot_events": [
        "app_home_opened"
      ]
    },
    "interactivity": {
      "is_enabled": true
    },
    "org_deploy_enabled": false,
    "socket_mode_enabled": true,
    "token_rotation_enabled": false
  }
}
```

### Manifest named `hermes`

```json
{
  "_metadata": {
    "major_version": 1,
    "minor_version": 1
  },
  "display_information": {
    "name": "Hermes",
    "description": "The orchestrator agent of the fleet. Ask it about the business and the board.",
    "background_color": "#1a1a2e"
  },
  "features": {
    "app_home": {
      "home_tab_enabled": false,
      "messages_tab_enabled": true,
      "messages_tab_read_only_enabled": false
    },
    "bot_user": {
      "display_name": "Hermes",
      "always_online": true
    },
    "slash_commands": [
      {
        "command": "/hermes",
        "description": "Talk to Hermes or run a subcommand",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[subcommand] [args]"
      },
      {
        "command": "/start",
        "description": "Acknowledge platform start pings without a reply",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/new",
        "description": "Start a new session (fresh session ID + history)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[name]"
      },
      {
        "command": "/save",
        "description": "Export the current conversation (bare /save shows usage)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "<json|md|html> [filename] [redact]"
      },
      {
        "command": "/retry",
        "description": "Retry the last message (resend to agent)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/undo",
        "description": "Back up N user turns and re-prompt (default 1)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[N]"
      },
      {
        "command": "/title",
        "description": "Set a title for the current session",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[name]"
      },
      {
        "command": "/branch",
        "description": "Branch the current session (new thread on Discord/Telegram/Slack/Matrix; --here stays here)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[--here] [name]"
      },
      {
        "command": "/compress",
        "description": "Compress conversation context (add 'here [N]' to keep recent N turns; --preview shows what would happen)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[here [N] | focus topic | --preview|--dry-run]"
      },
      {
        "command": "/rollback",
        "description": "List or restore filesystem checkpoints (restores keep your hand-edits; --all overrides)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[number] [--all]"
      },
      {
        "command": "/stop",
        "description": "Kill all running background processes",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/approve",
        "description": "Approve a pending dangerous command",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[session|always]"
      },
      {
        "command": "/deny",
        "description": "Deny a pending dangerous command (optionally with a reason)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[all] [reason]"
      },
      {
        "command": "/bg",
        "description": "Run a prompt in a separate background session",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "<prompt>"
      },
      {
        "command": "/btw",
        "description": "Ask a side question about the current conversation without interrupting it",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "<question>"
      },
      {
        "command": "/agents",
        "description": "Show active agents and running tasks",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/queue",
        "description": "Queue a prompt for the next turn, or list/edit/rm/move/clear queued prompts",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[<prompt>|list|edit N <prompt>|rm N|move A B|clear]"
      },
      {
        "command": "/steer",
        "description": "Inject a message after the next tool call without interrupting",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "<prompt>"
      },
      {
        "command": "/goal",
        "description": "Set a standing goal Hermes works on across turns until achieved",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[text | -- <text> | draft <text> | show | gate add <cmd> | pause | resume | clear | status | wait <p"
      },
      {
        "command": "/loop",
        "description": "Re-run a prompt on a recurring interval in this session",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[interval] <prompt> [--times N] [--until <condition>] | status | pause | resume | stop"
      },
      {
        "command": "/plan",
        "description": "Write a markdown implementation plan to .hermes/plans/ without executing anything",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[task]"
      },
      {
        "command": "/subgoal",
        "description": "Add or manage extra criteria on the active goal",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[text | remove N | clear]"
      },
      {
        "command": "/context",
        "description": "Show detailed context window view with usage gauge, category breakdown, compression stats, and throughput",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[all]"
      },
      {
        "command": "/profile",
        "description": "Show active profile name and home directory",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/sethome",
        "description": "Set this chat as the home channel",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/resume",
        "description": "Resume a previously-named session",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[name]"
      },
      {
        "command": "/sessions",
        "description": "Browse and resume previous sessions",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/model",
        "description": "Switch model (session-scoped; --global to persist)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[model|reset] [--provider name] [--reasoning level] [--global|--session] [--refresh]"
      },
      {
        "command": "/codex-runtime",
        "description": "Toggle codex app-server runtime for OpenAI/Codex models",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[auto|codex_app_server]"
      },
      {
        "command": "/personality",
        "description": "Set a predefined personality",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[name]"
      },
      {
        "command": "/footer",
        "description": "Toggle gateway runtime-metadata footer on final replies",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[on|off|status]"
      },
      {
        "command": "/reasoning",
        "description": "Manage reasoning effort and display",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[level|show|hide|full|clamp] [--global]"
      },
      {
        "command": "/fast",
        "description": "Fast mode \u2014 OpenAI Priority Processing / Anthropic Fast Mode (normal/fast/auto/cold/ultrafast)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[normal|fast|auto|cold|ultrafast|status] [--global]"
      },
      {
        "command": "/voice",
        "description": "Toggle voice mode",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[on|off|tts|status]"
      },
      {
        "command": "/busy",
        "description": "Control how messages behave while Hermes is working",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[queue|steer|interrupt|status]"
      },
      {
        "command": "/memory",
        "description": "Review pending memory writes / toggle the approval gate",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[pending|approve|reject|approval] [id|on|off]"
      },
      {
        "command": "/bundles",
        "description": "List skill bundles (aliases /<name> for multiple skills)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/learn",
        "description": "Learn a reusable skill from anything you describe (dirs, URLs, this chat, notes)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "<what to learn from>"
      },
      {
        "command": "/suggestions",
        "description": "Review suggested automations (accept/dismiss)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[accept|dismiss N | catalog]"
      },
      {
        "command": "/blueprint",
        "description": "Set up an automation from a blueprint template",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[name] [slot=value ...]"
      },
      {
        "command": "/curator",
        "description": "Background skill maintenance (status, run, pin, archive, list-archived)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[subcommand]"
      },
      {
        "command": "/kanban",
        "description": "Multi-profile collaboration board (tasks, links, comments)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[subcommand]"
      },
      {
        "command": "/reload-mcp",
        "description": "Reload MCP servers from config",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/reload-skills",
        "description": "Re-scan ~/.hermes/skills/ for newly installed or removed skills",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/commands",
        "description": "Browse all commands and skills (paginated)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[page]"
      },
      {
        "command": "/help",
        "description": "Show available commands (/help skills lists skill commands, /help <text> filters)",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[skills|<filter>]"
      },
      {
        "command": "/restart",
        "description": "Gracefully restart the gateway after draining active runs",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands"
      },
      {
        "command": "/usage",
        "description": "Show token usage and rate limits; `reset` redeems a banked Codex limit reset",
        "should_escape": false,
        "url": "https://hermes-agent.local/slack/commands",
        "usage_hint": "[reset [--force]]"
      }
    ]
  },
  "oauth_config": {
    "scopes": {
      "bot": [
        "app_mentions:read",
        "channels:history",
        "channels:read",
        "chat:write",
        "commands",
        "files:read",
        "files:write",
        "groups:history",
        "groups:read",
        "im:history",
        "im:read",
        "im:write",
        "mpim:history",
        "mpim:read",
        "reactions:read",
        "users:read"
      ]
    }
  },
  "settings": {
    "event_subscriptions": {
      "bot_events": [
        "app_mention",
        "message.channels",
        "message.groups",
        "message.im",
        "message.mpim",
        "reaction_added",
        "reaction_removed"
      ]
    },
    "interactivity": {
      "is_enabled": true
    },
    "org_deploy_enabled": false,
    "socket_mode_enabled": true,
    "token_rotation_enabled": false
  }
}
```
