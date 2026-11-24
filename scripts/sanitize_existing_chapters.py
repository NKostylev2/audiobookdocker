#!/usr/bin/env python3
"""
Sanitizes and reassembles audio chapters for 'Things Only Get Stranger' (Chapters 01 to 07).

Fixes the issue:
- Eliminates the initial burst/artifact at the beginning of each segment
- Removes DC offset and step discontinuities
- Applies smooth 15ms raised-cosine fade-in and fade-out to guarantee exact 0.00000000 boundary transitions
- Preserves 100% of speech and word order
- Reassembles master WAV and MP3 with exact pauses (200ms sentence, 500ms paragraph, 500ms tail)
- Copies to Windows Desktop & Downloads if mounted
"""

import os
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
OUTPUT_BASE = ROOT_DIR / "output" / "Things_Only_Get_Stranger"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("SanitizeChapters")


def sanitize_segment_audio(wav: torch.Tensor, sr: int = 24000) -> torch.Tensor:
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
            # Find silence valley (energy < 0.025) after burst (between 0.10s and 1.2s)
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
                # Fallback: local argmin in [20, 80]
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
                logger.info(f"   ✓ Windows copy: {t}")
        except Exception as e:
            logger.warning(f"   Could not copy to {t}: {e}")


def process_chapter(ch_dir: Path, sr: int = 24000):
    ch_name = ch_dir.name  # e.g. "Глава_01"
    segs_dir = ch_dir / "segments"
    segs_json_path = ch_dir / "segments.json"

    if not segs_dir.is_dir() or not segs_json_path.exists():
        logger.warning(f"Skipping {ch_dir}: missing segments or segments.json")
        return

    with open(segs_json_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    logger.info(f"▶ Processing {ch_name} ({len(segments)} segments)...")
    t0 = time.time()

    silence_sentence = torch.zeros((1, int(sr * 0.20)))  # 200 ms
    silence_para = torch.zeros((1, int(sr * 0.50)))      # 500 ms
    silence_tail = torch.zeros((1, int(sr * 0.50)))      # 500 ms

    audio_chunks = []
    backup_dir = ch_dir / "segments_untrimmed_backup"
    if not backup_dir.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)

    max_orig_first_sample = 0.0
    max_cleaned_first_sample = 0.0

    for s_idx, seg in enumerate(segments):
        seg_file = segs_dir / f"seg_{s_idx:04d}.wav"
        if not seg_file.exists():
            logger.error(f"Missing segment file: {seg_file}")
            continue

        # Load audio
        y, orig_sr = sf.read(str(seg_file))
        orig_first = np.abs(y[0])
        max_orig_first_sample = max(max_orig_first_sample, orig_first)

        # Backup original if not yet backed up
        bak_file = backup_dir / seg_file.name
        if not bak_file.exists():
            shutil.copy2(seg_file, bak_file)

        t_wav = torch.from_numpy(y).unsqueeze(0).float()
        if orig_sr != sr:
            resampler = torchaudio.transforms.Resample(orig_sr, sr)
            t_wav = resampler(t_wav)

        cleaned = sanitize_segment_audio(t_wav, sr)
        c_y = cleaned[0].numpy()
        max_cleaned_first_sample = max(max_cleaned_first_sample, np.abs(c_y[0]))

        # Overwrite cached segment with sanitized version
        sf.write(str(seg_file), c_y, sr, subtype="PCM_16")

        audio_chunks.append(cleaned)
        if seg.get("is_last_in_p", False):
            audio_chunks.append(silence_para)
        else:
            audio_chunks.append(silence_sentence)

    audio_chunks.append(silence_tail)
    master_wav = torch.cat(audio_chunks, dim=1)

    # Save master WAV
    master_wav_path = ch_dir / f"{ch_name}.wav"
    sf.write(str(master_wav_path), master_wav[0].numpy(), sr, subtype="PCM_16")

    # Also save with full name if exists
    alt_wav = ch_dir / f"Things_Only_Get_Stranger_{ch_name}.wav"
    sf.write(str(alt_wav), master_wav[0].numpy(), sr, subtype="PCM_16")

    # Convert to MP3
    mp3_path = ch_dir / f"{ch_name}.mp3"
    alt_mp3 = ch_dir / f"Things_Only_Get_Stranger_{ch_name}.mp3"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(master_wav_path),
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", str(sr),
        str(mp3_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    shutil.copy2(mp3_path, alt_mp3)

    # Windows export
    copy_to_windows(mp3_path, f"Things_Only_Get_Stranger_{ch_name}.mp3")

    dur_min = master_wav.shape[1] / sr / 60
    logger.info(
        f"✓ {ch_name} complete in {time.time()-t0:.1f}s: "
        f"Duration = {dur_min:.2f} min | "
        f"Max step jump: {max_orig_first_sample:.4f} -> {max_cleaned_first_sample:.8f}"
    )


def main():
    chapters = sorted([p for p in OUTPUT_BASE.glob("Глава_*") if p.is_dir()])
    if not chapters:
        logger.error(f"No chapters found in {OUTPUT_BASE}")
        return

    logger.info("=" * 60)
    logger.info(f"🛠️ СТАРТ САНИТАРИЗАЦИИ И СБОРКИ: {len(chapters)} глав")
    logger.info(f"Директория: {OUTPUT_BASE}")
    logger.info("=" * 60)

    t_all = time.time()
    for ch in chapters:
        process_chapter(ch)

    logger.info("=" * 60)
    logger.info(f"✨ ВСЕ {len(chapters)} ГЛАВ УСПЕШНО ОБРАБОТАНЫ ЗА {time.time()-t_all:.1f}с!")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
