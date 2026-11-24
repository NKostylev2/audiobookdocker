#!/usr/bin/env python3
"""
Re-render Monolithic Video for Book 2: "Гарри Поттер и Тайная комната" to target ~15 GB.
Settings:
- 1080p @ 20 fps
- Video bitrate: ~2.35 Mbps (total ~2.55 Mbps with audio AAC 192k)
- NVIDIA NVENC hardware acceleration (h264_nvenc, preset p5, VBR, CQ 24)
- Embedded chapter metadata for VLC, mpv, and YouTube
- Direct replace of monolithic MP4 at /run/media/nk/Main/Audiobooks/Книга_2/
"""

import os
import sys
import time
import subprocess
from pathlib import Path

BG_VIDEO = "/home/nk/Загрузки/export to linux/book2_video_ref.mp4"
BASE_DIR = Path("/run/media/nk/Main/Audiobooks/Книга_2")
FULL_WAV = BASE_DIR / "Книга_2_Тайная_комната_Полная_книга.wav"
META_FILE = BASE_DIR / "ffmetadata.txt"
FINAL_MP4 = BASE_DIR / "Книга_2_Тайная_комната_Полная_книга.mp4"
TEMP_MP4 = BASE_DIR / "Книга_2_Тайная_комната_Полная_книга.15gb.tmp.mp4"
LOG_FILE = BASE_DIR / "rerender_15gb.log"


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


def main():
    log("==================================================")
    log("СТАРТ ПЕРЕРЕНДЕРА КНИГИ 2 НА ЦЕЛЕВОЙ РАЗМЕР ~15 GB")
    log("==================================================")

    if not Path(BG_VIDEO).exists():
        raise FileNotFoundError(f"Фоновое видео не найдено: {BG_VIDEO}")
    if not FULL_WAV.exists():
        raise FileNotFoundError(f"Полный WAV не найден: {FULL_WAV}")
    if not META_FILE.exists():
        raise FileNotFoundError(f"Файл метаданных не найден: {META_FILE}")

    audio_dur = get_duration(FULL_WAV)
    log(f"Исходный аудиомастер: {FULL_WAV} ({audio_dur/3600:.2f} ч / {audio_dur/60:.2f} мин)")
    log(f"Целевой видеофайл:    {FINAL_MP4}")
    log("Параметры: 1080p, 20 fps, NVENC h264_nvenc, VBR ~2.35 Mbps, AAC 192k")

    if TEMP_MP4.exists():
        TEMP_MP4.unlink()

    start_time = time.time()

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(BG_VIDEO),
        "-i", str(FULL_WAV),
        "-i", str(META_FILE),
        "-map_metadata", "2",
        "-map", "0:v",
        "-map", "1:a",
        "-vf", "fps=20",
        "-c:v", "h264_nvenc",
        "-preset", "p5",
        "-rc", "vbr",
        "-cq", "24",
        "-b:v", "2350k",
        "-maxrate", "3200k",
        "-bufsize", "4500k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
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
                log(f"[NVENC] {line.strip()}")
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
    log(f"РЕ-РЕНДЕР УСПЕШНО ЗАВЕРШЁН!")
    log(f"Файл: {FINAL_MP4}")
    log(f"Размер: {file_size_gb:.2f} GB")
    log(f"Длительность: {rendered_dur/3600:.2f} ч ({rendered_dur/60:.2f} мин)")
    log(f"Время кодирования: {elapsed/60:.2f} мин ({elapsed/3600:.2f} ч), средняя скорость: {rendered_dur/elapsed:.1f}x")
    log("==================================================")


if __name__ == "__main__":
    main()
