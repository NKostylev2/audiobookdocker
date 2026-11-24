#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NVENC Video Rendering for any Book 3 Chapter (Узник Азкабана).
Uses background video loop and renders 1080p 20fps 2500kbps NVENC video with AAC stereo audio.
"""

import os
import sys
import time
import shutil
import argparse
import subprocess
from pathlib import Path

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
DEFAULT_BG = ROOT_DIR / "output" / "Книга_3_Узник_Азкабана" / "YouCut_20261123_155727575.mp4"
DEFAULT_CHAP = ROOT_DIR / "output" / "Книга_3_Узник_Азкабана" / "Глава_01"

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
        print(f"Telegram notify warning: {e}", flush=True)

def main():
    parser = argparse.ArgumentParser(description="Render 1080p NVENC video for chapter.")
    parser.add_argument("--chapter-dir", default=str(DEFAULT_CHAP), help="Chapter directory")
    parser.add_argument("--bg-video", default=str(DEFAULT_BG), help="Path to background video loop")
    args = parser.parse_args()

    chap_dir = Path(args.chapter_dir).resolve()
    bg_video = Path(args.bg_video).resolve()

    log_file = chap_dir / "render_video.log"

    def log(msg: str):
        timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
        line = f"{timestamp} {msg}"
        print(line, flush=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # Find master wav
    safe_stem = f"{chap_dir.parent.name}_{chap_dir.name}" if chap_dir.parent.name.startswith("Книга") else chap_dir.name
    audio_wav = chap_dir / f"{safe_stem}.wav"
    final_mp4 = chap_dir / f"{safe_stem}.mp4"
    temp_mp4 = chap_dir / f"{safe_stem}.tmp.mp4"

    log("=" * 60)
    log(f"🚀 Запуск NVENC видеорендера: {chap_dir.parent.name} — {chap_dir.name}")
    log(f"Фоновое видео: {bg_video}")
    log(f"Мастер-аудио:  {audio_wav}")
    log(f"Выходной файл: {final_mp4}")
    log("=" * 60)

    if not bg_video.exists():
        log(f"❌ ОШИБКА: Фоновое видео не найдено: {bg_video}")
        sys.exit(1)
    if not audio_wav.exists():
        log(f"❌ ОШИБКА: Аудио не найдено: {audio_wav}")
        sys.exit(1)

    dur = get_duration(audio_wav)
    log(f"Длительность аудио: {dur/60:.2f} мин ({int(dur)} сек)")

    if temp_mp4.exists():
        temp_mp4.unlink()

    ffmpeg = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    cmd = [
        ffmpeg, "-y",
        "-stream_loop", "-1", "-i", str(bg_video),
        "-i", str(audio_wav),
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
        str(temp_mp4)
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
        if temp_mp4.exists():
            temp_mp4.unlink()
        sys.exit(p.returncode)

    temp_mp4.rename(final_mp4)
    total_elapsed = time.time() - t0
    final_size_mb = final_mp4.stat().st_size / 1024 / 1024
    final_size_gb = final_size_mb / 1024

    log("=" * 60)
    log(f"🎉 Видео успешно создано: {final_mp4}")
    log(f"Размер: {final_size_mb:.1f} MB ({final_size_gb:.2f} GB)")
    log(f"Время рендера: {total_elapsed/60:.2f} мин (скорость {dur/total_elapsed:.1f}x realtime)")
    log("=" * 60)

    chap_display = f"{chap_dir.parent.name.replace('_', ' ')} — {chap_dir.name.replace('_', ' ')}"
    tg_msg = (
        f"🎬 <b>Видео рендер завершён!</b>\n"
        f"📖 <b>{chap_display}</b>\n"
        f"⏱️ Длительность: <b>{dur/60:.1f} мин.</b>\n"
        f"💾 Размер: <b>{final_size_gb:.2f} GB</b> ({int(final_size_mb)} MB)\n"
        f"⚙️ Формат: <code>1080p @ 20 fps, NVENC 2500 kbps, AAC stereo 48k</code>\n"
        f"⚡ Время рендера: <b>{total_elapsed/60:.1f} мин.</b> ({dur/total_elapsed:.1f}x)"
    )
    send_telegram(tg_msg)

if __name__ == "__main__":
    main()
