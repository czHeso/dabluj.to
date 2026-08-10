# Troubleshooting

Start here:

```bash
dabuj doctor
```

It checks the runtime, configuration, storage permissions, FFmpeg, the ASR
runtime, acceleration and installed models, and reports every problem in one
run rather than one at a time.

---

## FFmpeg could not be found

Dabuj does not bundle FFmpeg.

```bash
# Windows
winget install Gyan.FFmpeg

# macOS
brew install ffmpeg

# Debian / Ubuntu
sudo apt install ffmpeg
```

**On Windows, open a new terminal afterwards** — an existing one has the old
`PATH` and will still report FFmpeg as missing.

If it is installed somewhere unusual, point Dabuj at it in `dabuj.toml`:

```toml
ffmpeg_path = "C:/tools/ffmpeg/bin/ffmpeg.exe"
ffprobe_path = "C:/tools/ffmpeg/bin/ffprobe.exe"
```

## Speech recognition support is not installed

```bash
pip install "dabuj[asr]"
```

If that fails on **Python 3.13**, that is the cause: CTranslate2 publishes no
3.13 wheels. Use Python 3.10–3.12. See [ADR 0009](adr/0009-python-version.md).

## Certificate verify failed when downloading a model

You are almost certainly behind a TLS-inspecting proxy (common on corporate
networks) or have antivirus HTTPS scanning enabled.

Dabuj verifies against your **operating system trust store**, which is where
such a proxy's root certificate is installed — so this usually just works. If it
still fails, your organisation's root certificate is missing from the OS store;
ask your IT department to install it.

Dabuj will not disable certificate verification, and there is no setting to.

## The CUDA backend is not available

`dabuj system-info` shows what was detected.

GPU inference needs **CUDA 12 and cuDNN 9**. An NVIDIA driver alone is not
enough — the runtime libraries must be present too.

Dabuj deliberately will **not** silently fall back to CPU when you ask for
`--device cuda`: turning twenty minutes into six hours without telling you is
worse than an error. Run on the CPU explicitly:

```bash
dabuj transcribe video.mkv --device cpu --precision int8
```

## Not enough memory to load the model

Try, in order:

```bash
dabuj transcribe video.mkv --precision int8        # roughly halves memory
dabuj transcribe video.mkv --model whisper-small   # a smaller model
dabuj transcribe video.mkv --device cpu            # system RAM instead of VRAM
```

Close other applications — especially browsers and games — before retrying.

## Transcription is very slow

Expected on CPU. Rough guidance, not a benchmark:

| Setup | Order of magnitude |
|---|---|
| Small model, int8, CPU | Slower than real time on a laptop |
| Small model, GPU | Several times faster than real time |
| Large model, CPU | Impractical for long recordings |
| Large model, GPU | Faster than real time |

Speed up by choosing a smaller model, using `--precision int8`, using
`--beam-size 1` (greedy decoding), or using a GPU.

`dabuj system-info` recommends a profile that fits your machine.

## The transcript is empty, or full of nonsense

- **Empty**: the audio may be silent or music-only, or voice activity detection
  removed everything. Try `--no-vad`.
- **Repeated or invented phrases**: Whisper hallucinates on silence and music.
  Keep VAD on (the default), and use a larger model.
- **Wrong language**: check the detected language and confidence in the output.
  Below 60% confidence Dabuj warns you. Set it explicitly: `--language cs`.
- **Poor accuracy generally**: use a larger model. Czech and German improve
  markedly from `whisper-small` to `whisper-large-v3`.

## A website tried to talk to your local Dabuj instance

Dabuj blocked a cross-origin request — working as designed.

If you were not doing anything unusual, close the page that caused it. If you
are developing against the API, use the Vite dev server on `:5173`, which is
already allow-listed. See [ADR 0008](adr/0008-localhost-security.md).

## The port is already in use

Dabuj automatically tries the next 20 ports. To pick one:

```bash
dabuj start --port 8123
```

## A project will not open

- *"created by a newer version of Dabuj"* — update Dabuj.
- *"corrupt"* — `project.json` is not valid JSON. Saves are atomic, so this
  normally means external damage. The `source/` folder still holds your media.

## Reclaiming disk space

```bash
dabuj models list        # see what is installed and how large
dabuj models remove whisper-large-v3
```

Project caches hold extracted audio and intermediate artefacts. Deleting them
is safe — the pipeline regenerates what it needs. Your source media is never
removed without an explicit action.

## Getting more detail

```bash
dabuj --debug transcribe video.mkv
```

`--debug` prints tracebacks to the console and writes verbose logs to
`<data-dir>/logs/dabuj.jsonl`. Find the data directory with `dabuj system-info`.

Logs never contain transcript text. See [PRIVACY.md](PRIVACY.md).
