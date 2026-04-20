#!/bin/bash
# VOICEVOX動作確認スクリプト
# speaker=3: ずんだもん（ノーマル）
TEXT="${1:-こんにちは、ずんだもんなのだ！}"
SPEAKER="${2:-3}"
OUTPUT="${3:-/tmp/zundamon_test.wav}"

curl -s -X POST "http://localhost:50021/audio_query" \
  --get --data-urlencode "text=${TEXT}" --data-urlencode "speaker=${SPEAKER}" \
  -o /tmp/vv_query.json

curl -s -X POST "http://localhost:50021/synthesis?speaker=${SPEAKER}" \
  -H "Content-Type: application/json" \
  -d @/tmp/vv_query.json \
  -o "${OUTPUT}"

echo "Generated: ${OUTPUT} ($(wc -c < ${OUTPUT}) bytes)"
file "${OUTPUT}"
