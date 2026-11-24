#!/usr/bin/env python3
"""
Render Monolithic Video for Book 2: "Гарри Поттер и Тайная комната" (Пролог + Главы 01-07).
- No intros (starts directly with chapter audio, looped background video throughout).
- Encodes with NVIDIA NVENC (h264_nvenc, 1080p@30fps, ~7.7 Mbps).
- Injects chapter metadata for YouTube and video players (VLC, mpv).
- Saves directly to /run/media/nk/Main/Audiobooks/Книга_2/ (where 664 GB is available).
"""

import os
import sys
import time
import json
import shutil
import subprocess
from pathlib import Path
import soundfile as sf
import numpy as np

BG_VIDEO = "/home/nk/Загрузки/export to linux/book2_video_ref.mp4"
OUT_DIR = Path("/run/media/nk/Main/Audiobooks/Книга_2")
LOG_FILE = OUT_DIR / "render_full_video.log"

FULL_WAV = OUT_DIR / "Книга_2_Тайная_комната_Полная_книга.wav"
FULL_MP3 = OUT_DIR / "Книга_2_Тайная_комната_Полная_книга.mp3"
FULL_MP4 = OUT_DIR / "Книга_2_Тайная_комната_Полная_книга.mp4"
TEMP_MP4 = OUT_DIR / "Книга_2_Тайная_комната_Полная_книга.tmp.mp4"
META_FILE = OUT_DIR / "ffmetadata.txt"
CHAPTERS_TXT = OUT_DIR / "chapters_youtube.txt"

CHAPTERS = [
    ("Пролог", Path("/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Пролог/Книга_2_Тайная_комната_Пролог.wav")),
    ("Глава 01", Path("/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_01/Книга_2_Тайная_комната_Глава_01.wav")),
    ("Глава 02", Path("/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_02/chamber_of_secrets_ch02_vid_voice.wav")),
    ("Глава 03", Path("/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_03/Книга_2_Тайная_комната_Глава_03.wav")),
    ("Глава 04", Path("/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_04/Книга_2_Тайная_комната_Глава_04.wav")),
    ("Глава 05", Path("/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_05/Книга_2_Тайная_комната_Глава_05.wav")),
    ("Глава 06", Path("/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_06/Книга_2_Тайная_комната_Глава_06.wav")),
    ("Глава 07", Path("/mnt/ai/AI-Dubbing/output/Книга_2_Тайная_комната/Глава_07/Книга_2_Тайная_комната_Глава_07.wav")),
]


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


def assemble_audio_and_metadata():
    log("=== Шаг 1: Сборка единого аудиомастера (WAV) и расчет таймкодов ===")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    sample_rate = 24000
    pause_sec = 1.5
    silence_samples = np.zeros(int(sample_rate * pause_sec), dtype=np.int16)

    chapter_marks = []
    current_ms = 0

    log(f"Создание монолитного WAV: {FULL_WAV}...")
    with sf.SoundFile(str(FULL_WAV), mode="w", samplerate=sample_rate, channels=1, subtype="PCM_16") as out_f:
        for idx, (title, wav_path) in enumerate(CHAPTERS):
            if not wav_path.exists():
                raise FileNotFoundError(f"Файл главы не найден: {wav_path}")

            info = sf.info(str(wav_path))
            dur_ms = int(round(info.duration * 1000))
            start_ms = current_ms
            end_ms = current_ms + dur_ms

            chapter_marks.append({
                "title": title,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_s": info.duration
            })

            # Read and stream audio in blocks to minimize memory
            with sf.SoundFile(str(wav_path), mode="r") as in_f:
                while True:
                    data = in_f.read(65536, dtype="int16")
                    if len(data) == 0:
                        break
                    out_f.write(data)

            current_ms = end_ms

            # Append pause between chapters (except after last)
            if idx < len(CHAPTERS) - 1:
                out_f.write(silence_samples)
                current_ms += int(pause_sec * 1000)

            log(f"  + [{idx+1}/{len(CHAPTERS)}] {title}: {info.duration/60:.2f} мин ({info.duration:.1f} с)")

    total_dur_s = current_ms / 1000.0
    wav_size_gb = FULL_WAV.stat().st_size / (1024**3)
    log(f"Единый WAV успешно собран: {wav_size_gb:.2f} GB, длительность: {total_dur_s/3600:.2f} ч ({total_dur_s/60:.2f} мин)")

    # 1. Create ffmetadata.txt
    meta_lines = [
        ";FFMETADATA1",
        "title=Гарри Поттер и Тайная комната (Полная книга)",
        "artist=CosyVoice 3 (Голос: VID_20261101_122350_289)",
        "album=Гарри Поттер и Тайная комната",
        "genre=Audiobook",
        ""
    ]
    for ch in chapter_marks:
        meta_lines.append("[CHAPTER]")
        meta_lines.append("TIMEBASE=1/1000")
        meta_lines.append(f"START={ch['start_ms']}")
        meta_lines.append(f"END={ch['end_ms']}")
        meta_lines.append(f"title={ch['title']}")
        meta_lines.append("")

    with open(META_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(meta_lines) + "\n")
    log(f"Сгенерирован ffmetadata: {META_FILE}")

    # 2. Create chapters_youtube.txt
    yt_lines = ["Таймкоды глав (для YouTube):", ""]
    for ch in chapter_marks:
        s_total = ch["start_ms"] // 1000
        h = s_total // 3600
        m = (s_total % 3600) // 60
        s = s_total % 60
        tc = f"{h:02d}:{m:02d}:{s:02d}"
        yt_lines.append(f"{tc} {ch['title']}")

    with open(CHAPTERS_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(yt_lines) + "\n")
    log(f"Сгенерированы таймкоды для YouTube: {CHAPTERS_TXT}")

    return total_dur_s


