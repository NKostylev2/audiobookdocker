#!/usr/bin/env python3
"""
Orchestration Script for Chapter 08:
1. Automated CosyVoice 3 synthesis with Whisper CPU ending verification.
2. In-flight artifact elimination (DC offset, pre-speech bursts, 15ms raised-cosine fades).
3. Subsequent artifact inspection & verification of 100% segments.
4. Studio Warmth mastering (44.1 kHz, 192 kbps, EBU R128).
5. Automatic delivery to Windows Desktop & Downloads.
"""

import os
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
import sys
import json
import time
import shutil
import logging
import subprocess
from pathlib import Path

import torch
import torchaudio
import soundfile as sf
import numpy as np

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
CH_DIR = ROOT_DIR / "output" / "Things_Only_Get_Stranger" / "Глава_08"
TEXT_PATH = ROOT_DIR / "output" / "Things_Only_Get_Stranger" / "ru" / "Things_Only_Get_Stranger_Глава_08.txt"
VOICE_FILE = ROOT_DIR / "voices" / "steve_harrington.wav"
GENERATE_SCRIPT = ROOT_DIR / "scripts" / "generate_chapter_vid_voice.py"
PYTHON_BIN = Path("/home/nk/.venvs/cosyvoice-env/bin/python")

LOG_FILE = CH_DIR / "pipeline_orchestrator.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("Chapter08_Pipeline")


def log(msg: str):
    logger.info(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('[%Y-%m-%d %H:%M:%S]')} {msg}\n")


