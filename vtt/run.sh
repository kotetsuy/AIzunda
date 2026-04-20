#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

if [[ ! -x ".venv/bin/python" ]]; then
    echo ".venv が見つかりません。先に ./install.sh を実行してください。" >&2
    exit 1
fi

exec .venv/bin/python vtt.py "$@"
