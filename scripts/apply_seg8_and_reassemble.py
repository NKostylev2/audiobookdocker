#!/usr/bin/env python3
"""
Apply Seed 779 to Seg 0008 and reassemble Chapter 7.
"""
import os
import sys
import json
import logging
import subprocess
import shutil
from pathlib import Path
import torch
import torchaudio
import whisper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apply_seg8")

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
COSY_DIR = ROOT_DIR / "CosyVoice"
MATCHA_DIR = COSY_DIR / "third_party" / "Matcha-TTS"

for p in [str(ROOT_DIR), str(COSY_DIR), str(MATCHA_DIR)]:
    if p not in sys.path and os.path.exists(p):
        sys.path.insert(0, p)

from cosyvoice.cli.cosyvoice import AutoModel

OUT_DIR = ROOT_DIR / "output" / "Книга_2_Тайная_комната" / "Глава_07"
CACHE_DIR = OUT_DIR / "segments"
VOICE_FILE = ROOT_DIR / "voices" / "VID_20261101_122350_289.wav"
REF_TXT_PATH = ROOT_DIR / "voices" / "VID_20261101_122350_289.reference.txt"

def main():
    model_dir = str(COSY_DIR / "pretrained_models" / "Fun-CosyVoice3-0.5B")
    logger.info("Loading CosyVoice 3 model...")
    cosyvoice = AutoModel(model_dir=model_dir)
    
    ref_text = REF_TXT_PATH.read_text(encoding="utf-8").strip() if REF_TXT_PATH.exists() else ""
    prompt_text = f"You are a helpful assistant.<|endofprompt|>{ref_text}"
    
    phrase = "Каждый следующий слабый вдох давался ей всё труднее и звучал всё реже. Он отшвырнул одеяло."
    best_seed = 779
    logger.info(f"Synthesizing Seg 0008 with Seed {best_seed}...")
    torch.manual_seed(best_seed)
    torch.cuda.manual_seed_all(best_seed)
    outputs = list(cosyvoice.inference_zero_shot(
        tts_text=phrase,
        prompt_text=prompt_text,
        prompt_wav=str(VOICE_FILE),
        stream=False,
        speed=1.0,
        text_frontend=False
    ))
    speech = torch.concat([o['tts_speech'] for o in outputs], dim=1)
    seg8_wav = CACHE_DIR / "seg_0008.wav"
    torchaudio.save(str(seg8_wav), speech, cosyvoice.sample_rate)
    logger.info(f"Saved seg_0008.wav ({speech.shape[1]/cosyvoice.sample_rate:.2f}s)")
    
    # Whisper verification on CPU
    whisper_model = whisper.load_model("base", device="cpu")
    res = whisper_model.transcribe(str(seg8_wav), language="ru")
    logger.info(f"Whisper verified seg_0008: '{res['text'].strip()}'")
    
    # Reassemble Master Audio
    logger.info("=== Reassembling Master WAV & MP3 ===")
    with open(OUT_DIR / "segments.json", encoding="utf-8") as f:
        segments = json.load(f)
        
    silence_sentence = torch.zeros((1, int(cosyvoice.sample_rate * 0.20)))
    silence_para = torch.zeros((1, int(cosyvoice.sample_rate * 0.50)))
    silence_tail = torch.zeros((1, int(cosyvoice.sample_rate * 0.50)))
    
    audio_chunks = []
    for s_idx, seg in enumerate(segments):
        s_wav = CACHE_DIR / f"seg_{s_idx:04d}.wav"
        chunk, sr = torchaudio.load(str(s_wav))
        if sr != cosyvoice.sample_rate:
            chunk = torchaudio.transforms.Resample(sr, cosyvoice.sample_rate)(chunk)
        if chunk.dim() == 1:
            chunk = chunk.unsqueeze(0)
        elif chunk.shape[0] > 1:
            chunk = chunk.mean(dim=0, keepdim=True)
            
        audio_chunks.append(chunk)
        if seg.get("is_last_in_p", False):
            audio_chunks.append(silence_para)
        else:
            audio_chunks.append(silence_sentence)
            
    audio_chunks.append(silence_tail)
    master_wav_tensor = torch.cat(audio_chunks, dim=1)
    
    master_wav_path = OUT_DIR / "Книга_2_Тайная_комната_Глава_07.wav"
    torchaudio.save(str(master_wav_path), master_wav_tensor, cosyvoice.sample_rate)
    dur_min = master_wav_tensor.shape[1] / cosyvoice.sample_rate / 60
    logger.info(f"Saved master WAV: {master_wav_path} ({dur_min:.2f} min)")
    
    master_mp3_path = OUT_DIR / "Книга_2_Тайная_комната_Глава_07.mp3"
    ffmpeg = shutil.which("ffmpeg")
    cmd = [
        ffmpeg, "-y",
        "-i", str(master_wav_path),
        "-codec:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", "24000",
        str(master_mp3_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    logger.info(f"Saved master MP3: {master_mp3_path} ({master_mp3_path.stat().st_size / 1024 / 1024:.2f} MB)")
    
    telegram_mp3 = OUT_DIR / "Книга_2_Тайная_комната_Глава_07_telegram.mp3"
    cmd_tg = [
        ffmpeg, "-y",
        "-i", str(master_wav_path),
        "-codec:a", "libmp3lame",
        "-b:a", "64k",
        "-ar", "24000",
        str(telegram_mp3)
    ]
    subprocess.run(cmd_tg, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    logger.info(f"Saved telegram MP3: {telegram_mp3} ({telegram_mp3.stat().st_size / 1024 / 1024:.2f} MB)")
    
    logger.info("Windows delivery SKIPPED (permanently disabled).")
    logger.info("=== FINISHED ===")

if __name__ == "__main__":
    main()
