# AIずんだもん - VOICEVOXセットアップ

## 概要

AIずんだもんパイプラインのTTS（音声合成）コンポーネント。
VOICEVOXをDockerで動かし、HTTPのAPIを使って他コンポーネントから音声合成を呼び出す。

## 環境

- Docker: 29.4.0
- イメージ: `voicevox/voicevox_engine:cpu-ubuntu20.04-latest`
- APIポート: `50021`
- 推論モード: CPU（ROCm環境との干渉を避けるため）

## 起動方法

### 初回セットアップ

```bash
docker pull voicevox/voicevox_engine:cpu-ubuntu20.04-latest

docker run -d \
  --name voicevox_engine \
  --restart unless-stopped \
  -p 50021:50021 \
  voicevox/voicevox_engine:cpu-ubuntu20.04-latest
```

### 起動確認

```bash
curl http://localhost:50021/version
```

### 停止 / 再起動

```bash
docker stop voicevox_engine
docker start voicevox_engine
```

## ずんだもんのスピーカーID

| スタイル | ID |
|----------|----|
| ノーマル | 3  |
| あまあま | 1  |
| ツンツン | 7  |
| セクシー | 5  |
| ささやき | 22 |
| ヒソヒソ | 38 |
| ヘロヘロ | 75 |
| なみだめ | 76 |

## API使用方法

音声合成は2ステップ。

### 1. audio_query（音声合成用クエリ生成）

```bash
curl -X POST "http://localhost:50021/audio_query" \
  --get \
  --data-urlencode "text=こんにちは、ずんだもんなのだ！" \
  --data-urlencode "speaker=3" \
  -o query.json
```

### 2. synthesis（WAV生成）

```bash
curl -X POST "http://localhost:50021/synthesis?speaker=3" \
  -H "Content-Type: application/json" \
  -d @query.json \
  -o output.wav
```

### Pythonでの呼び出し例

```python
import requests

def synthesize(text: str, speaker: int = 3, output_path: str = "output.wav"):
    base_url = "http://localhost:50021"

    query = requests.post(
        f"{base_url}/audio_query",
        params={"text": text, "speaker": speaker}
    ).json()

    wav = requests.post(
        f"{base_url}/synthesis",
        params={"speaker": speaker},
        json=query
    )

    with open(output_path, "wb") as f:
        f.write(wav.content)

synthesize("こんにちは、ずんだもんなのだ！")
```

## テストスクリプト

```bash
# デフォルト（ノーマル、日本語テスト文）
./test_voicevox.sh

# テキストとスピーカーを指定
./test_voicevox.sh "よろしくなのだ" 3 /tmp/test.wav
```

## パイプライン内での位置づけ

```
マイク入力
   ↓
WhisperX (STT) - ~/AIzunda/whisperX-rocm
   ↓ テキスト
llama-server (LLM) - Qwen3.5-35B, localhost:8080
   ↓ 応答テキスト
VOICEVOX Engine (TTS) ← ここ - localhost:50021
   ↓ WAV音声
TalkingHead (VRM) - ブラウザ、リップシンク表示
```

## 出力仕様

- フォーマット: RIFF WAV
- サンプルレート: 24,000 Hz
- ビット深度: 16bit
- チャンネル: モノラル
