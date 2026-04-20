#!/usr/bin/env bash
set -euo pipefail

# vtt 用の軽量 venv をローカルに作る。ttllm / whisperX-rocm の venv とは独立させ、
# 音声キャプチャに必要な軽量パッケージだけ入れる。

cd "$(dirname "$(readlink -f "$0")")"

if ! PATH="$PATH:/sbin:/usr/sbin" ldconfig -p 2>/dev/null | grep -q libportaudio; then
    cat >&2 <<'EOF'
[warn] libportaudio2 が見つかりません。sounddevice の import が失敗します。
       先に次を実行してください:
         sudo apt-get install -y libportaudio2
EOF
fi

if command -v uv >/dev/null 2>&1; then
    uv venv
    VIRTUAL_ENV="$(pwd)/.venv" uv pip install --upgrade \
        "numpy>=1.26" \
        "sounddevice>=0.4.7" \
        "soundfile>=0.12" \
        "httpx>=0.27"
else
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install \
        "numpy>=1.26" \
        "sounddevice>=0.4.7" \
        "soundfile>=0.12" \
        "httpx>=0.27"
fi

echo "vtt deps installed into $(pwd)/.venv"
