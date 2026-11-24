#!/usr/bin/env python3
"""
Automated Video Rendering for Book 2 Chapters 6 and 7.
Appends 10-second intro with crossfade and loops background video to chapter duration.
Encodes using NVIDIA NVENC hardware acceleration (h264_nvenc).
Windows delivery is permanently disabled per user instruction.
"""

import sys
import os
import time
import subprocess
from pathlib import Path

INTRO_VIDEO = "/home/nk/Загрузки/Telegram Desktop/gemini_generated_video_61ea82f7.mp4"
BG_VIDEO = "/home/nk/Загрузки/export to linux/book2_video_ref.mp4"

TASKS = [
    {
        "name": "Книга 2 Глава 06",
        "wav": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_06/Книга_2_Тайная_комната_Глава_06.wav",
        "mp4": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_06/Книга_2_Тайная_комната_Глава_06.mp4",
        "log_file": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_06/render_video.log",
    },
    {
        "name": "Книга 2 Глава 07",
        "wav": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_07/Книга_2_Тайная_комната_Глава_07.wav",
        "mp4": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_07/Книга_2_Тайная_комната_Глава_07.mp4",
        "log_file": "/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_07/render_video.log",
    },
]


def log(msg: str, log_file: str = None):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}"
    print(line, flush=True)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
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
    log_file = task["log_file"]

    log(f"=== Начало рендера: {name} ===", log_file)
    log(f"Аудио мастер: {wav}", log_file)
    log(f"Целевое видео: {mp4}", log_file)

    if not os.path.exists(wav):
        raise FileNotFoundError(f"WAV файл не найден: {wav}")
    if not os.path.exists(INTRO_VIDEO):
        raise FileNotFoundError(f"Интро видео не найдено: {INTRO_VIDEO}")
    if not os.path.exists(BG_VIDEO):
        raise FileNotFoundError(f"Фоновое видео не найдено: {BG_VIDEO}")

    audio_dur = get_duration(wav)
    trim_dur = audio_dur + 1.0
    expected_total_dur = audio_dur + 10.0
    log(f"Длительность аудио: {audio_dur:.2f} с ({audio_dur / 60:.2f} мин)", log_file)
    log(f"Ожидаемая общая длительность видео: {expected_total_dur:.2f} с ({expected_total_dur / 60:.2f} мин)", log_file)

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

    log(f"Запуск ffmpeg NVENC кодирования для {name}...", log_file)
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
                log(f"[{name}] {line.strip()}", log_file)
                last_log_time = curr_time
        elif "Error" in line or "failed" in line:
            log(f"[{name} WARNING] {line.strip()}", log_file)

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg завершился с ошибкой: код {proc.returncode}")

    os.replace(temp_mp4, mp4)
    elapsed = time.time() - start_time
    file_size_mb = os.path.getsize(mp4) / (1024 * 1024)
    rendered_dur = get_duration(mp4)

    log(f"=== Успешно завершен рендер {name} ===", log_file)
    log(f"Файл: {mp4}", log_file)
    log(f"Размер: {file_size_mb:.2f} MB ({file_size_mb / 1024:.2f} GB)", log_file)
    log(f"Длительность: {rendered_dur / 60:.2f} мин ({rendered_dur:.2f} с)", log_file)
    log(f"Время рендера: {elapsed / 60:.2f} мин ({elapsed:.1f} с), скорость: {rendered_dur / elapsed:.1f}x", log_file)


def main():
    log("==================================================")
    log("СТАРТ ПАКЕТНОГО РЕНДЕРА ВИДЕО ДЛЯ ГЛАВ 6 И 7")
    log("==================================================")

    for i, task in enumerate(TASKS, 1):
        log(f"--- Шаг {i}/{len(TASKS)}: {task['name']} ---")
        try:
            render_chapter(task)
        except Exception as e:
            log(f"ОШИБКА при рендере {task['name']}: {e}", task.get("log_file"))
            sys.exit(1)

    log("==================================================")
    log("ВСЕ ГЛАВЫ УСПЕШНО ОТРЕНДЕРЕНЫ!")
    log("==================================================")


if __name__ == "__main__":
    main()
