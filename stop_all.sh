#!/usr/bin/env bash
# AIzunda パイプライン停止スクリプト。
#
#   ./stop_all.sh              → tmux セッション + VOICEVOX コンテナを停止
#   ./stop_all.sh --keep-voicevox → VOICEVOX は動かしたまま残す
#
# Chrome は閉じない (ユーザの操作を奪わない)。必要なら手で閉じる。

set -euo pipefail

SESSION="aizunda"
VOICEVOX_CONTAINER="voicevox_engine"
KEEP_VOICEVOX=0

for arg in "$@"; do
    case "$arg" in
        --keep-voicevox) KEEP_VOICEVOX=1 ;;
        -h|--help)
            sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf '\033[1;34m[stop]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[stop]\033[0m %s\n' "$*" >&2; }

# ---- 1. tmux セッションを落とす --------------------------------------

if tmux has-session -t "$SESSION" 2>/dev/null; then
    log "tmux セッション ${SESSION} を終了します"
    tmux kill-session -t "$SESSION"
else
    log "tmux セッション ${SESSION} は起動していません"
fi

# ---- 2. 取りこぼしプロセスを止める -----------------------------------
# tmux のウィンドウを kill-session で閉じるとその中のプロセスも
# 親が消えて SIGHUP で落ちるが、SIGHUP を握り潰すものもあるので保険。

PATTERNS=(
    "llama.cpp/build/bin/llama-server"
    "AIzunda/ttllm/server:app"
    "AIzunda/three-vrm/server.py"
    "AIzunda/vtt/vtt.py"
)

for pat in "${PATTERNS[@]}"; do
    pids=$(pgrep -f "$pat" || true)
    if [[ -n "${pids}" ]]; then
        log "残存プロセスを停止: $pat (pid=${pids//$'\n'/,})"
        # shellcheck disable=SC2086
        kill ${pids} 2>/dev/null || true
        sleep 1
        # まだ生きていれば SIGKILL
        pids=$(pgrep -f "$pat" || true)
        if [[ -n "${pids}" ]]; then
            # shellcheck disable=SC2086
            kill -9 ${pids} 2>/dev/null || true
        fi
    fi
done

# ---- 3. VOICEVOX docker --------------------------------------------

if (( KEEP_VOICEVOX == 0 )); then
    if docker ps --format '{{.Names}}' | grep -qx "$VOICEVOX_CONTAINER"; then
        log "VOICEVOX コンテナ (${VOICEVOX_CONTAINER}) を停止します"
        docker stop "$VOICEVOX_CONTAINER" >/dev/null
    else
        log "VOICEVOX コンテナは既に停止しています"
    fi
else
    log "VOICEVOX は残します (--keep-voicevox)"
fi

log "停止完了"
