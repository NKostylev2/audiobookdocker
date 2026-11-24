#!/usr/bin/env python3
"""
Re-render Monolithic Video for Book 2: "Гарри Поттер и Тайная комната" strictly to ~15 GB.
- 1080p @ 20 fps
- Video bitrate: 2500 kbps CBR (NVENC h264_nvenc, preset p5)
- Audio: copied directly from fixed master (AAC 48kHz, 125 kbps)
- Embedded chapter marks preserved
- Target size: ~14.8 - 15.0 GB
"""

import os
import sys
import time
import subprocess
from pathlib import Path

BG_VIDEO = "/home/nk/Загрузки/export to linux/book2_video_ref.mp4"
BASE_DIR = Path("/run/media/nk/Main/Audiobooks/Книга_2")
SOURCE_MP4 = BASE_DIR / "Книга_2_Тайная_комната_Полная_книга_fixed.mp4"
FINAL_MP4 = BASE_DIR / "Книга_2_Тайная_комната_Полная_книга_15gb.mp4"
TEMP_MP4 = BASE_DIR / "Книга_2_Тайная_комната_Полная_книга_15gb.tmp.mp4"
LOG_FILE = BASE_DIR / "rerender_15gb.log"


def log(msg: str):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_duration(filepath: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath)
    ]
    res = subprocess.check_output(cmd).decode().strip()
    return float(res)


def main():
    log("==================================================")
    log("СТАРТ РЕНДЕРА КНИГИ 2 НА СТРОГИЙ РАЗМЕР ~15 GB")
    log("==================================================")

    if not Path(BG_VIDEO).exists():
        raise FileNotFoundError(f"Фоновое видео не найдено: {BG_VIDEO}")
    if not SOURCE_MP4.exists():
        raise FileNotFoundError(f"Исходный MP4 не найден: {SOURCE_MP4}")

    source_dur = get_duration(SOURCE_MP4)
    log(f"Исходный файл: {SOURCE_MP4} ({source_dur/3600:.2f} ч / {source_dur/60:.2f} мин)")
    log(f"Целевой файл:  {FINAL_MP4}")
    log("Параметры: 1080p, 20 fps, NVENC h264_nvenc, 2500 kbps, AAC copy")

    if TEMP_MP4.exists():
        TEMP_MP4.unlink()

    start_time = time.time()

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(BG_VIDEO),
        "-i", str(SOURCE_MP4),
        "-map", "0:v",
        "-map", "1:a",
        "-map_metadata", "1",
        "-vf", "fps=20",
        "-c:v", "h264_nvenc",
        "-preset", "p5",
        "-rc", "cbr",
        "-b:v", "2500k",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-shortest",
        str(TEMP_MP4)
    ]

    log(f"Запуск FFmpeg NVENC...")
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
                log(f"[NVENC-15GB] {line.strip()}")
                last_log_time = curr_time
        elif "Error" in line or "failed" in line:
            log(f"[WARNING] {line.strip()}")

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg завершился с ошибкой: код {proc.returncode}")

    os.replace(TEMP_MP4, FINAL_MP4)
    elapsed = time.time() - start_time
    file_size_gb = FINAL_MP4.stat().st_size / (1024**3)
    rendered_dur = get_duration(FINAL_MP4)

    log("==================================================")
    log(f"РЕНДЕР НА 15 ГБ УСПЕШНО ЗАВЕРШЁН!")
    log(f"Файл: {FINAL_MP4}")
    log(f"Размер: {file_size_gb:.2f} GB (цель ~15 GB)")
    log(f"Длительность: {rendered_dur/3600:.2f} ч ({rendered_dur/60:.2f} мин)")
    log(f"Время рендера: {elapsed/60:.2f} мин ({elapsed/3600:.2f} ч), средняя скорость: {rendered_dur/elapsed:.1f}x")
    log("==================================================")


if __name__ == "__main__":
    main()
