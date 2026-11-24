#!/usr/bin/env python3
"""
Render Chapter 08 Video WITHOUT Intro:
- Looped BG: /home/nk/Загрузки/Telegram Desktop/зацикленное видео осд.mp4 (starts immediately)
- Audio: /mnt/ai/AI-Dubbing/output/Things_Only_Get_Stranger/Глава_08/Things_Only_Get_Stranger_Глава_08.mp3
- 1080p @ 30fps NVENC H.264
- Replaces master files and exports to Windows Desktop & Downloads.
"""

import os
import sys
import time
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path("/mnt/ai/AI-Dubbing/output/Things_Only_Get_Stranger")
CH_DIR = BASE_DIR / "Глава_08"
BG_VIDEO = "/home/nk/Загрузки/Telegram Desktop/зацикленное видео осд.mp4"
LOG_FILE = CH_DIR / "render_no_intro.log"

AUDIO_FILE = CH_DIR / "Things_Only_Get_Stranger_Глава_08.mp3"
OUTPUT_MP4 = CH_DIR / "Things_Only_Get_Stranger_Глава_08.mp4"
ALT_MP4 = CH_DIR / "Глава_08.mp4"
WITH_INTRO_BACKUP = CH_DIR / "Things_Only_Get_Stranger_Глава_08_with_intro.mp4"

WINDOWS_DESTS = [
    Path("/run/media/nk/System/Users/kosty/Desktop"),
    Path("/run/media/nk/Main/Downloads"),
]


def log(msg: str):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_duration(filepath: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath)
    ]
    res = subprocess.check_output(cmd).decode().strip()
    return float(res)


def main():
    log("=" * 65)
    log("🚀 РЕНДЕР ВИДЕО БЕЗ ВСТУПЛЕНИЯ: «Дальше — только страннее. Глава 8»")
    log("=" * 65)
    log(f"Аудио мастер: {AUDIO_FILE}")
    log(f"Фоновое видео:{BG_VIDEO}")
    log(f"Целевой MP4:  {OUTPUT_MP4}")

    if not AUDIO_FILE.exists():
        raise FileNotFoundError(f"Аудио не найдено: {AUDIO_FILE}")
    if not os.path.exists(BG_VIDEO):
        raise FileNotFoundError(f"Фоновое видео не найдено: {BG_VIDEO}")

    # Backup existing video with intro if not backed up yet
    if OUTPUT_MP4.exists() and not WITH_INTRO_BACKUP.exists():
        log(f"Резервное сохранение версии со вступлением -> {WITH_INTRO_BACKUP.name}...")
        shutil.copy2(OUTPUT_MP4, WITH_INTRO_BACKUP)

    audio_dur = get_duration(str(AUDIO_FILE))
    log(f"Длительность аудио: {audio_dur:.2f} с ({audio_dur / 60:.2f} мин)")

    temp_mp4 = OUTPUT_MP4.with_suffix(".no_intro.tmp.mp4")
    if temp_mp4.exists():
        temp_mp4.unlink()

    start_time = time.time()

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", BG_VIDEO,
        "-i", str(AUDIO_FILE),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "h264_nvenc",
        "-preset", "p5",
        "-rc", "vbr",
        "-cq", "20",
        "-b:v", "6M",
        "-maxrate", "10M",
        "-bufsize", "12M",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-metadata", "title=Дальше — только страннее (Глава 8. Изнанка)",
        "-metadata", "artist=hughescookingfan / Стив Харрингтон (CosyVoice 3)",
        "-metadata", "album=Дальше — только страннее",
        "-metadata", "genre=Audiobook",
        "-movflags", "+faststart",
        "-shortest",
        str(temp_mp4)
    ]

    log("Запуск FFmpeg NVENC без интро (1080p @ 30fps)...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    last_log_time = time.time()
    for line in proc.stdout:
        if "frame=" in line or "speed=" in line:
            curr_time = time.time()
            if curr_time - last_log_time >= 30.0:
                log(f"[Без интро] {line.strip()}")
                last_log_time = curr_time
        elif "Error" in line or "failed" in line:
            log(f"[WARNING] {line.strip()}")

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg завершился с ошибкой: код {proc.returncode}")

    os.replace(temp_mp4, OUTPUT_MP4)
    shutil.copy2(OUTPUT_MP4, ALT_MP4)

    elapsed = time.time() - start_time
    file_size_mb = OUTPUT_MP4.stat().st_size / (1024 * 1024)
    rendered_dur = get_duration(str(OUTPUT_MP4))

    log(f"✓ Успешно завершен рендер видео без интро!")
    log(f"  Файл:         {OUTPUT_MP4}")
    log(f"  Размер:       {file_size_mb:.2f} MB")
    log(f"  Длительность: {rendered_dur / 60:.2f} мин ({rendered_dur:.2f} с)")
    log(f"  Время:        {elapsed / 60:.2f} мин ({elapsed:.1f} с), средняя скорость: {rendered_dur / elapsed:.1f}x")

    # Windows export
    for dest in WINDOWS_DESTS:
        try:
            if dest.is_dir():
                target1 = dest / OUTPUT_MP4.name
                target2 = dest / ALT_MP4.name
                shutil.copy2(OUTPUT_MP4, target1)
                shutil.copy2(ALT_MP4, target2)
                log(f"  ✓ Экспортировано на Windows: {target1}")
        except Exception as e:
            log(f"  [Предупреждение] Не удалось скопировать на {dest}: {e}")

    log("=" * 65)
    log("=== ВИДЕО БЕЗ ВСТУПЛЕНИЯ ГОТОВО И ЭКСПОРТИРОВАНО! ===")
    log("=" * 65)


if __name__ == "__main__":
    main()
