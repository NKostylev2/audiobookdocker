---
trigger: always_on
description: Production audiobook dubbing pipeline rules and constraints for Russian TTS with CosyVoice 3
---

# Russian Audiobook TTS Pipeline (CosyVoice 3)

## Critical Operational Rules
1. **GPU & VRAM Limits (RTX 3070 8GB):**
   - CosyVoice 3 takes ~6.0–6.8 GB VRAM. Whisper verification MUST run on CPU (`whisper.load_model('base', device='cpu')`) to maintain zero VRAM overhead.
   - NEVER run two concurrent CUDA processes.
2. **Text Preparation:**
   - Multi-line paragraphs must split on `\n\s*\n+` and join internal lines with spaces.
   - Blocks must be 35–65 tokens. Blocks >70 tokens cause CosyVoice to drop trailing sentences.
   - Trailing punctuation must be sanitized to `.`.
   - 100% word count and word order matching must be verified before synthesis.
3. **Ending Protection & Self-Healing:**
   - Every segment's audio ending is verified against the last 2–3 target words via Whisper.
   - If swallowed: retry up to 3 times with seeds `base_seed + [17, 42]`.
   - If retries fail: automatically split into two natural sentence halves (`razdel.sentenize`), synthesize each half independently, verify with Whisper, and join with 200 ms silence.
4. **Mastering & Windows Delivery:**
   - Sentence pause: 200 ms. Paragraph pause: 500 ms. Final safety silence: 500 ms.
   - MP3: 128 kbps libmp3lame, 24 000 Hz.
   - Windows delivery DISABLED per user request (do not copy to Desktop or Downloads).
   - Default scripts: `run_audiobook.py` (supervisor / entry point) and `scripts/generate_chapter_vid_voice.py` (worker).
5. **Output Directory Hierarchy:**
   - Saved in `/mnt/ai/AI-Dubbing/output/` strictly under `Книга_1_Философский_камень/` or `Книга_2_Тайная_комната/` with subfolders `Глава_XX` or `Пролог_XX`.
   - Tests and benchmarks live in `_Тесты_и_Бенчмарки/`.
