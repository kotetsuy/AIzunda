#!/usr/bin/env bash
set -euo pipefail

# ROCm env for whisperX on AMD Ryzen AI Max+ 395 (gfx1151).
export HSA_OVERRIDE_GFX_VERSION="${HSA_OVERRIDE_GFX_VERSION:-11.5.1}"
export ROCM_PATH="${ROCM_PATH:-/opt/rocm}"
export HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"
export LD_LIBRARY_PATH="/usr/local/lib:/opt/rocm/lib:/opt/rocm/lib/llvm/lib:${LD_LIBRARY_PATH:-}"

VENV="${WHISPERX_VENV:-/home/$USER/AIzunda/whisperX-rocm/.venv}"
HOST="${BRIDGE_HOST:-0.0.0.0}"
PORT="${BRIDGE_PORT:-8001}"

cd "$(dirname "$(readlink -f "$0")")"
exec "$VENV/bin/python" -m uvicorn server:app --host "$HOST" --port "$PORT" "$@"
