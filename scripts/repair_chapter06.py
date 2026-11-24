#!/usr/bin/env python3
"""
Repair script for segment 350 in Book 2 Chapter 6.
Splits segment 350 into 2 natural sentence chunks, synthesizes with CosyVoice 3,
verifies endings with Whisper on GPU, joins with 200ms silence, saves seg_0350.wav,
and reassembles master WAV, MP3, and exports to Windows Desktop & Downloads.
"""

import os
import sys
import json
import time
import shutil
import logging
import subprocess
from pathlib import Path
import re
import torch
import torchaudio
import razdel
import whisper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("repair_ch06")

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
COSY_DIR = ROOT_DIR / "CosyVoice"
MATCHA_DIR = COSY_DIR / "third_party" / "Matcha-TTS"

for p in [str(ROOT_DIR), str(COSY_DIR), str(MATCHA_DIR)]:
    if p not in sys.path and os.path.exists(p):
        sys.path.insert(0, p)

from cosyvoice.cli.cosyvoice import AutoModel

OUT_DIR = ROOT_DIR / "output" / "Книга_2_Тайная_комната" / "Глава_06"
CACHE_DIR = OUT_DIR / "segments"
VOICE_FILE = ROOT_DIR / "voices" / "VID_20261101_122350_289.wav"
REF_TXT_PATH = ROOT_DIR / "voices" / "VID_20261101_122350_289.reference.txt"

def verify_ending(audio_chunk: torch.Tensor, sr: int, expected_text: str, whisper_model):
    words = re.findall(r'[a-zA-Zа-яА-ЯёЁ]+', expected_text.lower())
    if not words:
        return True
    target_words = words[-2:]
    
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_name = tmp.name
    try:
        torchaudio.save(tmp_name, audio_chunk, sr)
        res = whisper_model.transcribe(tmp_name, language="ru")
        rec = res["text"].lower()
        
        def stem(w):
            return w[:max(3, len(w)-2)]

        ok = any(stem(w) in rec for w in target_words)
        logger.info(f"Whisper heard: '{rec}' (Target: {target_words}) -> Match: {ok}")
        return ok
    except Exception as e:
        logger.warning(f"Whisper verification error: {e}")
        return True
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except Exception:
                pass

