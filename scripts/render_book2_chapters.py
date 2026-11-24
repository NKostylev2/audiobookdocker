#!/usr/bin/env python3
"""
Automated Video Rendering for Book 2 Chapters 3 and 2.
Appends 10-second intro with crossfade and loops background video to chapter duration.
Encodes using NVIDIA NVENC hardware acceleration (h264_nvenc).
"""

import sys
import os
import time
import subprocess
from pathlib import Path

INTRO_VIDEO = "/home/nk/Загрузки/Telegram Desktop/gemini_generated_video_61ea82f7.mp4"
BG_VIDEO = "/home/nk/Загрузки/export to linux/book2_video_ref.mp4"
LOG_FILE = "/mnt/ai/AI-Dubbing/output/render_book2_chapters.log"

TASKS = [
    {
        "name": "Глава 03",
        "wav": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_03/Книга_2_Тайная_комната_Глава_03.wav",
        "mp4": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_03/Книга_2_Тайная_комната_Глава_03.mp4",
        "duration": 5677.76,
        "trim": 5678.76,
    },
    {
        "name": "Глава 02",
        "wav": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_02/chamber_of_secrets_ch02_vid_voice.wav",
        "mp4": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_02/Книга_2_Тайная_комната_Глава_02.mp4",
        "duration": 6757.80,
        "trim": 6758.80,
    },
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
        filepath
    ]
    res = subprocess.check_output(cmd).decode().strip()
    return float(res)


def render_chapter(task: dict):
    name = task["name"]
    wav = task["wav"]
    mp4 = task["mp4"]
    trim_dur = task["trim"]

    log(f"=== Начало рендера: {name} ===")
    log(f"Аудио мастер: {wav}")
    log(f"Целевое видео: {mp4}")

    if not os.path.exists(wav):
        raise FileNotFoundError(f"WAV файл не найден: {wav}")
    if not os.path.exists(INTRO_VIDEO):
        raise FileNotFoundError(f"Интро видео не найдено: {INTRO_VIDEO}")
    if not os.path.exists(BG_VIDEO):
        raise FileNotFoundError(f"Фоновое видео не найдено: {BG_VIDEO}")

    # Temporary output to ensure atomic replacement
    temp_mp4 = mp4 + ".tmp.mp4"
    if os.path.exists(temp_mp4):
        os.remove(temp_mp4)

    start_time = time.time()

    cmd = [
        "ffmpeg", "-y",
        "-i", INTRO_VIDEO,
        "-stream_loop", "-1", "-i", BG_VIDEO,
        "-i", wav,
        "-filter_complex",
        f"[0:v]scale=1920:1080:flags=lanczos,fps=30,setpts=PTS-STARTPTS,format=yuv420p,settb=1/30[v0];"
        f"[1:v]fps=30,trim=duration={trim_dur:.2f},setpts=PTS-STARTPTS,format=yuv420p,settb=1/30[v1];"
        f"[v0][v1]xfade=transition=fade:duration=1.0:offset=9.0[vout];"
        f"[0:a]atrim=duration=10.0,afade=t=out:st=9.0:d=1.0,aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
        f"[2:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]concat=n=2:v=0:a=1[aout]",
        "-map", "[vout]",
        "-map", "[aout]",
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
        "-movflags", "+faststart",
        "-shortest",
        temp_mp4
    ]

    log(f"Запуск ffmpeg NVENC кодирования для {name}...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    last_log_time = time.time()
    for line in proc.stdout:
        # Log ffmpeg progress periodically (every ~30s or on final lines)
        if "frame=" in line or "speed=" in line:
            curr_time = time.time()
            if curr_time - last_log_time >= 30.0:
                log(f"[{name}] {line.strip()}")
                last_log_time = curr_time
        elif "Error" in line or "failed" in line:
            log(f"[{name} WARNING] {line.strip()}")

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg завершился с ошибкой: код {proc.returncode}")

    # Move temp to final
    os.replace(temp_mp4, mp4)
    elapsed = time.time() - start_time
    file_size_mb = os.path.getsize(mp4) / (1024 * 1024)
    rendered_dur = get_duration(mp4)

    log(f"=== Успешно завершен рендер {name} ===")
    log(f"Файл: {mp4}")
    log(f"Размер: {file_size_mb:.2f} MB")
    log(f"Длительность: {rendered_dur / 60:.2f} мин ({rendered_dur:.2f} с)")
    log(f"Время рендера: {elapsed / 60:.2f} мин ({elapsed:.1f} с), средняя скорость: {rendered_dur / elapsed:.1f}x")


def main():
    log("==================================================")
    log("СТАРТ ПАКЕТНОГО РЕНДЕРА ВИДЕО ДЛЯ ГЛАВ 3 И 2")
    log("==================================================")

    for i, task in enumerate(TASKS, 1):
        log(f"--- Шаг {i}/{len(TASKS)}: {task['name']} ---")
        try:
            render_chapter(task)
        except Exception as e:
            log(f"ОШИБКА при рендере {task['name']}: {e}")
            sys.exit(1)

    log("==================================================")
    log("ВСЕ ГЛАВЫ УСПЕШНО ОТРЕНДЕРЕНЫ!")
    log("==================================================")


if __name__ == "__main__":
    main()
