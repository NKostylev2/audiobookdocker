# Russian Audiobook TTS Pipeline — Project Overview for Claude Review

## 1. Project Purpose & Scope
This project is an end-to-end automated production pipeline for high-fidelity Russian audiobooks.
It transforms translated Russian chapter texts into professional mastered audio (and video) using:
- **CosyVoice 3 (Fun-CosyVoice3-0.5B)** in zero-shot cloning mode (24,000 Hz, mono).
- **OpenAI Whisper (base model)** for multi-stage acoustic verification of endings.
- **FFmpeg + NVIDIA NVENC** for video mastering (1080p, 20 fps, 2500 kbps, AAC stereo).

---

## 2. Hardware Constraints & VRAM Ceiling (CRITICAL)
- **GPU:** NVIDIA GeForce RTX 3070 (8 GB GDDR6 VRAM).
- **Operating System:** Linux (Arch / systemd, CUDA 12.x / Driver 595.x).
- **VRAM Ceiling:** CosyVoice 3 consumes ~6.0–6.8 GB VRAM during inference.
- **Strict Process Isolation Rule:**
  - Concurrent CUDA processes will crash the driver with CUDA Out-of-Memory (OOM).
  - During synthesis, in-flight verification runs on **CPU** (`whisper.load_model('base', device='cpu')`), consuming 0 VRAM and taking ~1.5s per chunk.
  - Full chapter post-verification on CUDA Whisper (`verify_chapter_endings_gpu.py`) runs **only after** the CosyVoice synthesis process has completely finished and exited.

---

## 3. Pipeline Architecture & Execution Flow

```
[Chapter Text (.txt)]
        │
        ▼
[run_audiobook.py] ── Master Autonomous Orchestrator (CLI & Interactive Menu)
        │
        ├─► 1. Preprocessing & Segmentation (SentenceSegmenter, 35–65 tokens)
        │      • Paragraph split: \n\s*\n+
        │      • Intra-paragraph newlines joined with space
        │      • Word count check: exact 100% token preservation required
        │
        ├─► 2. Voice Generation (scripts/generate_chapter_vid_voice.py)
        │      • CosyVoice 3 zero-shot inference with reference prompt
        │      • In-flight CPU Whisper ending check
        │      • Audio sanitization: DC offset removal, initial burst trimming, 15ms raised-cosine fades
        │      • Dynamic pause mastering: 200ms sentence, 500ms paragraph, 500ms safety tail
        │      • Initial Master WAV & 128 kbps MP3 assembly
        │      • Telegram delivery
        │
        ├─► 3. Post-Synthesis GPU Verification (scripts/verify_chapter_endings_gpu.py)
        │      • Whisper 'base' on CUDA (runs with full 8GB VRAM free)
        │      • Checks every segment's audio ending against stem of expected text
        │      • Generates gpu_verification_report.json
        │
        ├─► 4. Autonomous Self-Healing Loop (scripts/auto_repair_chapter.py)
        │      • If issues_count > 0:
        │          - Smart dialogue/punctuation split on flagged segments
        │          - Multi-seed sweep (seeds: 42, 779, 17, 77, 222, 500, etc.)
        │          - Audio sanitization & master reassembly
        │          - Re-verify on GPU until 0 issues (up to 3 passes)
        │
        └─► 5. Video Mastering (Optional via --video)
               • Hardware NVENC (h264_nvenc, 1080p, 20 fps, 2500 kbps, AAC stereo)
               • Looped atmospheric background video / visualizer
```

---

## 4. Key Files and Directory Structure

### Entry Point & Orchestration:
- `run_audiobook.py`: Single-command master runner with interactive terminal UI, batch mode (`--all`), GPU verification, and auto-repair self-healing loop.

### Core Processing Scripts (`scripts/`):
- `scripts/generate_chapter_vid_voice.py`: Main synthesis pipeline (text loading, tokenization, CosyVoice 3 inference, in-flight CPU verification, audio sanitization, Telegram upload).
- `scripts/verify_chapter_endings_gpu.py`: Fast GPU-accelerated Whisper post-verification for 100% ending fidelity.
- `scripts/auto_repair_chapter.py`: Autonomous self-healing repair for any degraded or swallowed segments.
- `scripts/render_book2_15gb_final.py`: High-efficiency NVENC video renderer for full-length monolithic book videos.

### NLP & Tokenization Engine (`fanfic_pipeline/`):
- `fanfic_pipeline/segmenter.py`: Token-aware segmenter (`SentenceSegmenter`) respecting model context limits (35–65 tokens) to avoid EOS dropping.
- `fanfic_pipeline/number_words.py`: Russian number-to-words normalizer.
- `fanfic_pipeline/robsten_scraper.py`: Translation parser and scrapers.

### Documentation & Operational Rules:
- `GEMINI.md`: Full architectural reference, VRAM budget, and segmenting rules.
- `.agent/rules/audiobook-pipeline.md`: Agent operational guidelines.

---

## 5. Specific Areas Requested for Claude Review:
1. **Pipeline Reliability & Edge-Case Handling:**
   - Are there any potential unhandled exceptions or edge cases during long batch synthesis?
2. **Audio Sanitization & Mastering Quality:**
   - Analysis of `sanitize_segment_audio` (DC offset, transient detection, cosine fades) and silence insertion.
3. **Segmentation & Token Budget:**
   - Review of `SentenceSegmenter` token windowing (35–65 tokens) and word preservation guarantees.
4. **VRAM Safety & Multiprocessing Architecture:**
   - Review of the process isolation model between CosyVoice and GPU Whisper.
5. **Code Modularity & Optimization Opportunities:**
   - Suggestions for refactoring, clean design patterns, or performance improvements.
