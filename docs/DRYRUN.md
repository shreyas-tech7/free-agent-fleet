# Dry run

The dry run puts one fake project, `dry-run-widgets`, through every stage of the business loop.
It uses the mock model (`tests/mock_llm.py`). It needs no key. It posts nothing and it deploys nothing.

Run it with `sudo python3 -m ops.dryrun.run`. It takes about 15 minutes on a small machine.
The merger runs its gates in a container three times. Each run takes about 5 minutes.

## What is real and what is simulated

Real parts:

- `fleetctl pivot`, the Kanban board, and its cards.
- The injection screen service, the honesty filter, and the rate limits.
- The intake, the ticket tools, and the git working clone.
- The merger with all six gates, and the rollback.
- The Deployer checks and the brief code.

Simulated parts:

- The model answers. They are canned and schema checked.
- The market numbers: signups, users, fix time, and stranger minutes.
- The Publisher and the Deployer. They run in dry-run mode inside the driver, with their own folders.

The real services and their configuration stay untouched. The fleet stays paused. No agent runs.

## Result

The dry run **passed** for the project `dry-run-widgets-2`. It had 47 checks and used 8 mock model requests. It took 841 seconds.

The project name gets a number when an earlier run already used `dry-run-widgets`. `fleetctl pivot` never reuses the name of an archived project.

## Checks

