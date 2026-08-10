# Models

> **The MIT license of this repository covers Dabuj's source code only.**
> AI models are downloaded separately from their upstream sources and remain
> subject to their own licenses, some of which are more restrictive than MIT.

Dabuj bundles **no model weights**. Nothing is downloaded until you explicitly
ask for it, and you are always shown the exact size, source and license first.

---

## Installed models

Models live in your application data directory, not in this repository:

| Platform | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\Dabuj\models` |
| macOS | `~/Library/Application Support/Dabuj/models` |
| Linux | `~/.local/share/Dabuj/models` |

Manage them with `dabuj models list`, `dabuj models install <id>` and
`dabuj models remove <id>`, or from the **Models** page in the web UI.

---

## Speech recognition — ✅ implemented

All ASR models currently in the catalog are Whisper-family models converted to
the CTranslate2 format, run by the `faster-whisper` runtime.

| Model ID | Task | Provider | Source | License | Commercial use | Redistribution |
|---|---|---|---|---|---|---|
| `whisper-tiny` | ASR | faster-whisper | [Systran/faster-whisper-tiny](https://huggingface.co/Systran/faster-whisper-tiny) | MIT | ✅ Yes | ❌ Not by Dabuj |
| `whisper-base` | ASR | faster-whisper | [Systran/faster-whisper-base](https://huggingface.co/Systran/faster-whisper-base) | MIT | ✅ Yes | ❌ Not by Dabuj |
| `whisper-small` | ASR | faster-whisper | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) | MIT | ✅ Yes | ❌ Not by Dabuj |
| `whisper-medium` | ASR | faster-whisper | [Systran/faster-whisper-medium](https://huggingface.co/Systran/faster-whisper-medium) | MIT | ✅ Yes | ❌ Not by Dabuj |
| `whisper-large-v3` | ASR | faster-whisper | [Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) | MIT | ✅ Yes | ❌ Not by Dabuj |

**Licensing basis, verified 2026-08-10:**

- The Whisper weights are released by OpenAI under the
  [MIT license](https://github.com/openai/whisper/blob/main/LICENSE).
- The `faster-whisper` runtime is
  [MIT licensed](https://github.com/SYSTRAN/faster-whisper).
- The SYSTRAN repositories above are CTranslate2 conversions of those MIT
  weights and carry the same terms.

"Redistribution: not by Dabuj" means Dabuj does not ship these weights. The MIT
license would permit it; the project chooses not to so that users always get
models from the authoritative upstream source.

### Sizes and requirements

Sizes are approximate (float16 conversions). The exact figure is fetched from
the source and shown before you confirm a download.

| Model | Download | Min RAM | Min VRAM | Notes |
|---|---|---|---|---|
| `whisper-tiny` | ~75 MB | 1 GB | 1 GB | Drafts and pipeline checks |
| `whisper-base` | ~145 MB | 2 GB | 1 GB | Default for the **Low** profile |
| `whisper-small` | ~484 MB | 2 GB | 2 GB | Default for **Balanced**; good on CPU with int8 |
| `whisper-medium` | ~1.5 GB | 6 GB | 5 GB | Wants a GPU |
| `whisper-large-v3` | ~3.1 GB | 8 GB | 8 GB | Default for **High**/**Ultra**; best Czech and German |

### Languages

The Whisper family was trained on 99 languages, including all three of Dabuj's
primary targets — **English**, **German** and **Czech** — plus Slovak, Polish,
Spanish, French, Italian, Portuguese, Dutch, Ukrainian and many more. The
authoritative list is `WHISPER_LANGUAGES` in
[`catalog.py`](../backend/src/dabuj/models/catalog.py), taken from Whisper's own
tokenizer.

Recognition quality varies substantially by language. Whisper is strongest on
English; Czech and German are well supported but benefit noticeably from the
larger models.

---

## Planned models

These are **not implemented**. They are recorded here because the licensing
research that shaped the roadmap is worth keeping, and because two of the
obvious candidates are traps.

### Speaker diarization — 🗺 planned (v0.3)

| Candidate | License | Commercial use | Notes |
|---|---|---|---|
| [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) | MIT | ✅ Yes | Gated on Hugging Face: you must accept the terms and supply an access token before download. Dabuj will make that an explicit, user-driven step. |

### Translation — 🗺 planned (v0.4)

| Candidate | License | Commercial use | Verdict |
|---|---|---|---|
| [Helsinki-NLP OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT) | CC-BY-4.0 / Apache-2.0 | ✅ Yes | **Preferred.** Small bilingual models, permissive terms, good en↔de and en↔cs coverage. |
| [NLLB-200](https://huggingface.co/facebook/nllb-200-distilled-600M) | **CC-BY-NC-4.0** | ❌ **No** | **Rejected as a default.** The non-commercial clause would silently make Dabuj's output unusable commercially. It will not be offered without a prominent warning. |

> This is exactly the trap the licensing rules exist to catch: NLLB-200 is a
> popular, high-quality, easy-to-use multilingual model, and shipping it as the
> default would quietly poison every commercial user's output.

### Text to speech — 🗺 planned (v0.5)

| Candidate | Code license | Voice licenses | Notes |
|---|---|---|---|
| [Piper](https://github.com/OHF-Voice/piper1-gpl) | **GPL-3.0** | Per voice (commonly MIT or CC-BY) | Has a Czech voice (`cs_CZ-jirka`). The maintained fork relicensed from MIT to GPL-3.0 in 2025. Dabuj intends to run Piper's ONNX voices through `onnxruntime` directly rather than linking GPL code into an MIT codebase. Each voice's license must be recorded individually. |
| [Coqui XTTS-v2](https://huggingface.co/coqui/XTTS-v2) | CPML | ❌ Non-commercial | Excellent quality and voice cloning, but the Coqui Public Model License forbids commercial use. Same trap as NLLB. |

### Source separation — 🗺 planned (v0.8)

Not yet researched in depth. Whatever is chosen must be optional: basic dubbing
has to work without a large separation model installed.

---

## Rules this catalog follows

1. **Licenses are read, not assumed.** A model is marked commercially usable
   only when its license text plainly says so.
2. **Popularity is not a criterion.** NLLB-200 and XTTS-v2 are both excellent
   and both rejected as defaults on licensing grounds.
3. **No capability is invented.** Language lists come from the model's own
   tokenizer or model card, never from a guess.
4. **No benchmark numbers are quoted** unless Dabuj measured them on the machine
   in question (`dabuj benchmark`, planned for v0.9).
5. **Nothing is bundled.** Every model is fetched from its upstream source, over
   HTTPS, verified against the publisher's checksum where one exists.

If you find an error in this table, please open an issue — a wrong license entry
is a bug of the most serious kind in this project.
