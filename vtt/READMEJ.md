# vtt — マイク → WhisperX 文字起こし

このPCに接続された USB マイク（または任意の入力デバイス）から音声を録って、
`ttllm` ブリッジ経由で WhisperX に投げて文字起こしする最小構成の CLI です。
AIzunda パイプライン（`vtt → ttllm → llama-server → voicevox → talkinghead/zundavrm`）
の先頭、音声入力の段を担当します。

2026-04-20 時点で、USB マイク入力 → WhisperX-ROCm 転写までエンドツーエンドで動作確認済みです。

## 構成

```
vtt/
├── vtt.py       # CLI 本体
├── install.sh   # ローカル .venv を作って依存をインストール
├── run.sh       # 実行ラッパ
└── READMEJ.md   # このファイル
```

`ttllm` の `/transcribe` に WAV を POST する薄いクライアントです。
WhisperX / torch-ROCm / ctranslate2-rocm は ttllm 側（`~/AIzunda/whisperx-rocm`）が
抱えているので、vtt 本体では `numpy` / `sounddevice` / `soundfile` / `httpx` だけ入ります。

## 動作確認済みの構成

| 項目 | 値 |
| ---- | --- |
| OS | Ubuntu 24.04.4 LTS (PipeWire) |
| 入力デバイス | USB Composite Device (YunChen, card 1, 48kHz mono) |
| ttllm 先 | `http://localhost:8001`（`~/AIzunda/ttllm/run.sh`） |
| WhisperX venv | `~/whisperx-rocm/.venv`（torch 2.9.1+rocm7.2.0 / ctranslate2 4.6.2 / faster-whisper 1.2.1） |
| モデル | `large-v3`（ttllm 側で環境変数指定） |

起こったハマりポイント：

- PortAudio 経由で直接 ALSA の USB デバイスを掴むと 16kHz を拒否されるので、
  vtt は自動で 48kHz にフォールバックし、リサンプルは WhisperX（ffmpeg）に任せます。

## 前提

- `~/AIzunda/ttllm` が起動中で、`http://localhost:8001` に応答すること
  ```bash
  cd ~/AIzunda/ttllm && ./run.sh
  ```
- `~/AIZunda/whisperx-rocm` に whisperx が入っており、ttllm の `WHISPERX_VENV`
  が`~/AIZunda/whisperx-rocm/.venv`を指していること（ttllm の `READMEJ.md` 参照）
- `libportaudio2` が入っていること
  ```bash
  sudo apt-get install -y libportaudio2
  ```

## セットアップ

```bash
cd ~/AIzunda/vtt
./install.sh
```

## 使い方

### デバイス確認

まずマイクが見えているか確認します。

```bash
./run.sh --list-devices
```

手元の環境では以下のように出ます。`--device USB` で部分一致指定できます（番号指定も可）。

```
[4] USB Composite Device: Audio (hw:1,0)  in=1 sr=48000
[5] HD-Audio Generic: SN6186 Analog (hw:2,0)  in=2 sr=48000
[7] pipewire  in=64 sr=44100
[8] pulse  in=32 sr=44100
[9] default  in=64 sr=44100
```

### プッシュ・トゥ・トーク（既定）

Enter で録音開始、もう一度 Enter で停止して転写。

```bash
./run.sh --device USB
```

動作例:

```
warming up WhisperX via ttllm...
Press Enter to START recording...
Recording. Press Enter to STOP.

テストテスト。聞こえますか?テストテスト。
```

### 固定秒数の録音

```bash
./run.sh --device USB --duration 5
```

### VAD で連続転写

無音で区切りながら喋り続けられるモード。Ctrl+C で終了。
ROCm の既知問題（60s 超でメモリフォールト）を避けるため、1 発話は 55s で強制カットします。

```bash
./run.sh --device USB --vad
```

動作例:

```
VAD listening (threshold=0.012, silence=0.8s). Ctrl+C to stop.
テストテスト聞こえますか?
これはコンティニューテストです
聞こえますか
^C
```

騒がしい環境で誤検知が多ければ `--vad-threshold` を上げてください
（既定 0.012、おすすめ範囲 0.02 ～ 0.05）。

### 出力オプション

| オプション         | 説明 |
| ------------------ | ---- |
| `--output FILE`    | 転写結果を FILE に追記 |
| `--json`           | `{"ts": ..., "transcript": ...}` を 1 行 JSON で出力 |
| `--keep DIR`       | 録音した WAV を DIR に残す（デバッグ用） |
| `--no-warmup`      | `/warmup` POST をスキップ |

例: VAD で連続転写して JSON ログとして残す。

```bash
./run.sh --device USB --vad --json --output ./transcripts.jsonl --keep ./captures
```

## 環境変数

| 変数                      | 既定値                    | 説明 |
| ------------------------- | ------------------------- | ---- |
| `VTT_SERVER`              | `http://localhost:8001`   | ttllm ブリッジの URL |
| `VTT_SAMPLE_RATE`         | `16000`                   | キャプチャのサンプリングレート |
| `VTT_CHANNELS`            | `1`                       | 入力チャンネル数 |
| `VTT_DEVICE`              | （なし）                  | デバイス番号 or 名前の部分一致 |
| `VTT_VAD_THRESHOLD`       | `0.012`                   | VAD の RMS しきい値 |
| `VTT_VAD_SILENCE_SEC`     | `0.8`                     | 発話終端とみなす無音の長さ |
| `VTT_VAD_MIN_SPEECH_SEC`  | `0.3`                     | これ未満の短い音は捨てる |
| `VTT_VAD_MAX_SEC`         | `55`                      | 発話の最大長（ROCm 回避で <60s） |

## 仕組み

1. `sounddevice` で PortAudio（PipeWire バックエンド）から `float32` / モノラル /
   16 kHz で取り込みます。デバイスが 16 kHz を拒否した場合は、そのデバイスの
   デフォルトレート（USB マイクなら 48 kHz）にフォールバックします。リサンプル
   は WhisperX（ffmpeg 経由）に任せます。
2. PCM16 WAV に変換し、`POST {VTT_SERVER}/transcribe` でマルチパート送信。
3. ttllm 側の `{"transcript": "..."}` を stdout に吐きます。

## 次の段につなぐとき

`/transcribe` ではなく ttllm の `/voice_chat` を叩けば、転写から llama.cpp 応答まで
ワンショットで返ります。ブラウザ（`talkinghead` / `zundavrm`）から直接叩く場合は
`ttllm/READMEJ.md` の JavaScript サンプルを参照してください。vtt 側から LLM まで
通したいなら、`post_transcribe` を `/voice_chat` 呼び出しに差し替えて `reply`
フィールドも出すだけです。

## 既知の注意点

- `--vad` で大音量のファンや空調が流れていると常時「発話中」になります。
  `--vad-threshold` を 0.02 ～ 0.05 あたりまで上げて調整してください。
- 1 発話を 55s で切るため、長い読み上げは自動で分割転写されます。連結は呼び出し側で。
- ttllm が起動していない状態で実行すると `/transcribe` で SystemExit します。
  `cd ~/AIzunda/ttllm && ./run.sh` を別ターミナルで先に立ち上げてください。
- 初回の `/transcribe` は WhisperX モデルのロードで数十秒かかります。以降は
  ttllm プロセスが生きている限りウォーム状態なので再ロードは不要です。
- MacBook から RDP ログイン中に Mac 側のマイクを使いたいケースは `--device` で
  RDP 仮想入力を指定してください。`./run.sh --list-devices` で名前が見えます。