def sanitize_segment_audio(wav: torch.Tensor, sr: int = 24000) -> torch.Tensor:
    """
    Sanitizes synthesized speech segment audio to eliminate acoustic artifacts,
    digital step clicks, and DC offset at chunk boundaries:
    1. Ensures 2D shape (1, N) and removes any DC offset.
    2. Detects and trims any leading non-speech burst/transient, preserving
       a clean natural pre-speech silence margin (~50-80ms).
    3. Applies smooth 15ms raised-cosine fade-in (starts at exact 0.000000).
    4. Applies smooth 15ms raised-cosine fade-out (ends at exact 0.000000).
    """
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)
    elif wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)

    # 1. Remove DC offset
    wav = wav - torch.mean(wav)

    # 2. Analyze leading frames for burst and silence valley
    y = wav[0].cpu().numpy()
    win = int(0.005 * sr)  # 5ms frames
    n_frames = min(int(1.5 / 0.005), len(y) // win)

    if n_frames > 20:
        energies = np.array([np.max(np.abs(y[i * win : (i + 1) * win])) for i in range(n_frames)])
        init_burst = np.max(energies[:10]) > 0.05

        if init_burst:
            valleys = [i for i in range(20, min(len(energies), int(1.2 / 0.005))) if energies[i] < 0.025]
            if valleys:
                first_valley = valleys[0]
                speech_onset = None
                for i in range(first_valley, len(energies) - 2):
                    if energies[i] > 0.06 and energies[i + 1] > 0.06:
                        speech_onset = i
                        break

                if speech_onset is not None:
                    clean_valley_idx = speech_onset - int(0.06 / 0.005)
                    clean_valley_idx = max(first_valley, min(clean_valley_idx, speech_onset - 4))
                    cut_sample = clean_valley_idx * win
                    wav = wav[:, cut_sample:]
            else:
                min_idx = 20 + np.argmin(energies[20:80])
                if energies[min_idx] < 0.05:
                    cut_sample = min_idx * win
                    wav = wav[:, cut_sample:]

    # 3. Apply smooth 15ms raised-cosine fade-in and fade-out
    fade_len = int(0.015 * sr)
    if wav.shape[1] > 2 * fade_len:
        t = torch.linspace(0, np.pi, fade_len, device=wav.device)
        fade_in = 0.5 * (1.0 - torch.cos(t))
        fade_out = 0.5 * (1.0 + torch.cos(t))
        wav[:, :fade_len] *= fade_in
        wav[:, -fade_len:] *= fade_out

    return wav


def copy_to_windows(src_path: Path, filename: str):
    targets = [
        Path(r"/run/media/nk/System/Users/kosty/Desktop") / filename,
        Path(r"/run/media/nk/Main/Downloads") / filename,
    ]
    for t in targets:
        try:
            if t.parent.is_dir():
                shutil.copy2(src_path, t)
                log(f"   ✓ Windows экспорт: {t}")
        except Exception as e:
            log(f"   [Предупреждение] Не удалось скопировать на {t}: {e}")


def verify_and_master():
    log("\n" + "=" * 65)
    log("=== ШАГ 2: ПЕРЕПРОВЕРКА НА АРТЕФАКТЫ И СТУДИЙНЫЙ МАСТЕРИНГ ===")
    log("=" * 65)

    segs_dir = CH_DIR / "segments"
    segs_json_path = CH_DIR / "segments.json"
    if not segs_dir.is_dir() or not segs_json_path.exists():
        raise RuntimeError("Не найдены сегменты для перепроверки!")

    with open(segs_json_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    sr = 24000
    silence_sentence = torch.zeros((1, int(sr * 0.20)))
    silence_para = torch.zeros((1, int(sr * 0.50)))
    silence_tail = torch.zeros((1, int(sr * 0.50)))

    audio_chunks = []
    max_first_sample = 0.0
    max_last_sample = 0.0
    re_sanitized = 0

    log(f"Проверка {len(segments)} сегментов на клики, скачки напряжения и артефакты...")
    for s_idx, seg in enumerate(segments):
        seg_file = segs_dir / f"seg_{s_idx:04d}.wav"
        if not seg_file.exists():
            raise FileNotFoundError(f"Отсутствует файл сегмента: {seg_file}")

        y, orig_sr = sf.read(str(seg_file))
        first_s = np.abs(y[0])
        last_s = np.abs(y[-1])

        # If boundary sample is not zero, re-sanitize
        if first_s > 1e-4 or last_s > 1e-4:
            t_wav = torch.from_numpy(y).unsqueeze(0).float()
            cleaned = sanitize_segment_audio(t_wav, sr)
            y = cleaned[0].numpy()
            sf.write(str(seg_file), y, sr, subtype="PCM_16")
            re_sanitized += 1

        c_first = np.abs(y[0])
        c_last = np.abs(y[-1])
        max_first_sample = max(max_first_sample, c_first)
        max_last_sample = max(max_last_sample, c_last)

        t_chunk = torch.from_numpy(y).unsqueeze(0).float()
        audio_chunks.append(t_chunk)

        if seg.get("is_last_in_p", False):
            audio_chunks.append(silence_para)
        else:
            audio_chunks.append(silence_sentence)

    audio_chunks.append(silence_tail)
    master_wav = torch.cat(audio_chunks, dim=1)

    # Save 24k master WAV
    master_wav_path = CH_DIR / "Глава_08.wav"
    alt_wav = CH_DIR / "Things_Only_Get_Stranger_Глава_08.wav"
    sf.write(str(master_wav_path), master_wav[0].numpy(), sr, subtype="PCM_16")
    shutil.copy2(master_wav_path, alt_wav)

    dur_s = master_wav.shape[1] / sr
    log(f"✓ Проверка артефактов пройдена: 100% сегментов проверено.")
    log(f"  Максимальный скачок границы (y[0]):   {max_first_sample:.8f} (норма: 0.00000000)")
    log(f"  Максимальный скачок окончания (y[-1]): {max_last_sample:.8f} (норма: 0.00000000)")
    log(f"  Повторно санитаризовано:             {re_sanitized} сегментов")
    log(f"  Длительность мастера:                {dur_s/60:.2f} мин ({dur_s:.1f} с)")

    # Studio Warmth Mastering (44.1 kHz, 192 kbps, EBU R128)
    log("\n▶ Применение студийного мастеринга Studio Warmth (44.1 kHz)...")
    af_warm = (
        "aresample=resampler=soxr:osr=44100,"
        "equalizer=f=200:t=q:w=1.2:g=2.2,"
        "equalizer=f=3200:t=q:w=1.2:g=-2.0,"
        "treble=g=3.0:f=6000,"
        "aecho=0.8:0.7:18:0.06,"
        "loudnorm=I=-16:TP=-1.0:LRA=9"
    )

    mp3_path = CH_DIR / "Глава_08.mp3"
    alt_mp3 = CH_DIR / "Things_Only_Get_Stranger_Глава_08.mp3"

    cmd_master = [
        "ffmpeg", "-y",
        "-i", str(master_wav_path),
        "-af", af_warm,
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-ar", "44100",
        "-metadata", "title=Дальше — только страннее (Глава 8)",
        "-metadata", "artist=hughescookingfan / Стив Харрингтон (CosyVoice 3)",
        "-metadata", "album=Дальше — только страннее",
        "-metadata", "genre=Audiobook",
        str(mp3_path)
    ]
    subprocess.run(cmd_master, check=True)
    shutil.copy2(mp3_path, alt_mp3)

    log(f"✓ Мастеринг завершён! Размер: {mp3_path.stat().st_size / 1024 / 1024:.2f} MB")

    # Windows export
    copy_to_windows(mp3_path, "Things_Only_Get_Stranger_Глава_08.mp3")
    copy_to_windows(mp3_path, "Глава_08.mp3")

    report = {
        "chapter": "Глава 08",
        "total_segments": len(segments),
        "re_sanitized": re_sanitized,
        "max_boundary_first_sample": float(max_first_sample),
        "max_boundary_last_sample": float(max_last_sample),
        "duration_minutes": round(dur_s / 60, 2),
        "duration_seconds": round(dur_s, 2),
        "master_wav": str(master_wav_path),
        "mastered_mp3": str(mp3_path),
        "mastering_chain": "Studio Warmth (44.1kHz, 192k, EBU R128)",
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(CH_DIR / "artifact_verification_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log("\n" + "=" * 65)
    log("=== ВСЯ ОБРАБОТКА И ПЕРЕПРОВЕРКА ГЛАВЫ 08 УСПЕШНО ЗАВЕРШЕНА! ===")
    log("=" * 65)


def main():
    CH_DIR.mkdir(parents=True, exist_ok=True)
    log("=" * 65)
    log("🚀 СТАРТ ГЕНЕРАЦИИ И ПЕРЕПРОВЕРКИ: «Дальше — только страннее. Глава 8»")
    log(f"Текст:  {TEXT_PATH}")
    log(f"Голос:  {VOICE_FILE.name}")
    log(f"Папка:  {CH_DIR}")
    log("=" * 65)

    if not TEXT_PATH.exists():
        log(f"ОШИБКА: Текст не найден: {TEXT_PATH}")
        sys.exit(1)
    if not VOICE_FILE.exists():
        log(f"ОШИБКА: Голос не найден: {VOICE_FILE}")
        sys.exit(1)

    t0 = time.time()

    # ШАГ 1: Запуск генерации CosyVoice 3
    cmd = [
        str(PYTHON_BIN),
        str(GENERATE_SCRIPT),
        "--text-path", str(TEXT_PATH),
        "--voice-file", str(VOICE_FILE),
        "--output-dir", str(CH_DIR),
        "--title", "Дальше — только страннее. Глава 8",
        "--whisper-verify",
        "--no-telegram"
    ]
    log(f"Запуск синтеза: {' '.join(cmd)}")
    ret = subprocess.run(cmd)

    if ret.returncode != 0:
        log(f"❌ Ошибка синтеза! Код возврата: {ret.returncode}")
        sys.exit(ret.returncode)

    elapsed_synth = time.time() - t0
    log(f"✓ Синтез CosyVoice 3 завершён за {elapsed_synth / 60:.1f} мин. ({elapsed_synth:.1f}с)")

    # ШАГ 2: Перепроверка на артефакты и мастеринг
    verify_and_master()

    total_time = time.time() - t0
    log(f"\nОбщее время конвейера Главы 8: {total_time / 60:.1f} мин. ({total_time:.1f}с)")


if __name__ == "__main__":
    main()
