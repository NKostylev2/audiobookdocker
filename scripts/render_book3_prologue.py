#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NVENC Video Rendering for Book 3 Prologue (Узник Азкабана).
Uses background video loop and masters 1080p 20fps 2500kbps NVENC video with AAC stereo audio.
"""

import os
import sys
import time
import shutil
import subprocess
from pathlib import Path

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
BG_VIDEO = ROOT_DIR / "output" / "Книга_3_Узник_Азкабана" / "YouCut_20261123_155727575.mp4"
CHAP_DIR = ROOT_DIR / "output" / "Книга_3_Узник_Азкабана" / "Пролог"
AUDIO_WAV = CHAP_DIR / "Книга_3_Узник_Азкабана_Пролог.wav"
FINAL_MP4 = CHAP_DIR / "Книга_3_Узник_Азкабана_Пролог.mp4"
TEMP_MP4 = CHAP_DIR / "Книга_3_Узник_Азкабана_Пролог.tmp.mp4"
LOG_FILE = CHAP_DIR / "render_video.log"

def log(msg: str):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def get_duration(filepath: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath)
    ]
    res = subprocess.check_output(cmd).decode().strip()
    return float(res)

def send_telegram(msg: str):
    try:
        sys.path.insert(0, str(ROOT_DIR / "scripts"))
        from generate_chapter_vid_voice import get_telegram_config, send_telegram_message
        token, chat_id, proxy = get_telegram_config()
        if token and chat_id:
            send_telegram_message(msg, token, chat_id, proxy)
    except Exception as e:
        log(f"Telegram notify warning: {e}")

def main():
    log("=" * 60)
    log("🚀 Запуск NVENC видеорендера: Книга 3 — Пролог")
    log(f"Фоновое видео: {BG_VIDEO}")
    log(f"Мастер-аудио:  {AUDIO_WAV}")
    log(f"Выходной файл: {FINAL_MP4}")
    log("=" * 60)

    if not BG_VIDEO.exists():
        log(f"❌ ОШИБКА: Фоновое видео не найдено: {BG_VIDEO}")
        sys.exit(1)
    if not AUDIO_WAV.exists():
        log(f"❌ ОШИБКА: Аудио не найдено: {AUDIO_WAV}")
        sys.exit(1)

    dur = get_duration(AUDIO_WAV)
    log(f"Длительность аудио: {dur/60:.2f} мин ({int(dur)} сек)")

    if TEMP_MP4.exists():
        TEMP_MP4.unlink()

    ffmpeg = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    cmd = [
        ffmpeg, "-y",
        "-stream_loop", "-1", "-i", str(BG_VIDEO),
        "-i", str(AUDIO_WAV),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-vf", "fps=20,format=yuv420p",
        "-c:v", "h264_nvenc",
        "-preset", "p5",
        "-tune", "hq",
        "-b:v", "2500k",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        "-shortest",
        str(TEMP_MP4)
    ]

    log("Запуск FFmpeg NVENC процесса...")
    t0 = time.time()

    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    stderr_lines = []
    last_reported = 0

    while True:
        line = p.stderr.readline()
        if not line and p.poll() is not None:
            break
        if line:
            stderr_lines.append(line)
            # Parse progress: frame= ... time=00:12:34.56
            if "time=" in line:
                import re
                m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                if m:
                    hours, mins, secs = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    cur_secs = hours * 3600 + mins * 60 + secs
                    pct = int(min(100, (cur_secs / dur) * 100))
                    if pct >= last_reported + 10 and pct > 0:
                        elapsed = time.time() - t0
                        speed = cur_secs / elapsed if elapsed > 0 else 1.0
                        eta = (dur - cur_secs) / speed if speed > 0 else 0
                        log(f"Прогресс рендера: {pct:3d}% [{cur_secs/60:.1f}/{dur/60:.1f} мин] (скорость {speed:.1f}x, осталось ~{eta/60:.1f} мин)")
                        last_reported = (pct // 10) * 10

    p.wait()

    if p.returncode != 0:
        log(f"❌ Ошибка рендера! Код {p.returncode}")
        log("".join(stderr_lines[-30:]))
        if TEMP_MP4.exists():
            TEMP_MP4.unlink()
        sys.exit(p.returncode)

    TEMP_MP4.rename(FINAL_MP4)
    total_elapsed = time.time() - t0
    final_size_mb = FINAL_MP4.stat().st_size / 1024 / 1024
    final_size_gb = final_size_mb / 1024

    log("=" * 60)
    log(f"🎉 Видео успешно создано: {FINAL_MP4}")
    log(f"Размер: {final_size_mb:.1f} MB ({final_size_gb:.2f} GB)")
    log(f"Время рендера: {total_elapsed/60:.2f} мин (скорость {dur/total_elapsed:.1f}x realtime)")
    log("=" * 60)

    tg_msg = (
        f"🎬 <b>Видео рендер завершён!</b>\n"
        f"📖 <b>Книга 3: Узник Азкабана — Пролог</b>\n"
        f"⏱️ Длительность: <b>{dur/60:.1f} мин.</b>\n"
        f"💾 Размер: <b>{final_size_gb:.2f} GB</b> ({int(final_size_mb)} MB)\n"
        f"⚙️ Формат: <code>1080p @ 20 fps, NVENC 2500 kbps, AAC stereo 48k</code>\n"
        f"⚡ Время рендера: <b>{total_elapsed/60:.1f} мин.</b> ({dur/total_elapsed:.1f}x)"
    )
    send_telegram(tg_msg)

if __name__ == "__main__":
    main()
