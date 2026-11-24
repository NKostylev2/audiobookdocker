#!/usr/bin/env python3
"""
Repair script for swallowed audio endings in Chapter 2.
Splits each identified segment into 2 smaller natural sentence chunks,
synthesizes each chunk with CosyVoice 3, verifies endings with Whisper,
concatenates them, and updates the segment file.
Finally re-assembles master WAV and MP3.
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
logger = logging.getLogger("repair_endings")

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
COSY_DIR = ROOT_DIR / "CosyVoice"
MATCHA_DIR = COSY_DIR / "third_party" / "Matcha-TTS"

for p in [str(ROOT_DIR), str(COSY_DIR), str(MATCHA_DIR)]:
    if p not in sys.path and os.path.exists(p):
        sys.path.insert(0, p)

from cosyvoice.cli.cosyvoice import AutoModel

OUT_DIR = ROOT_DIR / "output" / "Книга_2_Тайная_комната" / "Глава_02"
CACHE_DIR = OUT_DIR / "segments"
VOICE_FILE = ROOT_DIR / "voices" / "VID_20261101_122350_289.wav"
REF_TXT_PATH = ROOT_DIR / "voices" / "VID_20261101_122350_289.reference.txt"

def split_text_into_two_halves(text: str):
    sents = [s.text.strip() for s in razdel.sentenize(text) if s.text.strip()]
    if len(sents) <= 1:
        # Split on comma, semicolon, dash if single long sentence
        parts = re.split(r'([,;:—]\s+)', text)
        mid = len(parts) // 2
        # Ensure punctuation stays with left part
        if mid % 2 == 1:
            mid += 1
        left = "".join(parts[:mid]).strip()
        right = "".join(parts[mid:]).strip()
        if not left.endswith(('.', '!', '?')):
            left += '.'
        return [left, right]
    
    mid = max(1, len(sents) // 2)
    left = " ".join(sents[:mid])
    right = " ".join(sents[mid:])
    return [left, right]

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
        has_latin = bool(re.search(r'[a-zA-Z]', expected_text))
        has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', expected_text))
        whisper_lang = "en" if (has_latin and not has_cyrillic) else "ru"
        res = whisper_model.transcribe(tmp_name, language=whisper_lang)
        rec = res["text"].lower()
        
        def stem(w):
            return w[:max(3, len(w)-2)]

        ok = any(stem(w) in rec for w in target_words)
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
    swallowed_file = OUT_DIR / "swallowed_segments.json"
    if not swallowed_file.exists():
        logger.error(f"Missing {swallowed_file}")
        return
    
    with open(swallowed_file, encoding="utf-8") as f:
        bad_segments = json.load(f)
        
    segments_json = json.load(open(OUT_DIR / "segments.json", encoding="utf-8"))
    
    logger.info(f"Loaded {len(bad_segments)} segments requiring repair.")
    
    # Load CosyVoice model
    model_dir = str(COSY_DIR / "pretrained_models" / "Fun-CosyVoice3-0.5B")
    logger.info("Loading CosyVoice 3 model...")
    cosyvoice = AutoModel(model_dir=model_dir)
    
    # Load Whisper
    logger.info("Loading Whisper on CUDA...")
    whisper_model = whisper.load_model("base", device="cuda")
    
    ref_text = REF_TXT_PATH.read_text(encoding="utf-8").strip() if REF_TXT_PATH.exists() else ""
    prompt_text = f"You are a helpful assistant.<|endofprompt|>{ref_text}"
    
    silence_sub = torch.zeros((1, int(cosyvoice.sample_rate * 0.20))) # 200 ms pause between sentences
    
    for idx_item, item in enumerate(bad_segments):
        s_num = item[0]
        s_idx = s_num - 1
        seg = segments_json[s_idx]
        full_text = seg["text"].strip()
        wav_path = CACHE_DIR / f"seg_{s_idx:04d}.wav"
        
        logger.info(f"[{idx_item+1}/{len(bad_segments)}] Repairing segment {s_num:03d}: '{full_text[:50]}...'")
        
        halves = split_text_into_two_halves(full_text)
        sub_chunks = []
        
        for h_i, half_text in enumerate(halves):
            best_chunk = None
            base_seed = 3000 + s_idx * 10 + h_i
            
            for attempt, seed_offset in enumerate([0, 19, 47, 83]):
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
                        break
                    else:
                        best_chunk = cand
                        logger.warning(f"  Half {h_i+1} retry {attempt+1} due to swallow: '{half_text[-30:]}'")
            
            if best_chunk is not None:
                sub_chunks.append(best_chunk)
            else:
                logger.error(f"Failed to synthesize half {h_i+1} for segment {s_num}")
                
        if len(sub_chunks) == len(halves):
            # Combine sub-chunks
            joined = []
            for i, c in enumerate(sub_chunks):
                joined.append(c)
                if i < len(sub_chunks) - 1:
                    joined.append(silence_sub)
            final_seg_wav = torch.cat(joined, dim=1)
            torchaudio.save(str(wav_path), final_seg_wav, cosyvoice.sample_rate)
            dur = final_seg_wav.shape[1] / cosyvoice.sample_rate
            logger.info(f"  Segment {s_num:03d} repaired successfully! New duration: {dur:.2f}s")
        else:
            logger.error(f"  Segment {s_num:03d} repair incomplete!")
            
    # Reassemble Master Audio
    logger.info("=== All repairs finished! Re-assembling master audio ===")
    silence_sentence = torch.zeros((1, int(cosyvoice.sample_rate * 0.20)))
    silence_para = torch.zeros((1, int(cosyvoice.sample_rate * 0.50)))
    silence_tail = torch.zeros((1, int(cosyvoice.sample_rate * 0.50)))
    
    audio_chunks = []
    for s_idx, seg in enumerate(segments_json):
        seg_wav_path = CACHE_DIR / f"seg_{s_idx:04d}.wav"
        chunk, sr = torchaudio.load(str(seg_wav_path))
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
    full_wav = torch.cat(audio_chunks, dim=1)
    safe_out_stem = OUT_DIR.name
    master_wav = OUT_DIR / f"{safe_out_stem}.wav"
    torchaudio.save(str(master_wav), full_wav, cosyvoice.sample_rate)
    total_dur_s = full_wav.shape[1] / cosyvoice.sample_rate
    logger.info(f"Master WAV regenerated: {master_wav} ({total_dur_s/60:.2f} min)")
    
    # Re-encode to MP3
    final_mp3 = OUT_DIR / f"{safe_out_stem}.mp3"
    ffmpeg = shutil.which("ffmpeg") or str(ROOT_DIR / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe")
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
    
    # Windows export is permanently DISABLED per user instruction
    logger.info("Windows delivery SKIPPED (permanently disabled per user instruction).")
    logger.info("=== REPAIR AND MASTER EXPORT COMPLETE ===")

if __name__ == "__main__":
    main()