def render_full_video(total_dur_s: float):
    log("=== Шаг 2: Запуск рендера Full HD MP4 видео с NVENC ===")
    if not Path(BG_VIDEO).exists():
        raise FileNotFoundError(f"Фоновое видео не найдено: {BG_VIDEO}")

    if TEMP_MP4.exists():
        TEMP_MP4.unlink()

    start_time = time.time()

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", BG_VIDEO,
        "-i", str(FULL_WAV),
        "-i", str(META_FILE),
        "-map_metadata", "2",
        "-map", "0:v",
        "-map", "1:a",
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
        "-movflags", "+faststart",
        "-shortest",
        str(TEMP_MP4)
    ]

    log(f"Команда FFmpeg NVENC запущена. Ожидаемая длительность: {total_dur_s/3600:.2f} ч...")
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

    os.replace(TEMP_MP4, FULL_MP4)
    elapsed = time.time() - start_time
    file_size_gb = FULL_MP4.stat().st_size / (1024**3)
    rendered_dur = get_duration(FULL_MP4)

    log(f"=== Успешно завершен рендер полного видео Книги 2! ===")
    log(f"Файл: {FULL_MP4}")
    log(f"Размер: {file_size_gb:.2f} GB")
    log(f"Длительность: {rendered_dur/3600:.2f} ч ({rendered_dur/60:.2f} мин)")
    log(f"Время рендера: {elapsed/60:.2f} мин ({elapsed/3600:.2f} ч), средняя скорость: {rendered_dur/elapsed:.1f}x")


def render_full_mp3():
    log("=== Шаг 3: Кодирование единого MP3 (128 kbps) ===")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(FULL_WAV),
        "-i", str(META_FILE),
        "-map_metadata", "1",
        "-codec:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", "24000",
        str(FULL_MP3)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    size_mb = FULL_MP3.stat().st_size / (1024**2)
    log(f"Единый MP3 создан: {FULL_MP3} ({size_mb:.1f} MB)")


def main():
    log("==================================================")
    log("СТАРТ ПОЛНОГО РЕНДЕРА КНИГИ 2 (БЕЗ ВСТУПЛЕНИЙ)")
    log("==================================================")

    total_dur_s = assemble_audio_and_metadata()
    render_full_video(total_dur_s)
    render_full_mp3()

    log("==================================================")
    log("ВСЕ ОПЕРАЦИИ УСПЕШНО ЗАВЕРШЕНЫ!")
    log("==================================================")


if __name__ == "__main__":
    main()
