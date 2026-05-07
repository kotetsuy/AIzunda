#!/usr/bin/env bash
# AIzunda パイプライン一括起動スクリプト。
#
# 起動順:
#   1. VOICEVOX (docker)           :50021
#   2. llama-server (qwen3.6)      :8080
#   3. ttllm (WhisperX ↔ llama)    :8001  → /warmup 叩く
#   4. three-vrm (VRM ビューア)    :8000
#   5. Chrome で zundamon.html を開く
#   6. vtt を PTT モードで起動 (任意 / ブラウザの 🎤 ボタンがメイン動線)
#
# 各サービスは tmux セッション "aizunda" の別ウィンドウで走らせる。
# 終了するには:   tmux kill-session -t aizunda
# ログを見るには: tmux attach -t aizunda

set -euo pipefail

# Chrome / GUIアプリがPipeWireのpulseソケットに確実に接続できるよう
# PULSE_SERVER を明示する。
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export PULSE_SERVER="${PULSE_SERVER:-unix:${XDG_RUNTIME_DIR}/pulse/native}"

SESSION="aizunda"

LLAMA_BIN="/home/$USER/llama.cpp/build/bin/llama-server"
QWEN_MODEL="/home/$USER/AIzunda/qwen3.6/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"
LLAMA_HOST="127.0.0.1"
LLAMA_PORT="8080"
LLAMA_CTX="8192"
LLAMA_NGL="99"

VOICEVOX_CONTAINER="voicevox_engine"
VOICEVOX_IMAGE="voicevox/voicevox_engine:cpu-ubuntu20.04-latest"

TTLLM_DIR="/home/$USER/AIzunda/ttllm"
THREE_VRM_DIR="/home/$USER/AIzunda/three-vrm"
VTT_DIR="/home/$USER/AIzunda/vtt"

BROWSER_URL="http://localhost:8000/zundamon.html"

# gfx1151 (Ryzen AI Max+ 395) 向け ROCm env。
export HSA_OVERRIDE_GFX_VERSION="${HSA_OVERRIDE_GFX_VERSION:-11.5.1}"
export ROCM_PATH="${ROCM_PATH:-/opt/rocm}"
export HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"
export AMDGPU_TARGETS="${AMDGPU_TARGETS:-gfx1151}"
export LD_LIBRARY_PATH="/usr/local/lib:/opt/rocm/lib:/opt/rocm/lib/llvm/lib:${LD_LIBRARY_PATH:-}"

# ---- helpers ------------------------------------------------------------

log()  { printf '\033[1;34m[launch]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[launch]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[launch]\033[0m %s\n' "$*" >&2; exit 1; }

# wait_http <name> <url> <timeout_sec>
wait_http() {
    local name="$1" url="$2" timeout="${3:-120}"
    local start now
    start=$(date +%s)
    log "waiting for ${name} (${url}) ..."
    while true; do
        if curl -sf -o /dev/null -m 2 "$url"; then
            log "  ${name} is up"
            return 0
        fi
        now=$(date +%s)
        if (( now - start > timeout )); then
            die "${name} did not come up within ${timeout}s"
        fi
        sleep 2
    done
}

# new_window <name> <command>
new_window() {
    local name="$1" cmd="$2"
    tmux new-window -t "$SESSION" -n "$name"
    tmux send-keys -t "${SESSION}:${name}" "$cmd" C-m
}

# ---- preflight ----------------------------------------------------------

command -v tmux         >/dev/null || die "tmux がありません"
command -v docker       >/dev/null || die "docker がありません"
command -v curl         >/dev/null || die "curl がありません"
command -v google-chrome >/dev/null || warn "google-chrome が見つかりません (Chrome 起動はスキップします)"

[[ -x "$LLAMA_BIN"           ]] || die "llama-server が見つかりません: $LLAMA_BIN"
[[ -f "$QWEN_MODEL"          ]] || die "Qwen モデルが見つかりません: $QWEN_MODEL"
[[ -x "$TTLLM_DIR/run.sh"    ]] || die "ttllm/run.sh がありません"
[[ -d "$THREE_VRM_DIR"       ]] || die "three-vrm ディレクトリがありません"
[[ -x "$VTT_DIR/run.sh"      ]] || die "vtt/run.sh がありません"

