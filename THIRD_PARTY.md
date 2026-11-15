# Third-party software

free-agent-fleet is MIT licensed. It installs and calls the software below. Each part keeps its own license.
The list comes from `vendor/LOCK.md`, which records every install with its source and pinned version.

Some licenses need your attention:

- AGPL: run it as a separate service. Do not copy its code into this project.
- GPL: run it as a separate tool. Do not copy its code into this project.

| Name | Kind | Source | Version | License |
|---|---|---|---|---|
| acl | apt | Debian or Ubuntu archive | 2.3.2-2+b1 | GPL-2+, LGPL-2+ |
| agent-fleet-gate | docker-image | built from config/gate/Dockerfile on python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2c | agent-fleet-gate:5785368db6b2 | mixed, see the packages inside |
| build-essential | apt | Debian or Ubuntu archive | 12.12 | see copyright file |
| ca-certificates | apt | Debian or Ubuntu archive | 20250419+deb13u1 | GPL-2+, MPL-2.0 |
| chromium-for-playwright | binary | https://cdn.playwright.dev | chromium-1247 | BSD-3-Clause (Chromium), see the browser folder |
| cisco-ai-mcp-scanner | pypi | https://github.com/cisco-ai-defense/mcp-scanner | 4.8.7 | Apache-2.0 |
| cisco-ai-skill-scanner | pypi | https://github.com/cisco-ai-defense/skill-scanner | 2.3.2 | Apache-2.0 |
| containerd.io | apt | https://download.docker.com/linux | 2.4.2-4~debian.13~trixie | Apache-2.0 |
| crawl4ai | pypi | https://pypi.org/project/crawl4ai/ | 0.9.5 | Apache-2.0 |
| curl | apt | Debian or Ubuntu archive | 8.14.1-2+deb13u7 | curl, OLDAP-2.8, ISC |
| dbus-user-session | apt | Debian or Ubuntu archive | 1.16.2-2 | GPL-2+, BSD-3-clause and BSD-3-clause-generic, LGPL-2.1+ |
| deberta-v3-base-prompt-injection-v2 | model | https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2/tree/90c9989b1a342275 | 90c9989b1a342275dd0d1a95aad283c04e075671 | Apache-2.0 |
| docker-buildx-plugin | apt | https://download.docker.com/linux | 0.39.0-1~debian.13~trixie | see copyright file |
| docker-ce | apt | https://download.docker.com/linux | 5:29.9.3-1~debian.13~trixie | see copyright file |
| docker-ce-cli | apt | https://download.docker.com/linux | 5:29.9.3-1~debian.13~trixie | see copyright file |
| docker-ce-rootless-extras | apt | https://download.docker.com/linux | 5:29.9.3-1~debian.13~trixie | see copyright file |
| evolve-python-packages | pypi | https://pypi.org | see vendor/evolve-requirements.txt | various, see each package |
| file | apt | Debian or Ubuntu archive | 1:5.46-5 | BSD-2-Clause-alike, public-domain, BSD-2-Clause-netbsd |
| gate-test-packages | pypi | https://pypi.org | see vendor/gate-test-requirements.txt | MIT (pytest, jsonschema, PyYAML) |
| gh | apt | Debian or Ubuntu archive | 2.46.0-3 | Expat, BSD-3-Clause |
| git | apt | Debian or Ubuntu archive | 1:2.47.3-0+deb13u1 | GPL-2, BSD-3-clause, Zlib |
| gitleaks | binary | https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.t | v8.30.1 | MIT |
| gnupg | apt | Debian or Ubuntu archive | 2.4.7-21+deb13u1 | GPL-3+, permissive, LGPL-2.1+ |
| guarddog | pypi | https://pypi.org/project/guarddog/ | 3.2.0 | Apache-2.0 |
| hermes-agent | git | https://github.com/NousResearch/hermes-agent.git | v0.23.1 (3b421578588243f920886e6fe79a6fa | MIT (installer script sha256 shown) |
| hermes-agent-self-evolution | git | https://github.com/NousResearch/hermes-agent-self-evolution | 0a929e3aa20e15cf04dc7c28492a7d41a5139125 | MIT |
| hermes-sandbox | docker | docker.io/nousresearch/hermes-sandbox | nousresearch/hermes-sandbox@sha256:c7d86 | see image |
| humanizer-skill | git | https://github.com/blader/humanizer | b48c832f0f809c1b5658edbb838c2521b72f2413 | MIT |
| jq | apt | Debian or Ubuntu archive | 1.7.1-6+deb13u4 | MIT, CC-BY-3.0, Expat |
| libatomic1 | apt | Debian or Ubuntu archive | 14.2.0-19 | see copyright file |
| marketingskills-skills | git | https://github.com/coreyhaines31/marketingskills | 1f837447b20bb6f0e72d87b80ece1c221c362c7d | MIT |
| n8n-mcp | npm | https://www.npmjs.com/package/n8n-mcp | 2.102.0, with `@modelcontextprotocol/sdk | MIT |
| n8n-skills | git | https://github.com/czlonkowski/n8n-skills | v1.37.1 e037563e101749e630e6688d121d0452 | MIT |
| netcat-openbsd | apt | Debian or Ubuntu archive | 1.229-1 | BSD-3-Clause, BSD-2-Clause |
| node | binary | https://nodejs.org/dist/v24.22.3/node-v24.22.3-linux-x64.tar.xz | v24.22.3 | MIT |
| numpy | pypi | https://pypi.org/project/numpy/ | 2.5.4 | see PyPI |
| omniroute | docker | docker.io/diegosouzapw/omniroute | diegosouzapw/omniroute@sha256:b52a17661a | MIT |
| onnxruntime | pypi | https://pypi.org/project/onnxruntime/ | 1.31.1 | see PyPI |
| openssl | apt | Debian or Ubuntu archive | 3.5.7-1~deb13u3 | Apache-2.0, Artistic or GPL-1+, Artistic |
| osv-scanner | binary | https://github.com/google/osv-scanner/releases/download/v2.7.1/osv-scanner_linux_amd64 | v2.7.1 | Apache-2.0 |
| pipx | apt | Debian or Ubuntu archive | 1.7.1-1 | BSD-3-clause and Expat, GPL-3+, BSD-3-clause |
| playwright-mcp | npm | https://registry.npmjs.org/@playwright/mcp | 0.0.83 (playwright-core 1.64.0-alpha-179 | Apache-2.0 |
| playwright-system-libraries | apt | Debian archive, installed by playwright-core install-deps chromium | see /var/log/apt/history.log of the inst | various, see each package |
| python3 | apt | Debian or Ubuntu archive | 3.13.5-1 | see copyright file |
| python3-jsonschema | apt | Debian or Ubuntu archive | 4.19.2-6 | MIT, GPL-2+ |
| python3-pytest | apt | Debian or Ubuntu archive | 8.3.5-2 | Expat |
| python3-venv | apt | Debian or Ubuntu archive | 3.13.5-1 | see copyright file |
| python3-yaml | apt | Debian or Ubuntu archive | 6.0.2-1+b2 | see copyright file |
| rsync | apt | Debian or Ubuntu archive | 3.5.0+ds1-0+deb13u1 | GPL-3+, GPL-3+ and public-domain, GPL-3 |
| rtk | binary | https://github.com/rtk-ai/rtk/releases/download/v1.2.0/rtk-oss-x86_64-unknown-linux-musl.t | v1.2.0 | Apache-2.0 (rtk-oss edition) |
| searxng | docker | docker.io/searxng/searxng | searxng/searxng@sha256:0b104e2e1117b0ca8 | AGPL-3.0 |
| semgrep | pypi | https://pypi.org/project/semgrep/ | 1.185.0 | LGPL-2.1-or-later |
| semgrep-rules | git | https://github.com/semgrep/semgrep-rules | a5ad1714f00c9f7fde980cab0ea1d6dc6d414fd2 | Semgrep Rules License v1.0 (not redistributed in this repository) |
| slirp4netns | apt | Debian or Ubuntu archive | 1.2.1-1.1 | GPL-2+, GPL-2, LGPL-2.1 |
| sqlite3 | apt | Debian or Ubuntu archive | 3.46.1-7+deb13u2 | public-domain, GPL-2+ |
| superpowers-skills | git | https://github.com/obra/superpowers | 22f0b463e7a5e579904dd6211a520321035526f7 | MIT |
| tailscale | apt | https://pkgs.tailscale.com/stable | 1.104.7 | see copyright file |
| tmux | apt | Debian or Ubuntu archive | 3.5a-3 | ISC, BSD-3-clause, BSD-2-clause or ISC |
| tokenizers | pypi | https://pypi.org/project/tokenizers/ | 0.23.3 | see PyPI |
| ufw | apt | Debian or Ubuntu archive | 0.36.2-9 | GPL-3, BSD-3-clause |
| uidmap | apt | Debian or Ubuntu archive | 1:4.17.4-2 | BSD-3-clause, GPL-1, GPL-2+ |
| unattended-upgrades | apt | Debian or Ubuntu archive | 2.12 | GPL-2+ |
| unzip | apt | Debian or Ubuntu archive | 6.0-29+deb13u1 | see copyright file |
| uv | binary | https://astral.sh/uv/0.13.8/install.sh | 0.13.8 | MIT or Apache-2.0 |
| xz-utils | apt | Debian or Ubuntu archive | 5.8.1-1+deb13u2 | 0BSD, GPL-2+, LGPL-2.1+ |