| # | Check | Result | Seconds | Detail |
|---|---|---|---|---|
| 1 | fleetctl pivot starts the project | PASS | 3.1 |  |
| 2 | the project starts at Discover | PASS | 3.1 |  |
| 3 | the project has its own Kanban board | PASS | 4.6 | widgets-2 (empty) |
| 4 | Discover cards made, scoring card linked after three | PASS | 11.4 | cards t_f86c039a,t_46dcd1f3,t_a47dd177 then t_d07d804b |
| 5 | the research output matches the pain_report schema | PASS | 11.4 | [] |
| 6 | the Discover gate is met | PASS | 20.1 | 3 of 3 candidates, fewest complaints 10 of 10, paid proof yes |
| 7 | the highest total wins | PASS | 20.2 | c1 with 24 of {'c1': 24, 'c2': 20, 'c3': 21} |
| 8 | the decision record matches its fields | PASS | 20.2 |  |
| 9 | the decision is in STATE.md | PASS | 20.3 |  |
| 10 | the landing copy matches its schema | PASS | 20.3 | [] |
| 11 | an honest post goes to the dry-run outbox | PASS | 20.5 | {'status': 'dry_run', 'outbox': '1794580180-bluesky-c55d15f9.json'} |
| 12 | a dishonest post is blocked by the honesty filter | PASS | 20.5 | honesty |
| 13 | an injected post is blocked by the live injection screen | PASS | 20.6 | injection |
| 14 | a number without evidence is blocked | PASS | 20.6 | honesty |
| 15 | the same number passes with evidence from the plain-code store | PASS | 20.7 |  |
| 16 | the Validate gate is met (20 signups, simulated) | PASS | 20.7 | 23 of 20 signups, 0 of 5 yes replies, day 0 of 14 |
| 17 | the sample app passes its own tests | PASS | 21.3 | 7 passed, 0 failed |
| 18 | the Deployer refuses a commit that is not merged | PASS | 21.3 | not_merged |
| 19 | the merger merges the app branch (all six gates) | PASS | 347.5 | {'branch': 'agent/build/dry-run-widgets-mvp-20261113-082919', 'gates': {'G1': 'pass', 'G2': 'pass', 'G3': 'pas |
| 20 | the merged commit is on main | PASS | 347.5 |  |
| 21 | the merger recorded a pass for the Deployer | PASS | 347.5 |  |
| 22 | the Deployer plan is dry-run only, free plan, nothing deployed | PASS | 347.5 | fleet-dry-run-widgets-2 |
| 23 | the Build MVP gate is met (stranger minutes simulated) | PASS | 347.6 | tests pass yes, a stranger gets value in 3 of 5 minutes |
| 24 | the launch post goes to the dry-run outbox | PASS | 347.9 |  |
| 25 | the Launch gate is met (10 users, simulated) | PASS | 347.9 | 12 of 10 active users |
| 26 | the First customer gate is NOT met, because payments need the owner | PASS | 348.0 | 0 of 1 payments. Needs the owner to set up payments. |
| 27 | the brief lists the missing payment account under Limits | PASS | 348.2 | No payment account, so the first customer cannot pay yet. No model provider key. No control bot. No cloudflare |
| 28 | two real messages reach the inbox, the injection goes to quarantine | PASS | 348.7 | ['stored', 'stored', 'quarantined'] |
| 29 | no email address or raw user id is in the inbox | PASS | 348.7 |  |
| 30 | the feedback agent files a ticket that matches the schema | PASS | 348.7 | t-1794580508-d51b1ae3fe |
| 31 | the ticket tools list it as open | PASS | 348.7 |  |
| 32 | the fix branch has a new test and all tests pass | PASS | 349.1 | 8 passed |
| 33 | the fix report matches its schema | PASS | 349.1 |  |
| 34 | the merger auto-merges the fix branch | PASS | 675.1 | result merged. Gates G1 G2 G3 G4 G5 G6: all pass |
| 35 | the fix is on main | PASS | 675.1 |  |
| 36 | the ticket is marked fixed | PASS | 675.1 |  |
| 37 | the Iterate gate is met (fix time, simulated) | PASS | 675.2 | tickets are fixed in 1 of 72 hours |
| 38 | a forced health failure makes the merger roll back | PASS | 837.8 | result rolled_back. Gates G1 G2 G3 G4 G5 G6: all pass |
| 39 | the live code is back at the earlier commit | PASS | 837.8 |  |
| 40 | the merger filed a ticket for the failed deploy | PASS | 837.8 | ['t-1794580997-0d22839e74'] |
| 41 | the brief has four parts and 150 words or fewer | PASS | 838.0 | 74 words, written by the model |
| 42 | the plain-code brief also works without a model | PASS | 838.0 |  |
| 43 | fleetctl pivot archives the project | PASS | 839.5 | Archived to projects/_graveyard/dry-run-widgets-2-20261113 |
| 44 | the folder moved to the graveyard with a lessons file | PASS | 839.5 |  |
| 45 | the board is archived, not deleted | PASS | 840.9 | the board list no longer shows it as active |
| 46 | no project is active now | PASS | 840.9 |  |
| 47 | the test tickets moved to the archive folder | PASS | 840.9 | 2 moved to /var/lib/agent-fleet/dryrun-archive/20261113-082919 |

## Sample brief

The brief has four parts and 150 words or fewer. The model wrote the first three parts.
Plain code wrote the last part from real blockers.

```text
Achieved: Ran the dry-run project through every stage.
Next: Wait for owner keys. Then start the Discover stage on live models.
Limits: No model keys yet. No payment account yet.
What you can do:
- No model provider key. Run `fleetctl setup` and add a free provider key.
- No control bot. Run `fleetctl secret wizard --group control` to add a bot.
- No cloudflare token. Run `fleetctl secret wizard --group deployer`. Guide: docs/HUMAN_STEPS.md.
```

## What the run proves

- The stage gates read their numbers from `STATE.md` and compare them with `config/stages.yaml`.
- A dishonest post, a number without evidence, and an injected post never reach the outbox.
- Replies and form messages lose their personal data before an agent sees them. An injection goes to quarantine.
- The merger auto-merges a good branch, records the pass for the Deployer, and rolls back a change that fails the health check.
- The Deployer refuses a commit that is not merged. For a merged commit it makes only a free-plan dry-run plan.
- `fleetctl pivot` archives a project and its board without deleting anything.
- The first customer gate stays open, because payments need the owner. The brief lists it under Limits.

## What the dry run found

The dry run found real bugs. Each one is fixed and has a test.

- The Deployer would have refused every deploy, because nothing told it which commits passed the gates. See D-025.
- The merger could not find `runuser`, because its `PATH` had no `/usr/sbin`.
- After a rollback, the working clone of the agents stayed on the rolled-back commit. See D-028.
