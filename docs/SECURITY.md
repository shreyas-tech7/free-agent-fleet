# Security

This page describes the threats, the defenses, and the limits.
It also holds the secret routing table. The table is generated from `config/secrets.yaml`.

## Threats

1. **Prompt injection.** A web page, reply, email, or issue tries to give an agent orders.
2. **A confused or hijacked agent.** An agent tries to act outside its job.
3. **Secret leaks.** A key ends up in a log, a prompt, a commit, or a post.
4. **Supply chain.** A skill, a package, or a tool update carries bad code.
5. **Runaway spend.** Something adds a payment method or uses a paid plan.
6. **Platform harm.** The fleet breaks a platform rule and loses an account.

## Defenses

| Layer | What it does |
|---|---|
| Separate users | `fleet` runs agents with no sudo. `fleetops` runs services. `root` runs only the merger. |
| Rootless Docker sandbox | Agent shell commands run in a container. The container holds no secrets by default. |
| Locked core | Rules, policy tests, hidden tests, kill switch code, and the Hermes managed config. Root owns them. |
| Hermes managed scope | `/etc/hermes/config.yaml` pins smart approvals, the Docker backend, secret redaction, and a deny list. |
| Injection screen | Scans outside text at `127.0.0.1:8099`. Blocked text goes to quarantine. |
| Rule of Two | No agent has untrusted input and sensitive access and outside actions together. A test enforces it. |
| One reader per secret | Each secret sits in one file that one service reads. |
| Honesty filter | The Publisher blocks fake reviews, fake numbers, claims to be human, and AI-run claims in marketing. |
| Merger gates | Tests, scans, the locked-core check, and a size limit on skills run in a container with no network. |
| Kill switch | Three levels: pause, stop, and revoke. See below. |
| Firewall | `ufw` denies all incoming traffic except SSH. Services bind to `127.0.0.1`. |
| Page reader and API tool | Research reads public pages only through the reader. It obeys robots.txt, blocks private addresses, and removes text that looks like orders. The `source_search` tool calls four fixed APIs and hashes author names. |
| Intake | Replies and form messages are screened, stripped of personal data, and hashed before the feedback agent sees them. |
| Red-team drill | Every week plain code plants about 75 known attacks and checks that each defense holds. The pass rate goes to the security agent. |

## Agent privileges

Rule of Two says that an agent must not have all three of: untrusted input, sensitive access, and outside actions.
This project goes further. No agent has sensitive access. The services hold every key.

| Agent | Untrusted input | Sensitive access | Outside actions | How it acts outside |
|---|---|---|---|---|
| orchestrator | no | no | no | It changes only Kanban cards and files on its branches. |
| research | yes | no | no | It only reads. |
| build | yes | no | yes | `deploy_commit` asks the Deployer. The Deployer checks the rules again. |
| marketing | yes | no | yes | `publish_post` asks the Publisher. The Publisher checks honesty, injection, and rate limits. |
| feedback | yes | no | yes | `reply_faq` sends one approved FAQ answer by its id. It cannot send free text. |
| security | yes | no | no | It opens branches. The merger decides. |
| improver | yes | no | no | It opens branches. The merger decides. |

Local tokens work only on `127.0.0.1` and only through a gated service. Each token belongs to one agent. The service stores only a SHA-256 hash of its token.

## How an agent is boxed

- Hermes runs each shell command in a rootless Docker container. The image is pinned by digest. Dangerous commands are on a deny list that the managed config pins.
- The container mounts the working clone, the RTK binary (read only), and a few status folders (read only). It holds no key and no host path.
- The feedback agent has no sandbox and no file tools at all.
- The toolsets are limited for each agent. Browser, computer use, cron management, delegation, and code execution are off for all agents.
- An agent cannot change its own config or its own tools. `agents/*/agent.yaml` is part of the locked core.

## How skills enter the fleet

1. The research agent files a skill request. It never installs anything.
2. The security agent checks that the source is official. It reads the files as data and rejects scripts, hooks, and odd links.
3. The skill goes into `skills/<name>/` on a branch, with its source, its commit, and the hash of each original file.
4. The merger runs the Cisco skill scanner, the secret scan, and the locked tests in a throwaway container with no network. Each skill file must be 15 KB or less.
5. Updates stay off. A new version needs a new request with a new pinned commit.

The 23 skills that ship with the project passed the same scanner. See `vendor/LOCK.md`.

