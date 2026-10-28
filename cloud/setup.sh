#!/bin/sh
# Codex Cloud environment install command: sh cloud/setup.sh
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"
if [ ! -x .cloud-venv/bin/python ]; then
    python3 -m venv .cloud-venv
fi
.cloud-venv/bin/python -m pip install -r requirements.lock
exec sh cloud/check.sh
