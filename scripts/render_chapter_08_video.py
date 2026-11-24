#!/usr/bin/env python3
"""
Automated Video Rendering for Stranger Things Audiobook:
"Дальше — только страннее (Глава 8. Изнанка)" by hughescookingfan.
- Intro: /home/nk/Загрузки/Telegram Desktop/вступление осд.mp4 (7s with 1s crossfade)
- Looped BG: /home/nk/Загрузки/Telegram Desktop/зацикленное видео осд.mp4
- Audio: Studio Warmth mastered audio (Things_Only_Get_Stranger_Глава_08.mp3)
- Encodes using NVIDIA NVENC hardware acceleration (h264_nvenc, 1080p, 30fps).
- Automatically exports to Windows Desktop & Downloads.
"""

import os
import sys
import time
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path("/mnt/ai/AI-Dubbing/output/Things_Only_Get_Stranger")
CH_DIR = BASE_DIR / "Глава_08"
INTRO_VIDEO = "/home/nk/Загрузки/Telegram Desktop/вступление осд.mp4"
BG_VIDEO = "/home/nk/Загрузки/Telegram Desktop/зацикленное видео осд.mp4"
LOG_FILE = CH_DIR / "render_video.log"

AUDIO_FILE = CH_DIR / "Things_Only_Get_Stranger_Глава_08.mp3"
OUTPUT_MP4 = CH_DIR / "Things_Only_Get_Stranger_Глава_08.mp4"
ALT_MP4 = CH_DIR / "Глава_08.mp4"
VIDEO_META = CH_DIR / "ffmetadata_video.txt"

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


def create_chapter_metadata(audio_dur_s: float, intro_offset_ms: int = 7000):
    total_end_ms = int(round((7.0 + audio_dur_s) * 1000))
    lines = [
        ";FFMETADATA1",
        "title=Дальше — только страннее (Глава 8. Изнанка)",
        "artist=hughescookingfan / Стив Харрингтон (CosyVoice 3)",
        "album=Дальше — только страннее",
        "genre=Audiobook",
        "",
        "[CHAPTER]",
        "TIMEBASE=1/1000",
        "START=0",
        f"END={intro_offset_ms}",
        "title=Вступление",
        "",
        "[CHAPTER]",
        "TIMEBASE=1/1000",
        f"START={intro_offset_ms}",
        f"END={total_end_ms}",
        "title=Глава 08. Изнанка",
        ""
    ]
    with open(VIDEO_META, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    log(f"Generated video chapter metadata: {VIDEO_META}")
    return VIDEO_META


def render():
    log("=" * 65)
    log("🚀 НАЧАЛО РЕНДЕРА ВИДЕО: «Дальше — только страннее. Глава 8»")
    log("=" * 65)
    log(f"Аудио мастер: {AUDIO_FILE}")
    log(f"Целевой MP4:  {OUTPUT_MP4}")
    log(f"Вступление:   {INTRO_VIDEO}")
    log(f"Фоновое видео:{BG_VIDEO}")

    if not AUDIO_FILE.exists():
        raise FileNotFoundError(f"Аудио не найдено: {AUDIO_FILE}")
    if not os.path.exists(INTRO_VIDEO):
        raise FileNotFoundError(f"Интро видео не найдено: {INTRO_VIDEO}")
    if not os.path.exists(BG_VIDEO):
        raise FileNotFoundError(f"Фоновое видео не найдено: {BG_VIDEO}")

    audio_dur = get_duration(str(AUDIO_FILE))
    trim_dur = audio_dur + 1.0
    total_expected_dur = 7.0 + audio_dur
    log(f"Длительность аудио: {audio_dur:.2f} с ({audio_dur / 60:.2f} мин)")
    log(f"Ожидаемая общая длительность видео: {total_expected_dur:.2f} с ({total_expected_dur / 60:.2f} мин)")

    meta_path = create_chapter_metadata(audio_dur, intro_offset_ms=7000)

    temp_mp4 = OUTPUT_MP4.with_suffix(".tmp.mp4")
    if temp_mp4.exists():
        temp_mp4.unlink()

    start_time = time.time()

    cmd = [
        "ffmpeg", "-y",
        "-i", INTRO_VIDEO,
        "-stream_loop", "-1", "-i", BG_VIDEO,
        "-i", str(AUDIO_FILE),
        "-i", str(meta_path),
        "-filter_complex",
        f"[0:v]scale=1920:1080:flags=lanczos,fps=30,setpts=PTS-STARTPTS,format=yuv420p,settb=1/30[v0];"
        f"[1:v]fps=30,trim=duration={trim_dur:.2f},setpts=PTS-STARTPTS,format=yuv420p,settb=1/30[v1];"
        f"[v0][v1]xfade=transition=fade:duration=1.0:offset=6.0,format=yuv420p[vout];"
        f"[0:a]atrim=duration=7.0,afade=t=out:st=6.0:d=1.0,aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
        f"[2:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]concat=n=2:v=0:a=1[aout]",
        "-map", "[vout]",
        "-map", "[aout]",
        "-map_metadata", "3",
        "-map_chapters", "3",
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
        str(temp_mp4)
    ]

    log("Запуск FFmpeg NVENC (1080p @ 30fps) для Главы 8...")
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
                log(f"[Глава 08] {line.strip()}")
                last_log_time = curr_time
        elif "Error" in line or "failed" in line:
            log(f"[Глава 08 WARNING] {line.strip()}")

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg завершился с ошибкой: код {proc.returncode}")

    os.replace(temp_mp4, OUTPUT_MP4)
    shutil.copy2(OUTPUT_MP4, ALT_MP4)

    elapsed = time.time() - start_time
    file_size_mb = OUTPUT_MP4.stat().st_size / (1024 * 1024)
    rendered_dur = get_duration(str(OUTPUT_MP4))

    log(f"✓ Успешно завершен рендер Главы 8!")
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
    log("=== ВИДЕО ГЛАВЫ 08 УСПЕШНО СРЕНДЕРЕНО И ЭКСПОРТИРОВАНО! ===")
    log("=" * 65)


if __name__ == "__main__":
    render()
