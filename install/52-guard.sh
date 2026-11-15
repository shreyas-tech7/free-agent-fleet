#!/usr/bin/env bash
# Phase 4, step 5: the injection screen.
#   - a Python venv with onnxruntime, tokenizers, and numpy, from a hash-locked requirements file
#   - the model files from the pinned Hugging Face revision, each checked against its SHA-256
# Safe to run twice. The model download is about 740 MB.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

REPO=/opt/agent-fleet/repo
export FLEET_REPO_DIR="$REPO"
VENV=/opt/agent-fleet/venvs/guard
MODEL_DIR=/var/lib/agent-fleet-ops/guard/model

install -d -m 0755 -o root -g root /opt/agent-fleet/venvs
if [ ! -x "$VENV/bin/python" ]; then
  log "make the venv"
  uv venv --quiet --python /usr/bin/python3 "$VENV"
fi
uv pip install --quiet --python "$VENV/bin/python" --require-hashes -r "$REPO/vendor/guard-requirements.txt"
"$VENV/bin/python" -c "import onnxruntime, tokenizers, numpy; print('guard venv: onnxruntime', onnxruntime.__version__, 'tokenizers', tokenizers.__version__)"
for pkg in onnxruntime tokenizers numpy; do
  v=$("$VENV/bin/python" -c "import importlib.metadata as m; print(m.version('$pkg'))")
  lock_add --name "$pkg" --kind pypi --source "https://pypi.org/project/$pkg/" --version "$v" --sha256 "see vendor/guard-requirements.txt" --license "see PyPI"
done

# ---- model ----
REPO_ID=$(pin GUARD_MODEL_REPO)
REV=$(pin GUARD_MODEL_REV)
install -d -m 0750 -o fleetops -g fleetops "$MODEL_DIR"
fetch_model_file() {   # fetch_model_file NAME REMOTE_PATH PIN_KEY
  local name=$1 remote=$2 key=$3 want got dest="$MODEL_DIR/$1"
  want=$(pin "$key")
  if [ -f "$dest" ] && [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$want" ]; then return 0; fi
  log "download $name"
  curl -fL --retry 5 --retry-delay 3 -C - -o "$dest.part" "https://huggingface.co/${REPO_ID}/resolve/${REV}/${remote}"
  got=$(sha256sum "$dest.part" | cut -d' ' -f1)
  [ "$got" = "$want" ] || { rm -f "$dest.part"; die "$name has the wrong SHA-256. Expected $want and got $got."; }
  mv "$dest.part" "$dest"
}
fetch_model_file model.onnx onnx/model.onnx GUARD_SHA256_MODEL_ONNX
fetch_model_file tokenizer.json onnx/tokenizer.json GUARD_SHA256_TOKENIZER_JSON
fetch_model_file config.json onnx/config.json GUARD_SHA256_CONFIG_JSON
chown -R fleetops:fleetops "$MODEL_DIR"; chmod 0640 "$MODEL_DIR"/*
lock_add --name deberta-v3-base-prompt-injection-v2 --kind model --source "https://huggingface.co/${REPO_ID}/tree/${REV}" --version "$REV" --sha256 "$(pin GUARD_SHA256_MODEL_ONNX)" --license "Apache-2.0"
log "step 52-guard done"