# 既存セッションは作り直す。
if tmux has-session -t "$SESSION" 2>/dev/null; then
    log "既存の tmux セッション ${SESSION} を終了します"
    tmux kill-session -t "$SESSION"
fi

# ---- 1. VOICEVOX (docker) ----------------------------------------------

log "VOICEVOX コンテナ (${VOICEVOX_CONTAINER}) を起動します"
if docker ps --format '{{.Names}}' | grep -qx "$VOICEVOX_CONTAINER"; then
    log "  すでに running"
elif docker ps -a --format '{{.Names}}' | grep -qx "$VOICEVOX_CONTAINER"; then
    docker start "$VOICEVOX_CONTAINER" >/dev/null
else
    log "  コンテナが無いので新規作成します"
    docker run -d \
        --name "$VOICEVOX_CONTAINER" \
        --restart unless-stopped \
        -p 50021:50021 \
        "$VOICEVOX_IMAGE" >/dev/null
fi

# ---- tmux セッションを作って voicevox ログを 1 枚目に --------------------

tmux new-session -d -s "$SESSION" -n voicevox \
    "docker logs -f --tail 50 ${VOICEVOX_CONTAINER}"

wait_http "VOICEVOX" "http://localhost:50021/version" 60

# ---- 2. llama-server ----------------------------------------------------

LLAMA_CMD="HSA_OVERRIDE_GFX_VERSION=${HSA_OVERRIDE_GFX_VERSION} \
ROCM_PATH=${ROCM_PATH} \
HIP_VISIBLE_DEVICES=${HIP_VISIBLE_DEVICES} \
LD_LIBRARY_PATH=${LD_LIBRARY_PATH} \
${LLAMA_BIN} -m ${QWEN_MODEL} --host ${LLAMA_HOST} --port ${LLAMA_PORT} -ngl ${LLAMA_NGL} -c ${LLAMA_CTX} -fit off"

new_window "llama" "$LLAMA_CMD"

# モデルロードに数十秒〜数分かかるのでタイムアウト長め。
wait_http "llama-server" "http://${LLAMA_HOST}:${LLAMA_PORT}/health" 600

# ---- 3. ttllm -----------------------------------------------------------

new_window "ttllm" "cd ${TTLLM_DIR} && ./run.sh"

wait_http "ttllm" "http://localhost:8001/health" 60

# ---- 4. WhisperX warmup -------------------------------------------------

log "WhisperX を warmup (初回ロードを先に済ませる) ..."
if curl -sf -X POST -m 300 http://localhost:8001/warmup -o /dev/null; then
    log "  warmup 完了"
else
    warn "  warmup に失敗 (後続の初回転写が遅くなる可能性)"
fi

# ---- 5. three-vrm -------------------------------------------------------

new_window "three-vrm" "cd ${THREE_VRM_DIR} && python3 server.py"

wait_http "three-vrm" "http://localhost:8000/status" 30

# ---- 6. Chrome ----------------------------------------------------------

if command -v google-chrome >/dev/null; then
    log "Chrome で ${BROWSER_URL} を開きます"
    google-chrome --new-window "$BROWSER_URL" >/dev/null 2>&1 &
    disown
else
    warn "Chrome 起動はスキップ。手動で ${BROWSER_URL} を開いてください"
fi

# ---- 7. vtt (PTT) -------------------------------------------------------

# VRM 画面の 🎤 ボタンがメインの PTT 動線だが、
# CLI 側の PTT も別ウィンドウで待機させておく。
# Enter で録音開始 / 停止。要らなければこのウィンドウは閉じて良い。
new_window "vtt" "cd ${VTT_DIR} && ./run.sh --device USB --no-warmup"

# ---- done ---------------------------------------------------------------

cat <<EOF

=========================================================================
 AIzunda パイプラインが起動しました。

   VOICEVOX    : http://localhost:50021/docs
   llama-server: http://localhost:${LLAMA_PORT}/health
   ttllm       : http://localhost:8001/docs
   three-vrm   : ${BROWSER_URL}   ← Chrome で自動オープン
                 右下の 🎤 ボタンで PTT (長押し or クリック)

 tmux:
   tmux attach -t ${SESSION}     (ログを見る)
   tmux kill-session -t ${SESSION} (全部止める)
   docker stop ${VOICEVOX_CONTAINER}  (VOICEVOX は docker 側を止める)
=========================================================================
EOF
