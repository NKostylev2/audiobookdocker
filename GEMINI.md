# AI-Dubbing Project Guidelines & Pipeline Reference

## Project Purpose
Automated generation, verification, and mastering of high-quality Russian audiobooks (CosyVoice 3, 24 kHz) with zero swallowed endings and automatic delivery to Windows.

---

## 1. Environment & Hardware Constraints
- **GPU:** NVIDIA GeForce RTX 3070 (8 GB VRAM).
- **VRAM Ceiling:** CosyVoice 3 uses ~6.0–6.8 GB VRAM. To avoid exceeding the 8 GB ceiling with desktop compositor/browser running, Whisper verification MUST run on CPU (`whisper.load_model('base', device='cpu')`, taking only ~1.5s per chunk and 0 VRAM).
  - NEVER run a second concurrent Python process on CUDA (will cause CUDA Out-of-Memory).
- **Python Environment:** `/home/nk/.venvs/cosyvoice-env/bin/python`.
- **Mounts & Paths:**
  - Workspace: `/mnt/ai/AI-Dubbing` (native Linux `ext4`, mounted via fstab at `/mnt/ai`).
  - Windows C: `/dev/nvme0n1p2` mounted to `/run/media/nk/System` (`C:\Users\kosty\Desktop`).
  - Windows Main: `/dev/nvme0n1p5` mounted to `/run/media/nk/Main` (`M:\Downloads`).

---

## 2. Text Segmentation & Ending Protection Rules
- **Hard-wrapped Lines:** Chapter source texts often have `\n` within paragraphs. Paragraphs MUST be split by `\n\s*\n+` and internal newlines replaced with spaces (`re.sub(r'\s+', ' ', p)`).
- **Token Limits:**
  - Optimal block size for CosyVoice 3 zero-shot is **30–60 tokens** (max ~65 tokens).
  - Packing >70–90 tokens causes CosyVoice to prematurely emit EOS tokens and swallow the final sentences/words.
- **Punctuation Sanitization:**
  - Trailing non-terminal characters (`;`, `,`, `:`, `—`, numbers) at the end of blocks must be normalized to `.` to prevent acoustic glitching or abrupt cutoffs.
- **Full Word Count Preservation:**
  - Before synthesis, `[a-zA-Zа-яА-ЯёЁ0-9]+` tokens from the source text and joined segments must be compared and confirmed to match 100% in exact order.

---

## 3. Automated Synthesis & Ending Verification
- **Voice Profile:** `VID_20261101_122350_289.wav` (with corresponding `.reference.txt`).
- **Prompt:** `You are a helpful assistant.<|endofprompt|>{ref_text}`.
- **In-flight Whisper Verification:**
  - After synthesizing each candidate chunk, transcribe with Whisper (`whisper.load_model('base')`).
  - Check whether the expected ending words (last 2–3 words or their stems) are present in the transcription.
  - If swallowed: retry up to 3 times with seed offsets (e.g. +17, +42, +83).
- **Split-Fallback on Persistent Swallows:**
  - If a segment still swallows its ending after seed retries, split into two natural sentence halves (`razdel.sentenize`), synthesize each half independently with Whisper verification, and join with 200 ms silence.

---

## 4. Master Assembly & Delivery
- **Inter-Sentence Silence:** 200 ms digital zero silence (`torch.zeros((1, sr * 0.2))`).
- **Inter-Paragraph Silence:** 500 ms digital zero silence (`torch.zeros((1, sr * 0.5))`).
- **Safety Tail Silence:** Master audio must append **500 ms safety silence** at the very end to prevent media player hardware/DAC tail clipping.
- **Master Audio Formats:**
  - Master WAV: 24,000 Hz, mono.
  - Master MP3: 128 kbps `libmp3lame`, 24,000 Hz.
- **Windows Master Export:**
  DISABLED per user instruction (do not copy to Desktop or Downloads unless explicitly requested).
- **Telegram Policy:**
  - Respect `--no-telegram` flag (do not spam chat/Telegram when requested).

---

## 5. Main Execution Scripts
- **Supervisor / Orchestrator:** `run_audiobook.py` (main entry point for interactive menu, `--status`, `--all`, single-chapter runs, verification and auto-repair).
- Primary generation worker: `scripts/generate_chapter_vid_voice.py`
- Ending post-verification: `scripts/verify_chapter_endings_gpu.py`
- Ending auto-repair worker: `scripts/auto_repair_chapter.py`
- Auxiliary repair: `scripts/repair_swallowed_segments.py`
- Contract & Guards: `fanfic_pipeline/exit_codes.py`, `fanfic_pipeline/runtime_guards.py`, `fanfic_pipeline/verification.py`

---

## 6. Output Directory Structure
`/mnt/ai/AI-Dubbing/output/` is organized strictly by Book and Chapter:
- `Книга_1_Философский_камень/`: `Пролог_02`, `Пролог_03`, `Глава_01`, `Глава_04`
- `Книга_2_Тайная_комната/`: `Глава_01`, `Глава_02`, `Глава_03`
- `_Тесты_и_Бенчмарки/`: вспомогательные тесты, сэмплы голосов, замеры
- `_Архив_Сумерки_Wide_Awake/`: архив предыдущего проекта (с symlink `wide_awake`)
