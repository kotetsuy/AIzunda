# ずんだもん three-vrm サーバー

VOICEVOXの音声合成結果をWebSocketでブラウザに送り、`@pixiv/three-vrm` でVRM1.0モデル（ずんだもん）をリップシンク表示するスタンドアロンサーバー。

## ディレクトリ構成

```
~/AIzunda/three-vrm/
├── server.py                      # aiohttp サーバー（port 8000）
├── READMEJ.md
└── TalkingHead/
    ├── zundamon.html              # ビューア本体
    └── libs/
        ├── three/
        │   ├── three.module.js    # r180 wrapper
        │   ├── three.core.js      # r180 実装本体（必須）
        │   └── addons/
        │       ├── loaders/GLTFLoader.js
        │       └── utils/BufferGeometryUtils.js
        └── three-vrm/
            └── three-vrm.module.min.js
```

## 前提

- **VOICEVOX エンジン**が `localhost:50021` で稼働していること（Docker推奨）
  ```
  docker start $(docker ps -aq --filter ancestor=voicevox/voicevox_engine:cpu-ubuntu20.04-latest)
  ```
- **ttllm ブリッジ**（WhisperX + llama.cpp）が `localhost:8001` で稼働していること
  （マイク入力機能を使う場合のみ必須。`~/AIzunda/ttllm/run.sh`）
- **llama-server** が `localhost:8080` で稼働していること（ttllm の依存）
- **ずんだもんVRM** が `/home/araki/AIzunda/zundavrm/VRM/Zundamon_2025_VRM10A.vrm` に配置されていること
  （変更したい場合は `server.py` の `VRM_DIR` を書き換える）

## パイプライン全体像

```
マイク (ブラウザ zundamon.html)
    ↓ MediaRecorder (webm/opus)
three-vrm /voice_chat_speak           (port 8000)
    ↓ multipart POST audio
ttllm /voice_chat                     (port 8001)
    ↓ WhisperX STT → llama.cpp LLM
ttllm returns {transcript, reply}
    ↓ three-vrm が reply を受け取る
VOICEVOX /audio_query + /synthesis    (port 50021)
    ↓ WAV + accent_phrases
three-vrm: moras → visemes 変換
    ↓ WS broadcast
ブラウザ: AudioContext 再生 + three-vrm で口パク
```

## 起動

```bash
cd ~/AIzunda/three-vrm
python3 server.py
```

ブラウザで `http://localhost:8000/zundamon.html` を開く。
初回は **画面を一度クリック** して AudioContext を有効化する必要がある（ブラウザの user-gesture 要件）。

## 音声 + リップシンクの発火

```bash
curl -X POST http://localhost:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"こんにちはなのだ","speaker_id":3}'
```

- `text` : 読み上げテキスト
- `speaker_id` : ずんだもんのスタイル
  - 3: ノーマル
  - 1: あまあま
  - 7: ツンツン
  - 22: ささやき

レスポンス:
```json
{"ok": true, "visemes": 40, "clients": 1}
```

## 内部動作

1. `POST /speak` → VOICEVOX `audio_query` → `synthesis` でWAV生成
2. `accent_phrases` の `moras` から `visemes / vtimes / vdurations` を算出
   - 母音: a→aa, i→I, u→U, e→E, o→O, N→nn
   - 子音: p/b/m→PP, s/z→SS, t/d→DD, k/g→kk など
   - 時間単位: **ミリ秒**
3. WebSocket で接続中の全クライアントにJSONブロードキャスト
4. ブラウザ側で Base64 → WAV decode → `AudioContext` 再生
5. `audioCtx.currentTime` ベースのスケジュールで `vrm.expressionManager.setValue(expr, 1.0)` を発火

VRM1.0 標準表情 `aa / ih / ou / ee / oh / nn` のみ動きます。子音は一時的に口を閉じる挙動。

## エンドポイント

| メソッド | パス | 用途 |
|---|---|---|
| GET  | `/zundamon.html` | ビューア（マイクボタン内蔵） |
| GET  | `/ws` | WebSocket 接続 |
| POST | `/speak` | 音声生成＋リップシンクブロードキャスト |
| POST | `/voice_chat_speak` | 音声 → ttllm → VOICEVOX → WS 配信（ワンショット） |
| GET  | `/vrm/{filename}` | VRMファイル配信 |
| GET  | `/status` | クライアント数確認 |

### `/voice_chat_speak` (multipart/form-data)

| フィールド     | 型              | 既定値 | 説明 |
| -------------- | --------------- | ------ | ---- |
| `audio`        | file            | —      | webm / wav / mp3 / m4a 等 |
| `speaker_id`   | int             | `3`    | VOICEVOX スピーカーID |
| `system`       | str             | ttllm 既定 | LLM system prompt 上書き |
| `history`      | str (JSON list) | `[]`   | 会話履歴 |
| `temperature`  | float           | `0.7`  | LLM |
| `max_tokens`   | int             | `512`  | LLM |

レスポンス:
```json
{"ok": true, "transcript": "...", "reply": "...", "visemes": 42, "clients": 1}
```

ブラウザ側の使用例（既にビューアに組み込み済み）:
```javascript
const fd = new FormData();
fd.append("audio", blob, "utterance.webm");
fd.append("speaker_id", "3");
await fetch("/voice_chat_speak", { method: "POST", body: fd });
// 応答音声は WS 経由で自動再生＋リップシンク
```

### ブラウザ内蔵マイク

右下の 🎤 ボタン:
- **長押し（250ms 以上）**: 押している間だけ録音（離すと送信）
- **短クリック**: 録音開始 → もう一度クリックで送信
- ユーザー発話は薄青の字幕、ずんだもんの返答は白字の字幕として表示

初回は画面を一度クリックして AudioContext とマイク権限を有効化してください。

## 再構築時の落とし穴

- **three.js r170 以降は `three.module.js` + `three.core.js` の2ファイル構成**。両方配置必須。  
  `three.core.js` が欠けると Chrome は `Failed to fetch dynamically imported module` という紛らわしいエラーを出す（実際は依存解決失敗）。
  取得元: `https://unpkg.com/three@0.180.0/build/three.core.js`
- `GLTFLoader.js` と `three-vrm.module.min.js` はベアスペシファイア `"three"` を import する。`zundamon.html` 内の `<script type="importmap">` で解決している。
- サーバーが送る vtimes / vdurations は**ミリ秒**。ブラウザ側は `audioCtx.currentTime`（秒）と比較するため `/1000` 変換が必須。

## 既知のワーニング（動作に影響なし）

```
VRMUtils.removeUnnecessaryJoints is deprecated. Use combineSkeletons instead.
```
次のメジャーバージョンで除去予定。

## 次のステップ

マイク入力 → WhisperX(STT) → llama-server Qwen3-35B-A3B → この `/speak` を叩く、というパイプライン連携スクリプトで完全な AI ずんだもんになる。
