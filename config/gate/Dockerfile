# The gate image. The merger runs agent branches inside a throwaway container from this image.
# At run time the container has no network, a read-only root file system, and no capabilities.
# Build time needs the network once (Debian archive and PyPI). Every Python package is hash-locked.
# The base image is pinned by digest (vendor/pins.env, GATE_BASE_IMAGE).
ARG BASE
FROM ${BASE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8

# git: some tests make a throwaway repository. ca-certificates: the scanners read local files only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Static scanner binaries and Node.js come from the pinned installs on the host (see vendor/LOCK.md).
COPY bin/gitleaks bin/osv-scanner bin/node /usr/local/bin/

# One venv for each tool. semgrep needs mcp 1.x. The Cisco mcp-scanner needs mcp 2.x.
COPY req/ /req/
RUN set -eux; \
    for n in test semgrep skillscan mcpscan; do \
      python -m venv "/opt/$n"; \
      "/opt/$n/bin/pip" install --require-hashes --only-binary=:all: --no-deps -r "/req/gate-$n-requirements.txt"; \
    done; \
    ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep; \
    ln -s /opt/skillscan/bin/skill-scanner /usr/local/bin/skill-scanner; \
    ln -s /opt/mcpscan/bin/mcp-scanner /usr/local/bin/mcp-scanner; \
    rm -rf /req

RUN useradd --uid 65534 --no-create-home --shell /usr/sbin/nologin gate 2>/dev/null || true
WORKDIR /work
USER 65534:65534
CMD ["/opt/test/bin/python", "/gate/gate_entry.py"]
