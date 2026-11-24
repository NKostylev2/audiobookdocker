#!/usr/bin/env python3
"""
Targeted repair script for Chapter 7 segments 8 and 427.
"""
import os
import sys
import json
import logging
from pathlib import Path
import re
import torch
import torchaudio
import whisper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("repair_ch7")

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

def verify_text(audio_chunk: torch.Tensor, sr: int, expected_text: str, whisper_model):
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_name = tmp.name
    try:
        torchaudio.save(tmp_name, audio_chunk, sr)
        res = whisper_model.transcribe(tmp_name, language="ru")
        rec = res["text"].strip()
        return rec
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except Exception:
                pass

def synthesize_best(text, cosyvoice, prompt_text, whisper_model, target_keywords, seeds=[42, 17, 222, 2024, 777, 999, 1234, 5555]):
    best_cand = None
    best_score = -1
    best_rec = ""
    
    for seed in seeds:
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        outputs = list(cosyvoice.inference_zero_shot(
            tts_text=text,
            prompt_text=prompt_text,
            prompt_wav=str(VOICE_FILE),
            stream=False,
            speed=1.0,
            text_frontend=False
        ))
        if not outputs or outputs[0]['tts_speech'].shape[1] == 0:
            continue
        cand = torch.concat([o['tts_speech'] for o in outputs], dim=1)
        rec = verify_text(cand, cosyvoice.sample_rate, text, whisper_model)
        rec_lower = rec.lower()
        
        matches = sum(1 for kw in target_keywords if kw.lower() in rec_lower)
        logger.info(f"Seed {seed:5d} -> matches {matches}/{len(target_keywords)} | Whisper: '{rec}'")
        if matches == len(target_keywords):
            return cand, rec
        if matches > best_score:
            best_score = matches
            best_cand = cand
            best_rec = rec
            
    logger.warning(f"Returning best candidate with {best_score}/{len(target_keywords)} matches: '{best_rec}'")
    return best_cand, best_rec

def repair_seg_8(cosyvoice, prompt_text, whisper_model):
    logger.info("=== Repairing Segment 8 ===")
    # Expected: "каждый следующий слабый вдох давался ей всё труднее и звучал всё реже. Он отшвырнул одеяло."
    part1_text = "Каждый следующий слабый вдох давался ей всё труднее и звучал всё реже."
    part2_text = "Он отшвырнул одеяло."
    
    cand1, rec1 = synthesize_best(part1_text, cosyvoice, prompt_text, whisper_model, ["следующий", "давался", "труднее", "реже"], seeds=[42, 2024, 77, 314, 500])
    cand2, rec2 = synthesize_best(part2_text, cosyvoice, prompt_text, whisper_model, ["отшвырнул", "одеяло"], seeds=[42, 17, 222, 2024, 77, 101, 555])
    
    silence_200ms = torch.zeros((1, int(cosyvoice.sample_rate * 0.20)))
    combined = torch.cat([cand1, silence_200ms, cand2], dim=1)
    
    rec_full = verify_text(combined, cosyvoice.sample_rate, part1_text + " " + part2_text, whisper_model)
    logger.info(f"Segment 8 combined verification: '{rec_full}'")
    
    out_path = CACHE_DIR / "seg_0008.wav"
    torchaudio.save(str(out_path), combined, cosyvoice.sample_rate)
    logger.info(f"Saved repaired seg_0008.wav ({combined.shape[1]/cosyvoice.sample_rate:.2f}s)")
    return out_path

def repair_seg_427(cosyvoice, prompt_text, whisper_model):
    logger.info("=== Repairing Segment 427 ===")
    # Expected: "— Мистер Малфой. — Ваш дядя получил условия, на которых вы вернётесь к нему. — И как он отреагировал? — Плохо. Гарри невольно улыбнулся."
    # Let's split into 4 natural dialogue lines
    parts = [
        ("Мистер Малфой.", ["малфой"]),
        ("Ваш дядя получил условия, на которых вы вернётесь к нему.", ["дядя", "условия", "вернётесь"]),
        ("И как он отреагировал?", ["отреагировал"]),
        ("Плохо. Гарри невольно улыбнулся.", ["плохо", "гарри", "невольно", "улыбнулся"])
    ]
    
    cands = []
    silence_200ms = torch.zeros((1, int(cosyvoice.sample_rate * 0.20)))
    for p_text, keywords in parts:
        logger.info(f"Synthesizing part: '{p_text}'")
        cand, rec = synthesize_best(p_text, cosyvoice, prompt_text, whisper_model, keywords, seeds=[42, 2024, 17, 77, 222, 500, 777, 999])
        cands.append(cand)
        
    combined_parts = []
    for i, c in enumerate(cands):
        combined_parts.append(c)
        if i < len(cands) - 1:
            combined_parts.append(silence_200ms)
    combined = torch.cat(combined_parts, dim=1)
    
    rec_full = verify_text(combined, cosyvoice.sample_rate, " ".join(p[0] for p in parts), whisper_model)
    logger.info(f"Segment 427 combined verification: '{rec_full}'")
    
    out_path = CACHE_DIR / "seg_0427.wav"
    torchaudio.save(str(out_path), combined, cosyvoice.sample_rate)
    logger.info(f"Saved repaired seg_0427.wav ({combined.shape[1]/cosyvoice.sample_rate:.2f}s)")
    return out_path

def reassemble_master(cosyvoice):
    logger.info("=== Reassembling Master WAV & MP3 ===")
    with open(OUT_DIR / "segments.json", encoding="utf-8") as f:
        segments = json.load(f)
        
    silence_sentence = torch.zeros((1, int(cosyvoice.sample_rate * 0.20)))
    silence_para = torch.zeros((1, int(cosyvoice.sample_rate * 0.50)))
    silence_tail = torch.zeros((1, int(cosyvoice.sample_rate * 0.50)))
    
    audio_chunks = []
    for s_idx, seg in enumerate(segments):
        seg_wav = CACHE_DIR / f"seg_{s_idx:04d}.wav"
        chunk, sr = torchaudio.load(str(seg_wav))
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
    import subprocess, shutil
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
    
    # Telegram 64k mp3 if needed
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
    logger.info("Windows delivery SKIPPED (per user command: disabled permanently).")

def main():
    model_dir = str(COSY_DIR / "pretrained_models" / "Fun-CosyVoice3-0.5B")
    logger.info("Loading CosyVoice 3 model...")
    cosyvoice = AutoModel(model_dir=model_dir)
    
    logger.info("Loading Whisper base on CPU...")
    whisper_model = whisper.load_model("base", device="cpu")
    
    ref_text = REF_TXT_PATH.read_text(encoding="utf-8").strip() if REF_TXT_PATH.exists() else ""
    prompt_text = f"You are a helpful assistant.<|endofprompt|>{ref_text}"
    
    repair_seg_8(cosyvoice, prompt_text, whisper_model)
    repair_seg_427(cosyvoice, prompt_text, whisper_model)
    reassemble_master(cosyvoice)
    logger.info("=== REPAIR COMPLETE ===")

if __name__ == "__main__":
    main()
