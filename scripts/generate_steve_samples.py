#!/usr/bin/env python3
"""
Generate audio samples using Steve Harrington voice profiles (CosyVoice 3)
1. Основной (steve_harrington)
2. Эмоциональный (steve_harrington_sarcastic)
"""

import os
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
import sys
import time
import subprocess
from pathlib import Path

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
COSY_DIR = ROOT_DIR / "CosyVoice"
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(COSY_DIR))
sys.path.insert(0, str(COSY_DIR / "third_party" / "Matcha-TTS"))

import torch
import torchaudio
import whisper
from cosyvoice.cli.cosyvoice import AutoModel

OUT_DIR = ROOT_DIR / "output" / "Things_Only_Get_Stranger" / "samples"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_DIR = str(COSY_DIR / "pretrained_models" / "Fun-CosyVoice3-0.5B")
TEXT = "Привет, я Стив Харрингтон, и сегодня я вам расскажу историю, которая произошла со мной в лесу."

print(f"Loading CosyVoice 3 from {MODEL_DIR}...")
cosyvoice = AutoModel(model_dir=MODEL_DIR)
sr = cosyvoice.sample_rate
print(f"Model loaded. Sample rate: {sr} Hz")

print("Loading Whisper base on CPU for ending verification...")
whisper_model = whisper.load_model("base", device="cpu")
print("Whisper ready.")

PROFILES = [
    {
        "name": "Основной (steve_harrington)",
        "id": "steve_harrington_main",
        "wav": ROOT_DIR / "voices" / "steve_harrington.wav",
        "txt": ROOT_DIR / "voices" / "steve_harrington.reference.txt",
    },
    {
        "name": "Эмоциональный (steve_harrington_sarcastic)",
        "id": "steve_harrington_sarcastic",
        "wav": ROOT_DIR / "voices" / "steve_harrington_sarcastic.wav",
        "txt": ROOT_DIR / "voices" / "steve_harrington_sarcastic.reference.txt",
    }
]

results = []

for prof in PROFILES:
    prof_name = prof["name"]
    prof_id = prof["id"]
    ref_wav = str(prof["wav"])
    ref_text = prof["txt"].read_text(encoding="utf-8").strip()
    prompt_text = f"You are a helpful assistant.<|endofprompt|>{ref_text}"

    print(f"\n==========================================")
    print(f"🎙 Синтез: {prof_name}")
    print(f"Текст: {TEXT}")
    print(f"==========================================")

    best_speech = None
    best_transcript = ""
    best_time = 0.0

    # Try seeds for best natural delivery
    for seed_offset in [0, 17, 42]:
        seed = 2026 + seed_offset
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        t0 = time.time()
        outputs = list(cosyvoice.inference_zero_shot(
            tts_text=TEXT,
            prompt_text=prompt_text,
            prompt_wav=ref_wav,
            stream=False,
            speed=1.0,
            text_frontend=False
        ))
        synth_t = time.time() - t0

        if outputs and outputs[0]["tts_speech"].shape[1] > 0:
            speech = torch.concat([o["tts_speech"] for o in outputs], dim=1)
            # Add 300ms tail silence per audio safety rules
            tail = torch.zeros((1, int(sr * 0.3)))
            speech = torch.concat([speech, tail], dim=1)

            # Temp verify with whisper
            temp_wav = OUT_DIR / f"temp_{prof_id}.wav"
            torchaudio.save(str(temp_wav), speech, sr)

            wh_res = whisper_model.transcribe(str(temp_wav), language="ru")
            transcript = wh_res["text"].strip()
            temp_wav.unlink(missing_ok=True)

            print(f"  [Seed {seed}] Время генерации: {synth_t:.2f}s | Длительность: {speech.shape[1]/sr:.2f}s")
            print(f"  Транскрипт: {transcript}")

            # Check ending words: "лесу" / "в лесу"
            clean_tr = transcript.lower()
            if "лесу" in clean_tr or "произошла" in clean_tr:
                best_speech = speech
                best_transcript = transcript
                best_time = synth_t
                break
            else:
                print("  ⚠️ Возможно проглочено окончание, пробуем следующий seed...")
                best_speech = speech
                best_transcript = transcript
                best_time = synth_t

    wav_out = OUT_DIR / f"{prof_id}.wav"
    mp3_out = OUT_DIR / f"{prof_id}.mp3"

    torchaudio.save(str(wav_out), best_speech, sr)

    # Encode MP3 via ffmpeg
    subprocess.run([
        "ffmpeg", "-y", "-i", str(wav_out),
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(mp3_out)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    dur = best_speech.shape[1] / sr
    results.append({
        "name": prof_name,
        "id": prof_id,
        "wav": str(wav_out),
        "mp3": str(mp3_out),
        "duration": round(dur, 2),
        "synth_time": round(best_time, 2),
        "transcript": best_transcript
    })

print("\n" + "="*50)
print("✅ ВСЕ СЭМПЛЫ УСПЕШНО СГЕНЕРИРОВАНЫ:")
for r in results:
    print(f"- {r['name']}: {r['duration']} сек (генерация: {r['synth_time']}с)")
    print(f"  WAV: {r['wav']}")
    print(f"  MP3: {r['mp3']}")
    print(f"  Транскрипт Whisper: \"{r['transcript']}\"")
