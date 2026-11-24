#!/usr/bin/env python3
"""
Automated Video Rendering for Stranger Things Audiobook:
"Дальше — только страннее" (Things Only Get Stranger) by hughescookingfan.
- Intro: /home/nk/Загрузки/Telegram Desktop/вступление осд.mp4 (7s with 1s crossfade)
- Looped BG: /home/nk/Загрузки/Telegram Desktop/зацикленное видео осд.mp4
- Audio: Studio Warmth mastered audio (Things_Only_Get_Stranger_Главы_01-07.mp3)
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
INTRO_VIDEO = "/home/nk/Загрузки/Telegram Desktop/вступление осд.mp4"
BG_VIDEO = "/home/nk/Загрузки/Telegram Desktop/зацикленное видео осд.mp4"
LOG_FILE = BASE_DIR / "render_video.log"

FULL_AUDIO = BASE_DIR / "Things_Only_Get_Stranger_Главы_01-07.mp3"
FULL_MP4 = BASE_DIR / "Things_Only_Get_Stranger_Главы_01-07.mp4"
VIDEO_META = BASE_DIR / "ffmetadata_video.txt"

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


def create_video_metadata(intro_offset_ms: int = 7000):
    """Generate metadata file with shifted chapters including the intro."""
    raw_meta = BASE_DIR / "ffmetadata.txt"
    if not raw_meta.exists():
        log(f"Warning: {raw_meta} not found. Skipping chapter metadata generation.")
        return None

    lines = [
        ";FFMETADATA1",
        "title=Дальше — только страннее (Главы 1–7)",
        "artist=hughescookingfan / Стив Харрингтон (CosyVoice 3)",
        "album=Дальше — только страннее",
        "genre=Audiobook",
        "",
        "[CHAPTER]",
        "TIMEBASE=1/1000",
        "START=0",
        f"END={intro_offset_ms}",
        "title=Вступление",
        ""
    ]

    in_chapter = False
    current_chapter = {}
    with open(raw_meta, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line == "[CHAPTER]":
                in_chapter = True
                current_chapter = {}
            elif in_chapter and line.startswith("START="):
                current_chapter["START"] = int(line.split("=")[1]) + intro_offset_ms
            elif in_chapter and line.startswith("END="):
                current_chapter["END"] = int(line.split("=")[1]) + intro_offset_ms
            elif in_chapter and line.startswith("title="):
                current_chapter["title"] = line.split("=", 1)[1]
                lines.append("[CHAPTER]")
                lines.append("TIMEBASE=1/1000")
                lines.append(f"START={current_chapter['START']}")
                lines.append(f"END={current_chapter['END']}")
                lines.append(f"title={current_chapter['title']}")
                lines.append("")
                in_chapter = False

    with open(VIDEO_META, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    log(f"Generated video chapter metadata: {VIDEO_META}")
    return VIDEO_META


def render_file(name: str, audio_path: Path, output_mp4: Path, meta_path: Path = None):
    log(f"\n=======================================================")
    log(f"=== Начало рендера видео: {name} ===")
    log(f"=======================================================")
    log(f"Аудио мастер: {audio_path}")
    log(f"Целевой MP4:  {output_mp4}")

    if not audio_path.exists():
        raise FileNotFoundError(f"Аудио не найдено: {audio_path}")
    if not os.path.exists(INTRO_VIDEO):
        raise FileNotFoundError(f"Интро видео не найдено: {INTRO_VIDEO}")
    if not os.path.exists(BG_VIDEO):
        raise FileNotFoundError(f"Фоновое видео не найдено: {BG_VIDEO}")

    audio_dur = get_duration(audio_path)
    trim_dur = audio_dur + 1.0
    total_expected_dur = 7.0 + audio_dur
    log(f"Длительность аудио: {audio_dur:.2f} с ({audio_dur / 60:.2f} мин)")
    log(f"Ожидаемая общая длительность видео: {total_expected_dur:.2f} с ({total_expected_dur / 60:.2f} мин)")

    temp_mp4 = output_mp4.with_suffix(".tmp.mp4")
    if temp_mp4.exists():
        temp_mp4.unlink()

    start_time = time.time()

    cmd = [
        "ffmpeg", "-y",
        "-i", INTRO_VIDEO,
        "-stream_loop", "-1", "-i", BG_VIDEO,
        "-i", str(audio_path),
    ]

    if meta_path and meta_path.exists():
        cmd.extend(["-i", str(meta_path)])
        map_meta_idx = "3"
    else:
        map_meta_idx = None

    cmd.extend([
        "-filter_complex",
        f"[0:v]scale=1920:1080:flags=lanczos,fps=30,setpts=PTS-STARTPTS,format=yuv420p,settb=1/30[v0];"
        f"[1:v]fps=30,trim=duration={trim_dur:.2f},setpts=PTS-STARTPTS,format=yuv420p,settb=1/30[v1];"
        f"[v0][v1]xfade=transition=fade:duration=1.0:offset=6.0,format=yuv420p[vout];"
        f"[0:a]atrim=duration=7.0,afade=t=out:st=6.0:d=1.0,aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
        f"[2:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]concat=n=2:v=0:a=1[aout]",
        "-map", "[vout]",
        "-map", "[aout]",
    ])

    if map_meta_idx:
        cmd.extend([
            "-map_metadata", map_meta_idx,
            "-map_chapters", map_meta_idx,
        ])

    cmd.extend([
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
    ])

    log(f"Запуск FFmpeg NVENC (1080p @ 30fps) для {name}...")
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
                log(f"[{name}] {line.strip()}")
                last_log_time = curr_time
        elif "Error" in line or "failed" in line:
            log(f"[{name} WARNING] {line.strip()}")

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg завершился с ошибкой: код {proc.returncode}")

    os.replace(temp_mp4, output_mp4)
    elapsed = time.time() - start_time
    file_size_mb = output_mp4.stat().st_size / (1024 * 1024)
    rendered_dur = get_duration(str(output_mp4))

    log(f"✓ Успешно завершен рендер {name}!")
    log(f"  Файл:         {output_mp4}")
    log(f"  Размер:       {file_size_mb:.2f} MB")
    log(f"  Длительность: {rendered_dur / 60:.2f} мин ({rendered_dur:.2f} с)")
    log(f"  Время:        {elapsed / 60:.2f} мин ({elapsed:.1f} с), средняя скорость: {rendered_dur / elapsed:.1f}x")

    # Windows export
    for dest in WINDOWS_DESTS:
        try:
            if dest.is_dir():
                target = dest / output_mp4.name
                shutil.copy2(output_mp4, target)
                log(f"  ✓ Экспортировано на Windows: {target}")
        except Exception as e:
            log(f"  [Предупреждение] Не удалось скопировать на {dest}: {e}")

    return output_mp4


def main():
    log("▶ Запуск конвейера видеорендера для проекта Stranger Things...")

    # 1. Render FULL MASTER VIDEO
    meta_path = create_video_metadata(intro_offset_ms=7000)
    render_file(
        name="Полная аудиокнига (Главы 01-07)",
        audio_path=FULL_AUDIO,
        output_mp4=FULL_MP4,
        meta_path=meta_path
    )

    log("\n=======================================================")
    log("=== БОЛЬШОЕ ВИДЕО УСПЕШНО СРЕНДЕРЕНО И ЭКСПОРТИРОВАНО! ===")
    log("=======================================================")


if __name__ == "__main__":
    main()