## How an injection is stopped

An attack text has to get past every layer. One layer is enough to stop it.

1. The text is scanned by a model and by rules before an agent reads it. Flagged text is removed or quarantined.
2. The agent prompt says that outside text is data. The agent has no key to leak and no free-text way to act.
3. Each action goes through a service that checks it again: the honesty filter, the injection screen, the rate limits, and the free-plan check.
4. Hermes approvals and the deny list stop dangerous commands. The sandbox stops what is left.
5. The kill switch stops everything at once.

## Secret routing table

Each secret has exactly one reader service.
`fleetctl secret set NAME` writes the value to the listed file with the listed owner and mode 600.
Local tokens that agents present to the Publisher and the Deployer are stored on the agent side.
The service stores only a SHA-256 hash of each token.

<!-- SECRETS-TABLE:START -->
| Secret | Group | Service that reads it | File | Owner and mode |
|---|---|---|---|---|
| GROQ_API_KEY | router | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| CLOUDFLARE_AI_API_TOKEN | router | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| CLOUDFLARE_ACCOUNT_ID | router | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| OPENROUTER_API_KEY | router | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| GEMINI_API_KEY | router | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| COHERE_API_KEY | router | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| NVIDIA_API_KEY | router | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| BLUESKY_APP_PASSWORD | publisher | publisher | `/etc/agent-fleet/secrets/publisher.env` | fleetops, 600 |
| MASTODON_ACCESS_TOKEN | publisher | publisher | `/etc/agent-fleet/secrets/publisher.env` | fleetops, 600 |
| DISCORD_PUBLISHER_BOT_TOKEN | publisher | publisher | `/etc/agent-fleet/secrets/publisher.env` | fleetops, 600 |
| TELEGRAM_PUBLISHER_BOT_TOKEN | publisher | publisher | `/etc/agent-fleet/secrets/publisher.env` | fleetops, 600 |
| DEVTO_API_KEY | publisher | publisher | `/etc/agent-fleet/secrets/publisher.env` | fleetops, 600 |
| GITHUB_PUBLISHER_TOKEN | publisher | publisher | `/etc/agent-fleet/secrets/publisher.env` | fleetops, 600 |
| X_ACCESS_TOKEN | publisher | publisher | `/etc/agent-fleet/secrets/publisher.env` | fleetops, 600 |
| REDDIT_CLIENT_SECRET | publisher | publisher | `/etc/agent-fleet/secrets/publisher.env` | fleetops, 600 |
| CLOUDFLARE_API_TOKEN | deployer | deployer | `/etc/agent-fleet/secrets/deployer.env` | fleetops, 600 |
| CLOUDFLARE_DEPLOYER_ACCOUNT_ID | deployer | deployer | `/etc/agent-fleet/secrets/deployer.env` | fleetops, 600 |
| CONTROL_TELEGRAM_BOT_TOKEN | control | control | `/etc/agent-fleet/secrets/control.env` | fleetops, 600 |
| CONTROL_DISCORD_BOT_TOKEN | control | control | `/etc/agent-fleet/secrets/control.env` | fleetops, 600 |
| SMTP_PASSWORD | briefing | briefing | `/etc/agent-fleet/secrets/briefing.env` | fleetops, 600 |
| BACKUP_GITHUB_TOKEN | backup | backup | `/etc/agent-fleet/secrets/backup.env` | fleetops, 600 |
| JWT_SECRET | internal | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| API_KEY_SECRET | internal | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| STORAGE_ENCRYPTION_KEY | internal | router | `/etc/agent-fleet/secrets/router.env` | fleetops, 600 |
| INITIAL_PASSWORD | internal | router | `/etc/agent-fleet/secrets/router-admin.env` | fleetops, 600 |
| SEARXNG_SECRET | internal | searxng | `/etc/agent-fleet/secrets/searxng.env` | fleetops, 600 |
| CONTROL_NOTIFY_TOKEN | internal | briefing | `/etc/agent-fleet/secrets/briefing.env` | fleetops, 600 |
| ROUTER_KEY_HERMES | internal | hermes | `/home/fleet/.hermes/.env` | fleet, 600 |
| ROUTER_KEY_BRIEFING | internal | briefing | `/etc/agent-fleet/secrets/briefing.env` | fleetops, 600 |
| ROUTER_KEY_DEV | internal | dev-tests | `/etc/agent-fleet/secrets/router-dev.env` | root, 600 |
| N8N_ENCRYPTION_KEY | internal | n8n | `/etc/agent-fleet/secrets/n8n.env` | fleetops, 600 |
| N8N_INSTANCE_OWNER_PASSWORD_HASH | internal | n8n | `/etc/agent-fleet/secrets/n8n.env` | fleetops, 600 |
| N8N_OWNER_PASSWORD | internal | n8n-admin | `/etc/agent-fleet/secrets/n8n-admin.env` | root, 600 |
| N8N_API_KEY | internal | n8n-admin | `/etc/agent-fleet/secrets/n8n-admin.env` | root, 600 |
| FLEET_EVENT_TOKEN | internal | events | `/etc/agent-fleet/secrets/events.env` | fleetops, 600 |
| FLEET_ACTION_TOKEN | internal | n8n-admin | `/etc/agent-fleet/secrets/n8n-admin.env` | root, 600 |
| N8N_TOOLS_TOKEN | internal | hermes | `/home/fleet/.hermes/.env` | fleet, 600 |
| PUBLISHER_API_TOKEN | internal | hermes | `/home/fleet/.hermes/profiles/marketing/.env` | fleet, 600 |
| PUBLISHER_REPLY_TOKEN | internal | hermes | `/home/fleet/.hermes/profiles/feedback/.env` | fleet, 600 |
| DEPLOYER_API_TOKEN | internal | hermes | `/home/fleet/.hermes/profiles/build/.env` | fleet, 600 |
| SLACK_HQ_BOT_TOKEN | slack | control | `/etc/agent-fleet/secrets/control.env` | fleetops, 600 |
| SLACK_HQ_APP_TOKEN | slack | control | `/etc/agent-fleet/secrets/control.env` | fleetops, 600 |
| SLACK_HERMES_BOT_TOKEN | slack | hermes | `/home/fleet/.hermes/profiles/orchestrator/.env` | fleet, 600 |
| SLACK_HERMES_APP_TOKEN | slack | hermes | `/home/fleet/.hermes/profiles/orchestrator/.env` | fleet, 600 |
<!-- SECRETS-TABLE:END -->

