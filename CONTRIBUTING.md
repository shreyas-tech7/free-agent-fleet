# Contributing

Thank you for helping. This project accepts changes through pull requests.

## Set up

1. Fork the repository and clone your fork.
2. Run the tests: `python3 -m pytest`. The policy, unit, and injection suites must pass.
3. Run `python3 scripts/lint_docs.py` for the docs. See the style rules below.

## Rules for a change

- Keep a change small. One idea in one pull request.
- Add a test for each change in behavior. Write the test first when you can.
- Do not add a paid service, a paid model, or a trial that needs a card. A policy test checks the router.
- Do not add a secret, a real address, or a real name. The CI runs gitleaks and a pattern scan.
- Do not touch `core/` unless the change is about the locked core. The owners review those changes with extra care.
- Pin every new dependency to an exact version. Record it in `vendor/LOCK.md` with its source and license.
- New skills, tools, and servers need the install gate: owner and name check, license check, scanners, and a sandbox trial.

## Docs style

Write in short sentences and active voice. Use simple words. Do not use em dashes or semicolons. Keep a sentence under 33 words.

## Security problems

See [SECURITY.md](SECURITY.md).
