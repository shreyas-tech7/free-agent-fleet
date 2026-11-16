# Vendor lock

This file records every piece of software that the build installed.
Rule R6: install only from the official source, and pin an exact version or commit.
Each row names the install, its kind, its source, its pinned version or commit,
its SHA-256 when one exists, and its license.
Kinds: apt, binary, npm, pypi, docker, git, model, script.

| Name | Kind | Source | Version or commit | SHA-256 | License |
|---|---|---|---|---|---|
| acl | apt | Debian or Ubuntu archive | 2.3.2-2+b1 | fa89b05576c590211c8e8965761fc76b830e3eeb3dd100ac59c8440f11ea18fd | GPL-2+, LGPL-2+ |
| build-essential | apt | Debian or Ubuntu archive | 12.12 | a45159aebea0d98d6652ac5a56b7fff164a7733a7dc736b9f8715ca504519d3a | see copyright file |
| ca-certificates | apt | Debian or Ubuntu archive | 20250419+deb13u1 | c10585ac22c7f617a73d394eea29ba71d0356f350ae973b19043d1b017f80f54 | GPL-2+, MPL-2.0 |
| containerd.io | apt | https://download.docker.com/linux | 2.4.2-4~debian.13~trixie | a23e96d1dda8900cc46b96d4f41c73222c3a706a6a593e98f6095a916662bf02 | Apache-2.0 |
| curl | apt | Debian or Ubuntu archive | 8.14.1-2+deb13u7 | 135636bd8b713c582d33e50ec4e36ea55cc3c5da3f12bbeb6b87d2bcbd3a0649 | curl, OLDAP-2.8, ISC |
| dbus-user-session | apt | Debian or Ubuntu archive | 1.16.2-2 | 0f9aa775a708b28b1a3ad784e9d1f0c96227521bc29e99ddbfe05ff008b5d246 | GPL-2+, BSD-3-clause and BSD-3-clause-generic, LGPL-2.1+ |
| docker-buildx-plugin | apt | https://download.docker.com/linux | 0.39.0-1~debian.13~trixie | c5993ad8496d20c8e14e700fd51d0a84a1d18627dcd6cce483f78a8bf796322b | see copyright file |
| docker-ce | apt | https://download.docker.com/linux | 5:29.9.3-1~debian.13~trixie | 7ad5e9909874f635e438baa83a7033f1b5cb20d713a15eae73e930b82fb70cef | see copyright file |
| docker-ce-cli | apt | https://download.docker.com/linux | 5:29.9.3-1~debian.13~trixie | d75fe7383c11d1399736f937b2146019f0b38801c96358664dd3c85ca97188f0 | see copyright file |
| docker-ce-rootless-extras | apt | https://download.docker.com/linux | 5:29.9.3-1~debian.13~trixie | c36f495851b3f8104260e7d5e63ccbd468d3e718e66cef5370869c5a789b95ed | see copyright file |
| file | apt | Debian or Ubuntu archive | 1:5.46-5 | 5b980b02a7d91aab9cb50c65ac7e059678a36d36ed626ec52506442f4501a1a7 | BSD-2-Clause-alike, public-domain, BSD-2-Clause-netbsd |
| gh | apt | Debian or Ubuntu archive | 2.46.0-3 | 4af2d243a11c94ade52aa2e66bb28d8a048fc05f95e2f7cb0e0ecad2343ea872 | Expat, BSD-3-Clause |
| git | apt | Debian or Ubuntu archive | 1:2.47.3-0+deb13u1 | 3e35662fd5c46add561703e54031a1d8ad9df45811927689f0a51122b13be722 | GPL-2, BSD-3-clause, Zlib |
| gnupg | apt | Debian or Ubuntu archive | 2.4.7-21+deb13u1 | e68f0fd24a7a62f1112ae3e442564e134f21040492f11d1aefa7b18de93ebf6e | GPL-3+, permissive, LGPL-2.1+ |
| jq | apt | Debian or Ubuntu archive | 1.7.1-6+deb13u4 | 7350392f700e20e46b7ae174a57f54e4ea106dd559753646d629b4e618bf2bff | MIT, CC-BY-3.0, Expat |
| libatomic1 | apt | Debian or Ubuntu archive | 14.2.0-19 | 212b399aae2f7299203d261a57e49372e09565a9a5ea971905f94a3960366c05 | see copyright file |
| netcat-openbsd | apt | Debian or Ubuntu archive | 1.229-1 | d24b18012d4567e9b8fce551293a2373c2bfea5373d012d7f7e81bf5bf17b13e | BSD-3-Clause, BSD-2-Clause |
| openssl | apt | Debian or Ubuntu archive | 3.5.7-1~deb13u3 | 43a9cbcf2a8a0e1f0a1c3f5a33ee1f56b269690e7a2129f2b2d0582e789589d7 | Apache-2.0, Artistic or GPL-1+, Artistic |
| pipx | apt | Debian or Ubuntu archive | 1.7.1-1 | 18bb66b35e69cc63f3bc95b92a2996e72b437df51e97d9e5bb8816490b5823e9 | BSD-3-clause and Expat, GPL-3+, BSD-3-clause |
| playwright-system-libraries | apt | Debian archive, installed by playwright-core install-deps chromium | see /var/log/apt/history.log of the install | apt verifies each package | various, see each package |
| python3 | apt | Debian or Ubuntu archive | 3.13.5-1 | c1388174dc7140e6c19a6dd39f8a0dcb38a835cd66c65644e0dded43d5223cb0 | see copyright file |
| python3-jsonschema | apt | Debian or Ubuntu archive | 4.19.2-6 | e826c2777771ca3852bf3ea4c5df11d8bec99648375c849225eb557411d1b694 | MIT, GPL-2+ |
| python3-pytest | apt | Debian or Ubuntu archive | 8.3.5-2 | f933f7b38d1fd2c140684a44fecc7aa23cd6d08364604384c14c4cc21de8f8f9 | Expat |
| python3-venv | apt | Debian or Ubuntu archive | 3.13.5-1 | 744ea5038e645ed22299e1bca47f76169b5665dd640df3492bdd1e21eba6cb76 | see copyright file |
| python3-yaml | apt | Debian or Ubuntu archive | 6.0.2-1+b2 | 0019c4e7848c83a0d6bcb78cca902b5cdb1714aa6370ba01955b1fed6f667056 | see copyright file |
| rsync | apt | Debian or Ubuntu archive | 3.5.0+ds1-0+deb13u1 | 4e7c9ceffc110e23e91177811fb02c7033b161d327f696f1b8f2f57d2edb92c5 | GPL-3+, GPL-3+ and public-domain, GPL-3 |
| slirp4netns | apt | Debian or Ubuntu archive | 1.2.1-1.1 | c294a4df952b9ae54e19e0ab19ac09a7e398fbf7aa8969cfcae07f92dc4f30ac | GPL-2+, GPL-2, LGPL-2.1 |
| sqlite3 | apt | Debian or Ubuntu archive | 3.46.1-7+deb13u2 | 877b00bd5ab6a3792fdffebd4aeebe0b8178df3c21f0e06f6a3c5186f7a799d9 | public-domain, GPL-2+ |
| tailscale | apt | https://pkgs.tailscale.com/stable | 1.104.7 | a41efd469875e27b4d8f313e07476f9bae459bafab305a3f2621ff5b5254a885 | see copyright file |
| tmux | apt | Debian or Ubuntu archive | 3.5a-3 | e6b6aa51780044e2c4a0e618c33f832b70ec80caa30303e6c254a0c851b5af78 | ISC, BSD-3-clause, BSD-2-clause or ISC |
| ufw | apt | Debian or Ubuntu archive | 0.36.2-9 | 7017dd1b5fd5a1755bc0095ef464281de72a07ab15f4bda3b9181ca371892026 | GPL-3, BSD-3-clause |
| uidmap | apt | Debian or Ubuntu archive | 1:4.17.4-2 | a017182d42a32ca241cd830a59153488e1f65ae6e31a760014575878044a9d2d | BSD-3-clause, GPL-1, GPL-2+ |
| unattended-upgrades | apt | Debian or Ubuntu archive | 2.12 | a5e37d196598d2f2a7cb8c3ded760b77283d0ecf110d2f90d77d4821e3512eb6 | GPL-2+ |
| unzip | apt | Debian or Ubuntu archive | 6.0-29+deb13u1 | fc11f8736bdec6fc46a6864f75bce0c8206a790df37dbcab5ddb25b1bf0f58f0 | see copyright file |
| xz-utils | apt | Debian or Ubuntu archive | 5.8.1-1+deb13u2 | 66b060ef2f09722c947ba019da5e9a63f6bac9d7bf90b32339a72c0390acc53f | 0BSD, GPL-2+, LGPL-2.1+ |
| chromium-for-playwright | binary | https://cdn.playwright.dev (pinned by playwright-core, revision 1247, plus the headless shell and ffmpeg 1011) | chromium-1247 | none published by the vendor. The revision is pinned by the playwright-core version in vendor/playwright-mcp/package-lock.json | BSD-3-Clause (Chromium), see the browser folder |
| gitleaks | binary | https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz | v8.30.1 | 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb | MIT |
| node | binary | https://nodejs.org/dist/v24.22.3/node-v24.22.3-linux-x64.tar.xz | v24.22.3 | 4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb | MIT |
| osv-scanner | binary | https://github.com/google/osv-scanner/releases/download/v2.7.1/osv-scanner_linux_amd64 | v2.7.1 | fb7dab19a8bd9bbe5c3189f09d4b7ee1820c9553a23bac202648a56efd2f6ee8 | Apache-2.0 |
| rtk | binary | https://github.com/rtk-ai/rtk/releases/download/v1.2.0/rtk-oss-x86_64-unknown-linux-musl.tar.gz | v1.2.0 | a28407dc13038bdc9c2a1f5a047b0c636e0c1d495b15523576e2adb992a06a17 | Apache-2.0 (rtk-oss edition) |
| uv | binary | https://astral.sh/uv/0.13.8/install.sh | 0.13.8 | 4a7607589912d9c20e86b860b032f7c7f2913c9a3e76f46aef136885387452b8 | MIT or Apache-2.0 |
| gate-base | docker | docker.io/python:3.13-slim-trixie | python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 | e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 | PSF-2.0 and others (Python official image) |
| hermes-sandbox | docker | docker.io/nousresearch/hermes-sandbox | nousresearch/hermes-sandbox@sha256:c7d8607d076f26a64e0bd6828f74e1764af43fe3281368188459bb693bfa7567 | c7d8607d076f26a64e0bd6828f74e1764af43fe3281368188459bb693bfa7567 | see image |
| n8n | docker | docker.n8n.io/n8nio/n8n (official image of github.com/n8n-io/n8n) | 2.47.4 | 56a3f0d56c2888b53ca7d89de4aa3de702888f33667c96a1f1d7118548f979e9 | Sustainable Use License |
| omniroute | docker | docker.io/diegosouzapw/omniroute | diegosouzapw/omniroute@sha256:b52a17661a1aff42ffb78f349f423e986dfc55b57274f28559f8387622ef8ee4 | b52a17661a1aff42ffb78f349f423e986dfc55b57274f28559f8387622ef8ee4 | MIT |
| searxng | docker | docker.io/searxng/searxng | searxng/searxng@sha256:0b104e2e1117b0ca83a36f3ac6ecda7d9ee948ccea6ead74750a03e1b856229a | 0b104e2e1117b0ca83a36f3ac6ecda7d9ee948ccea6ead74750a03e1b856229a | AGPL-3.0 |
| agent-fleet-gate | docker-image | built from config/gate/Dockerfile on python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 | agent-fleet-gate:5785368db6b2 | sha256:2c1a8585ef70a080ae8e247cd2794c6501cafbe214cbd5876e9db89eb5a40261 | mixed, see the packages inside |
| hermes-agent | git | https://github.com/NousResearch/hermes-agent.git | v0.23.1 (3b421578588243f920886e6fe79a6faa87ea026e) | abf338605dae6dd28d101673ac6aed7995c955745db46d47676bc591a7957209 | MIT (installer script sha256 shown) |
| hermes-agent-self-evolution | git | https://github.com/NousResearch/hermes-agent-self-evolution | 0a929e3aa20e15cf04dc7c28492a7d41a5139125 | fb50c3dbf29d719e29a24c271ae9fa7329fc6798910c261be5548f75151a812d (source archive) | MIT |
| humanizer-skill | git | https://github.com/blader/humanizer | b48c832f0f809c1b5658edbb838c2521b72f2413 | per file in skills/*/UPSTREAM.md (1 skill) | MIT |
| marketingskills-skills | git | https://github.com/coreyhaines31/marketingskills | 1f837447b20bb6f0e72d87b80ece1c221c362c7d | per file in skills/*/UPSTREAM.md (15 skills) | MIT |
| n8n-skills | git | https://github.com/czlonkowski/n8n-skills (skills only, markdown only, not the hooks, not mcp.json) | v1.37.1 e037563e101749e630e6688d121d04529fe6ee7b | n/a, pinned by commit | MIT |
| semgrep-rules | git | https://github.com/semgrep/semgrep-rules | a5ad1714f00c9f7fde980cab0ea1d6dc6d414fd2 | n/a (git commit) | Semgrep Rules License v1.0 (not redistributed in this repository) |
| superpowers-skills | git | https://github.com/obra/superpowers | 22f0b463e7a5e579904dd6211a520321035526f7 | per file in skills/*/UPSTREAM.md (7 skills) | MIT |
| deberta-v3-base-prompt-injection-v2 | model | https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2/tree/90c9989b1a342275dd0d1a95aad283c04e075671 | 90c9989b1a342275dd0d1a95aad283c04e075671 | f0ea7f239f765aedbde7c9e163a7cb38a79c5b8853d3f76db5152172047b228c | Apache-2.0 |
| n8n-mcp | npm | https://www.npmjs.com/package/n8n-mcp (repository https://github.com/czlonkowski/n8n-mcp). Pinned with `vendor/n8n-mcp/package-lock.json`. Installed by `scripts/n8n-mcp-install.sh` with scripts off | 2.102.0, with `@modelcontextprotocol/sdk` overridden to 1.32.2 | 3703e9743965a894a490af14906600ab68f340a2b3b0ad0c2c0507eda6e71e4a (tarball). npm integrity sha512-ODC0ffkR4RlsQjvViQI+pbY1UknG/gqd0cNb6VqVslQ71Ax28Rvr0WDZEha63MLYvaqDpTq8tqzClV23p7TXNQ== | MIT |
| playwright-mcp | npm | https://registry.npmjs.org/@playwright/mcp | 0.0.83 (playwright-core 1.64.0-alpha-1790635538000) | see vendor/playwright-mcp/package-lock.json | Apache-2.0 |
| cisco-ai-mcp-scanner | pypi | https://github.com/cisco-ai-defense/mcp-scanner | 4.8.7 | hashes in vendor/gate-mcpscan-requirements.txt | Apache-2.0 |
| cisco-ai-skill-scanner | pypi | https://github.com/cisco-ai-defense/skill-scanner | 2.3.2 | hashes in vendor/gate-skillscan-requirements.txt | Apache-2.0 |
| crawl4ai | pypi | https://pypi.org/project/crawl4ai/ | 0.9.5 | see vendor/reader-requirements.txt | Apache-2.0 |
| evolve-python-packages | pypi | https://pypi.org | see vendor/evolve-requirements.txt | a hash for each package in that file | various, see each package |
| gate-test-packages | pypi | https://pypi.org | see vendor/gate-test-requirements.txt | hashes in vendor/gate-test-requirements.txt | MIT (pytest, jsonschema, PyYAML) |
| guarddog | pypi | https://pypi.org/project/guarddog/ (a scan tool, run once from a throwaway venv, not installed in the fleet) | 3.2.0 | see PyPI | Apache-2.0 |
| numpy | pypi | https://pypi.org/project/numpy/ | 2.5.4 | see vendor/guard-requirements.txt | see PyPI |
| onnxruntime | pypi | https://pypi.org/project/onnxruntime/ | 1.31.1 | see vendor/guard-requirements.txt | see PyPI |
| semgrep | pypi | https://pypi.org/project/semgrep/ | 1.185.0 | hashes in vendor/gate-semgrep-requirements.txt | LGPL-2.1-or-later |
| slack-bolt | pypi | https://pypi.org/project/slack-bolt/ | 1.30.0 | see vendor/control-requirements.txt | MIT |
| slack-sdk | pypi | https://pypi.org/project/slack-sdk/ | 3.46.0 | see vendor/control-requirements.txt | MIT |
| tokenizers | pypi | https://pypi.org/project/tokenizers/ | 0.23.3 | see vendor/guard-requirements.txt | see PyPI |