## Kill switch

| Level | Command | Effect |
|---|---|---|
| PAUSE | `fleetctl pause` or `/pause` | Creates `/var/lib/agent-fleet/PAUSED`. Pauses every Hermes cron job. Stops the Kanban dispatcher. |
| STOP | `fleetctl stop` or `/stop` | Runs `systemctl stop agent-fleet.target`. The control bot keeps running. |
| REVOKE | `fleetctl revoke-checklist` | Prints each key and grant with its revoke page. It prints no secret values. |

Each agent checks the `PAUSED` flag before each task. A pause is not a hard stop. Work in progress finishes.
Use STOP when you need a hard stop. Use REVOKE when you think a key leaked.

## Locked core

Root owns these paths. The fleet can read them and cannot change them.
The merger refuses any change that touches them.

- `core/` (rules, policy tests, kill switch code)
- `config/hermes.managed.yaml` and `/etc/hermes/config.yaml`
- `ops/merger/`, `ops/control/`, `fleetctl`, `install.sh`, `install/`, and `systemd/`
  (these run as root or hold the kill switch)

## Known limits

Read these. They are honest.

- Hermes managed scope works through file permissions. It is not a sandbox for a hostile process.
- The Hermes Docker backend skips the dangerous-command prompt. The container is the boundary.
  The `approvals.deny` list still applies.
- The injection screen can miss new attacks. It is one layer, not the only one. The weekly drill tests it against known attacks only.
- The rule-based honesty filter errs on the side of blocking. It can miss a new kind of false claim.
- Product tests run in the build sandbox. The merger gates run the repository tests and scans. They do not run the tests of a product.
- The weekly GEPA run is third-party Python code. It runs on the host as the user `fleet`, not in a container, so it has the same file access as the Hermes gateway. Its source and every package are pinned by hash. Its environment holds only the local router key. See D-027.
- The Playwright browser is a large program. It may open only local pages, but it is not a sandbox against a browser bug.
- A pause does not kill running work. A stop does.
- Free model plans can change their terms. Read the terms before you approve a provider.
- The owner must keep the accounts safe. Use unique passwords and two-factor login.

## Report a problem

Open a private security report on the project repository. Do not post secrets in a report.
