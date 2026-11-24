#!/usr/bin/env python3
"""
Batch generation script for 'Things Only Get Stranger' (Дальше — только страннее)
Chapters 01 to 07 using Steve Harrington voice profile (CosyVoice 3).

Features:
- Full Whisper CPU in-flight verification (zero VRAM overhead)
- Seed retries (+17, +42) and fallback splitting on persistent swallows
- Automatic segment caching (fully resumable if interrupted)
- 200ms sentence pause, 500ms paragraph pause, 500ms safety tail silence
- Master 24kHz mono WAV and 128 kbps MP3
- Automatic export to Windows Desktop and Downloads if mounted
"""

import os
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
import sys
import time
import argparse
import logging
import subprocess
from pathlib import Path

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
RU_DIR = ROOT_DIR / "output" / "Things_Only_Get_Stranger" / "ru"
OUTPUT_BASE = ROOT_DIR / "output" / "Things_Only_Get_Stranger"
VOICE_FILE = ROOT_DIR / "voices" / "steve_harrington.wav"
GENERATE_SCRIPT = ROOT_DIR / "scripts" / "generate_chapter_vid_voice.py"
PYTHON_BIN = Path("/home/nk/.venvs/cosyvoice-env/bin/python")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("BatchStrangerThings")


def get_available_chapters():
    files = sorted(list(RU_DIR.glob("Things_Only_Get_Stranger_Глава_*.txt")))
    chapters = []
    for f in files:
        # Extract chapter number
        import re
        m = re.search(r"Глава_(\d+)", f.name)
        if m:
            ch_num = int(m.group(1))
            chapters.append((ch_num, f))
    return sorted(chapters, key=lambda x: x[0])


def run_batch(from_ch: int = 1, to_ch: int = 7, no_telegram: bool = True):
    all_chapters = get_available_chapters()
    selected = [(num, path) for num, path in all_chapters if from_ch <= num <= to_ch]

    if not selected:
        logger.error(f"No chapters found in range {from_ch} to {to_ch} in {RU_DIR}")
        return

    logger.info("=" * 60)
    logger.info(f"🚀 СТАРТ БАТЧ-ГЕНЕРАЦИИ: «Дальше — только страннее»")
    logger.info(f"Главы: {[num for num, _ in selected]} (всего: {len(selected)})")
    logger.info(f"Голос: {VOICE_FILE.name}")
    logger.info(f"Выходная папка: {OUTPUT_BASE}")
    logger.info("=" * 60)

    total_t0 = time.time()
    completed = []

    for idx, (ch_num, ch_path) in enumerate(selected, 1):
        ch_dir = OUTPUT_BASE / f"Глава_{ch_num:02d}"
        ch_dir.mkdir(parents=True, exist_ok=True)
        ch_title = f"Дальше — только страннее. Глава {ch_num}"

        logger.info(f"\n▶ [{idx}/{len(selected)}] Запуск Главы {ch_num:02d}: {ch_path.name}")
        t_ch0 = time.time()

        cmd = [
            str(PYTHON_BIN),
            str(GENERATE_SCRIPT),
            "--text-path", str(ch_path),
            "--voice-file", str(VOICE_FILE),
            "--output-dir", str(ch_dir),
            "--title", ch_title,
            "--whisper-verify"
        ]
        if no_telegram:
            cmd.append("--no-telegram")

        logger.info(f"Команда: {' '.join(cmd)}")
        ret = subprocess.run(cmd)

        ch_elapsed = time.time() - t_ch0
        if ret.returncode == 0:
            logger.info(f"✓ Глава {ch_num:02d} завершена за {ch_elapsed / 60:.1f} мин. ({ch_elapsed:.1f}с)")
            completed.append(ch_num)
        else:
            logger.error(f"❌ Ошибка в Главе {ch_num:02d}! Код возврата: {ret.returncode}")
            # Stop or continue?
            break

    total_elapsed = time.time() - total_t0
    logger.info("\n" + "=" * 60)
    logger.info(f"🎉 БАТЧ-ГЕНЕРАЦИЯ ЗАВЕРШЕНА!")
    logger.info(f"Успешно сгенерировано: {len(completed)}/{len(selected)} глав: {completed}")
    logger.info(f"Общее время работы: {total_elapsed / 60:.1f} мин. ({total_elapsed:.1f}с)")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Batch generate Stranger Things chapters")
    parser.add_argument("--from-chapter", type=int, default=1, help="Start chapter number (default: 1)")
    parser.add_argument("--to-chapter", type=int, default=7, help="End chapter number (default: 7)")
    parser.add_argument("--no-telegram", action="store_true", default=True, help="Disable Telegram notifications")
    args = parser.parse_args()

    run_batch(from_ch=args.from_chapter, to_ch=args.to_chapter, no_telegram=args.no_telegram)


if __name__ == "__main__":
    main()
