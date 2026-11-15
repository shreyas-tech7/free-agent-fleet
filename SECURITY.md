# Security

## Report a problem

Please do not open a public issue for a security problem. Use the private vulnerability reporting of this repository on GitHub. Give the steps to reproduce it. Never include a real secret in a report.

## The model in short

- Agents run in a Docker sandbox with no sudo. Plain-code services hold every secret and every outside action.
- All outside text goes through a local injection screen before an agent reads it.
- No agent may read untrusted text, hold sensitive access, and act outside at the same time. A test enforces this.
- The Publisher and the Deployer run in dry-run mode by default. You approve each release.
- The rules, the policy tests, the kill switch, and the Hermes managed config are owned by root. Agents cannot change them.
- Every change from an agent passes tests, scanners, and policy gates in a throwaway container with no network before the merger lets it in.

Read `docs/SECURITY.md` for the table of secrets, and `docs/RUNBOOK.md` for the kill switch.

## What you should do

- Keep the machine updated. The installer turns on unattended security upgrades.
- Keep the n8n editor on `127.0.0.1`. Reach it with an SSH tunnel or with `tailscale serve`. Never use `tailscale funnel`.
- Put keys in only with `fleetctl secret set`. Never paste a key into a chat, an issue, or a file in this repository.
- Revoke every key when you stop using the fleet. Run `fleetctl revoke-checklist`.
