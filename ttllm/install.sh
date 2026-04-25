#!/usr/bin/env bash
set -euo pipefail

# Install ttllm bridge dependencies into the whisperX-rocm venv so we share
# its torch-ROCm / ctranslate2-rocm / whisperx stack.

VENV="${WHISPERX_VENV:-/home/$USER/AIzunda/whisperX-rocm/.venv}"

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "whisperX venv not found at $VENV" >&2
    echo "Create it first: cd ~/AIzunda/whisperX-rocm && uv venv && uv pip install -e ." >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    VIRTUAL_ENV="$VENV" uv pip install --upgrade \
        "fastapi>=0.110" \
        "uvicorn[standard]>=0.27" \
        "python-multipart>=0.0.9" \
        "httpx>=0.27" \
        "pydantic>=2"
else
    "$VENV/bin/python" -m ensurepip --upgrade
    "$VENV/bin/python" -m pip install --upgrade \
        "fastapi>=0.110" \
        "uvicorn[standard]>=0.27" \
        "python-multipart>=0.0.9" \
        "httpx>=0.27" \
        "pydantic>=2"
fi

echo "Dependencies installed into $VENV"