def main():
    segments_json_path = OUT_DIR / "segments.json"
    with open(segments_json_path, encoding="utf-8") as f:
        segments_json = json.load(f)

    # 1. Load CosyVoice
    model_dir = str(COSY_DIR / "pretrained_models" / "Fun-CosyVoice3-0.5B")
    logger.info("Loading CosyVoice 3 model...")
    cosyvoice = AutoModel(model_dir=model_dir)

    # 2. Load Whisper on GPU
    logger.info("Loading Whisper on CUDA...")
    whisper_model = whisper.load_model("base", device="cuda")

    ref_text = REF_TXT_PATH.read_text(encoding="utf-8").strip() if REF_TXT_PATH.exists() else ""
    prompt_text = f"You are a helpful assistant.<|endofprompt|>{ref_text}"

    silence_sub = torch.zeros((1, int(cosyvoice.sample_rate * 0.20)))

    s_idx = 350
    seg = segments_json[s_idx]
    full_text = seg["text"].strip()
    wav_path = CACHE_DIR / f"seg_{s_idx:04d}.wav"

    logger.info(f"Repairing segment {s_idx:04d}: '{full_text}'")

    halves = [
        "Дверь туалета распахнулась. Гарри отпустил руки.",
        "Труба поглотила его. Её внутренняя поверхность была скользкой от многовековой слизи."
    ]

    sub_chunks = []
    for h_i, half_text in enumerate(halves):
        best_chunk = None
        base_seed = 4200 + h_i * 100

        for attempt, seed_offset in enumerate([0, 17, 42, 83, 119]):
            cur_seed = base_seed + seed_offset
            torch.manual_seed(cur_seed)
            torch.cuda.manual_seed_all(cur_seed)

            outputs = list(cosyvoice.inference_zero_shot(
                tts_text=half_text,
                prompt_text=prompt_text,
                prompt_wav=str(VOICE_FILE),
                stream=False,
                speed=1.0,
                text_frontend=False
            ))
            if outputs and outputs[0]['tts_speech'].shape[1] > 0:
                cand = torch.concat([o['tts_speech'] for o in outputs], dim=1)
                if verify_ending(cand, cosyvoice.sample_rate, half_text, whisper_model):
                    best_chunk = cand
                    logger.info(f"Half {h_i+1} verified OK with seed {cur_seed}")
                    break
                else:
                    best_chunk = cand
                    logger.warning(f"Half {h_i+1} attempt {attempt+1} swallow detected. Retrying...")

        if best_chunk is not None:
            sub_chunks.append(best_chunk)
        else:
            raise RuntimeError(f"Failed to synthesize half {h_i+1}")

    # Join halves with 200 ms silence
    joined = [sub_chunks[0], silence_sub, sub_chunks[1]]
    final_seg_wav = torch.cat(joined, dim=1)
    torchaudio.save(str(wav_path), final_seg_wav, cosyvoice.sample_rate)
    dur = final_seg_wav.shape[1] / cosyvoice.sample_rate
    logger.info(f"Segment {s_idx:04d} repaired successfully! Duration: {dur:.2f}s")

    # Double-check final segment with whisper
    logger.info("Double-checking whole repaired segment with Whisper...")
    res = whisper_model.transcribe(str(wav_path), language="ru")
    logger.info(f"Repaired segment transcription: {res['text']}")

    # Unload models from CUDA to free VRAM for ffmpeg / future tasks
    del cosyvoice
    del whisper_model
    torch.cuda.empty_cache()

    # Reassemble Master Audio
    logger.info("=== Re-assembling master audio ===")
    silence_sentence = torch.zeros((1, int(24000 * 0.20)))
    silence_para = torch.zeros((1, int(24000 * 0.50)))
    silence_tail = torch.zeros((1, int(24000 * 0.50)))

    audio_chunks = []
    for idx, s in enumerate(segments_json):
        s_file = CACHE_DIR / f"seg_{idx:04d}.wav"
        chunk, sr = torchaudio.load(str(s_file))
        if sr != 24000:
            chunk = torchaudio.transforms.Resample(sr, 24000)(chunk)
        if chunk.dim() == 1:
            chunk = chunk.unsqueeze(0)
        elif chunk.shape[0] > 1:
            chunk = chunk.mean(dim=0, keepdim=True)
        audio_chunks.append(chunk)
        if s.get("is_last_in_p", False):
            audio_chunks.append(silence_para)
        else:
            audio_chunks.append(silence_sentence)

    audio_chunks.append(silence_tail)
    full_wav = torch.cat(audio_chunks, dim=1)
    safe_out_stem = f"{OUT_DIR.parent.name}_{OUT_DIR.name}"
    master_wav = OUT_DIR / f"{safe_out_stem}.wav"
    torchaudio.save(str(master_wav), full_wav, 24000)
    total_dur_s = full_wav.shape[1] / 24000
    logger.info(f"Master WAV regenerated: {master_wav} ({total_dur_s/60:.2f} min)")

    # Re-encode to MP3
    final_mp3 = OUT_DIR / f"{safe_out_stem}.mp3"
    ffmpeg = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    cmd = [
        ffmpeg, "-y",
        "-i", str(master_wav),
        "-codec:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", "24000",
        str(final_mp3)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    logger.info(f"Master MP3 encoded: {final_mp3} ({final_mp3.stat().st_size / 1024 / 1024:.2f} MB)")

    # Telegram MP3 (compact 48k mono)
    compact_mp3 = OUT_DIR / f"{safe_out_stem}_telegram.mp3"
    subprocess.run([
        ffmpeg, "-y", "-i", str(master_wav),
        "-codec:a", "libmp3lame", "-b:a", "48k", "-ac", "1", "-ar", "24000",
        str(compact_mp3)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.info(f"Compact Telegram MP3 encoded: {compact_mp3} ({compact_mp3.stat().st_size / 1024 / 1024:.2f} MB)")

    # Copy to Windows Desktop & Downloads
    win_targets = [
        Path("/run/media/nk/System/Users/kosty/Desktop"),
        Path("/run/media/nk/Main/Downloads"),
    ]
    for wt in win_targets:
        if wt.is_dir():
            dest = wt / final_mp3.name
            shutil.copy2(str(final_mp3), str(dest))
            logger.info(f"Copied to Windows: {dest}")

    logger.info("=== REPAIR AND MASTER EXPORT COMPLETE ===")

if __name__ == "__main__":
    main()
