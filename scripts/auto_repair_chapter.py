#!/usr/bin/env python3
"""
Autonomous Self-Healing Repair Script for Flagged Chapter Segments.
Reads gpu_verification_report.json, repairs any swallowed or degraded segments,
re-assembles master WAV & MP3, and prepares for re-verification.
"""

import os
import sys
import json
import logging
import subprocess
import shutil
from pathlib import Path
import re
import numpy as np
import torch
import torchaudio
import razdel
import whisper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AutoRepair")

ROOT_DIR = Path(os.environ.get("AUDIOBOOK_ROOT_DIR", "/mnt/ai/AI-Dubbing"))
COSY_DIR = ROOT_DIR / "CosyVoice"
MATCHA_DIR = COSY_DIR / "third_party" / "Matcha-TTS"

for p in [str(ROOT_DIR), str(COSY_DIR), str(MATCHA_DIR)]:
    if p not in sys.path and os.path.exists(p):
        sys.path.insert(0, p)

from cosyvoice.cli.cosyvoice import AutoModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # fanfic_pipeline lives next to scripts/
from fanfic_pipeline.exit_codes import EXIT_REPAIR_INCOMPLETE
from fanfic_pipeline.runtime_guards import ThreadExceptionGuard

THREAD_GUARD = ThreadExceptionGuard()   # CosyVoice swallows exceptions of its LLM thread

VOICE_FILE = ROOT_DIR / "voices" / "VID_20261101_122350_289.wav"
REF_TXT_PATH = ROOT_DIR / "voices" / "VID_20261101_122350_289.reference.txt"


def clean_words(text: str):
    return [w.lower().replace('ё', 'е') for w in re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]+', text)]


def stem(w: str) -> str:
    w = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9]', '', w.lower().replace('ё', 'е'))
    if len(w) <= 3:
        return w
    return w[:max(3, len(w)-2)]


def sanitize_segment_audio(wav: torch.Tensor, sr: int = 24000) -> torch.Tensor:
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)
    elif wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)

    wav = wav - torch.mean(wav)

    y = wav[0].cpu().numpy()
    win = int(0.005 * sr)
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
                if speech_onset:
                    cut_frame = max(0, speech_onset - int(0.05 / 0.005))
                    cut_sample = cut_frame * win
                    wav = wav[:, cut_sample:]

    fade_len = int(0.015 * sr)
    if wav.shape[1] > 2 * fade_len:
        t = torch.linspace(0, np.pi, fade_len, device=wav.device)
        fade_in = 0.5 * (1.0 - torch.cos(t))
        fade_out = 0.5 * (1.0 + torch.cos(t))
        wav[:, :fade_len] *= fade_in
        wav[:, -fade_len:] *= fade_out

    return wav


def split_text_into_smart_parts(text: str) -> list:
    """Splits dialogue turns or multiple sentences into balanced natural chunks."""
    sents = [s.text.strip() for s in razdel.sentenize(text) if s.text.strip()]
    if len(sents) <= 1:
        # Check dialogue hyphens
        parts = [p.strip() for p in re.split(r'(?:^|\s+)—\s+', text) if p.strip()]
        if len(parts) > 1:
            return [("— " if not p.startswith("—") else "") + p for p in parts]
        return [text]

    # If multiple sentences, split if too long
    res = []
    for s in sents:
        if len(clean_words(s)) > 15:
            # Further split long sentence on punctuation
            sub = [p.strip() for p in re.split(r'([,;:—]\s+)', s) if p.strip()]
            if len(sub) > 2:
                mid = len(sub) // 2
                if mid % 2 == 1:
                    mid += 1
                res.append("".join(sub[:mid]).strip())
                res.append("".join(sub[mid:]).strip())
            else:
                res.append(s)
        else:
            res.append(s)
    return res if res else [text]


def verify_text_cpu(audio_chunk: torch.Tensor, sr: int, whisper_model, expected_text: str = "") -> str:
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_name = tmp.name
    try:
        torchaudio.save(tmp_name, audio_chunk, sr)
        has_latin = bool(re.search(r'[a-zA-Z]', expected_text))
        has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', expected_text))
        whisper_lang = "en" if (has_latin and not has_cyrillic) else ("ru" if has_cyrillic else None)
        res = whisper_model.transcribe(tmp_name, language=whisper_lang)
        return res.get("text", "").strip()
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except Exception:
                pass


def synthesize_candidate(text: str, cosyvoice, prompt_text, whisper_model, seeds=None):
    if seeds is None:
        seeds = [42, 779, 17, 77, 222, 500, 777, 999, 1234, 2024]

    target_words = clean_words(text)
    target_ending = target_words[-2:] if len(target_words) >= 2 else target_words[-1:]
    target_stems = [stem(w) for w in target_ending]

    best_cand = None
    best_score = -1

    for seed in seeds:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

        try:
            THREAD_GUARD.clear()
            outputs = list(cosyvoice.inference_zero_shot(
                tts_text=text,
                prompt_text=prompt_text,
                prompt_wav=str(VOICE_FILE),
                stream=False,
                speed=1.0,
                text_frontend=False
            ))
            THREAD_GUARD.raise_if_failed()
        except Exception as e:   # previously one failed seed aborted the whole repair run
            logger.warning(f"    Seed {seed:5d} -> inference failed: {type(e).__name__}: {e}")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            continue
        if not outputs or outputs[0]['tts_speech'].shape[1] == 0:
            continue

        cand = torch.concat([o['tts_speech'] for o in outputs], dim=1)
        rec = verify_text_cpu(cand, cosyvoice.sample_rate, whisper_model)
        rec_words = clean_words(rec)
        rec_stems = [stem(w) for w in rec_words]

        # Check ending presence
        ending_ok = any(st in rec_stems for st in target_stems) or any(st in rec.lower() for st in target_stems)
        # Word overlap score
        matches = sum(1 for w in target_words if stem(w) in rec_stems)
        score = matches + (10 if ending_ok else 0)

        if ending_ok and matches >= max(1, len(target_words) - 2):
            logger.info(f"    Seed {seed:5d} -> PERFECT (ending OK, {matches}/{len(target_words)} words) | '{rec}'")
            return cand

        if score > best_score:
            best_score = score
            best_cand = cand

    return best_cand


def repair_segment(s_idx: int, text: str, seg_wav_path: Path, cosyvoice, prompt_text, whisper_model):
    logger.info(f"  Attempting self-healing repair on Seg {s_idx:04d}: '{text[:60]}...'")

    parts = split_text_into_smart_parts(text)
    if len(parts) > 1:
        logger.info(f"    Splitting into {len(parts)} natural parts...")
        part_chunks = []
        silence_sub = torch.zeros((1, int(cosyvoice.sample_rate * 0.20)))

        for p_i, p_text in enumerate(parts):
            cand = synthesize_candidate(p_text, cosyvoice, prompt_text, whisper_model)
            if cand is not None:
                part_chunks.append(cand)
            else:
                logger.error(f"    Failed to synthesize part {p_i+1}: '{p_text}'")

        if len(part_chunks) == len(parts):
            joined = []
            for i, c in enumerate(part_chunks):
                joined.append(sanitize_segment_audio(c, cosyvoice.sample_rate))
                if i < len(part_chunks) - 1:
                    joined.append(silence_sub)
            final_audio = torch.cat(joined, dim=1)
            final_audio = sanitize_segment_audio(final_audio, cosyvoice.sample_rate)
            torchaudio.save(str(seg_wav_path), final_audio, cosyvoice.sample_rate)
            dur = final_audio.shape[1] / cosyvoice.sample_rate
            logger.info(f"    Seg {s_idx:04d} repaired via smart split ({dur:.2f}s)")
            return True

    # If single sentence or split failed, try whole phrase seed sweep
    cand = synthesize_candidate(text, cosyvoice, prompt_text, whisper_model)
    if cand is not None:
        cand = sanitize_segment_audio(cand, cosyvoice.sample_rate)
        torchaudio.save(str(seg_wav_path), cand, cosyvoice.sample_rate)
        dur = cand.shape[1] / cosyvoice.sample_rate
        logger.info(f"    Seg {s_idx:04d} repaired via seed sweep ({dur:.2f}s)")
        return True

    logger.error(f"    Seg {s_idx:04d} repair failed!")
    return False


def reassemble_master(chap_dir: Path, sample_rate: int = 24000):
    logger.info("=== Reassembling Master WAV & MP3 after repairs ===")
    segments_json_path = chap_dir / "segments.json"
    cache_dir = chap_dir / "segments"

    with open(segments_json_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    silence_sentence = torch.zeros((1, int(sample_rate * 0.20)))
    silence_para = torch.zeros((1, int(sample_rate * 0.50)))
    silence_tail = torch.zeros((1, int(sample_rate * 0.50)))

    audio_chunks = []
    for s_idx, seg in enumerate(segments):
        s_wav = cache_dir / f"seg_{s_idx:04d}.wav"
        chunk, sr = torchaudio.load(str(s_wav))
        if sr != sample_rate:
            chunk = torchaudio.transforms.Resample(sr, sample_rate)(chunk)
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

    if chap_dir.parent.name.startswith("Книга"):
        safe_stem = f"{chap_dir.parent.name}_{chap_dir.name}"
    else:
        safe_stem = chap_dir.name

    master_wav = chap_dir / f"{safe_stem}.wav"
    torchaudio.save(str(master_wav), master_wav_tensor, sample_rate)
    dur_min = master_wav_tensor.shape[1] / sample_rate / 60
    logger.info(f"Master WAV updated: {master_wav} ({dur_min:.2f} min)")

    ffmpeg = shutil.which("ffmpeg") or str(ROOT_DIR / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe")
    master_mp3 = chap_dir / f"{safe_stem}.mp3"
    cmd = [
        ffmpeg, "-y",
        "-i", str(master_wav),
        "-codec:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", str(sample_rate),
        str(master_mp3)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    logger.info(f"Master MP3 updated: {master_mp3} ({master_mp3.stat().st_size / 1024 / 1024:.2f} MB)")

    tg_mp3 = chap_dir / f"{safe_stem}_telegram.mp3"
    cmd_tg = [
        ffmpeg, "-y",
        "-i", str(master_wav),
        "-codec:a", "libmp3lame",
        "-b:a", "64k",
        "-ar", str(sample_rate),
        str(tg_mp3)
    ]
    subprocess.run(cmd_tg, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    logger.info(f"Telegram MP3 updated: {tg_mp3} ({tg_mp3.stat().st_size / 1024 / 1024:.2f} MB)")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--chapter-dir", required=True)
    args = parser.parse_args()

    chap_dir = Path(args.chapter_dir)
    report_file = chap_dir / "gpu_verification_report.json"
    if not report_file.exists():
        logger.info(f"No report found at {report_file}. Nothing to repair.")
        return 0

    with open(report_file, "r", encoding="utf-8") as f:
        report = json.load(f)

    issues = report.get("issues", [])
    if not issues:
        logger.info("Verification report has 0 issues! Chapter is completely clean.")
        return 0

    logger.info(f"Found {len(issues)} issue(s) requiring self-healing repair...")

    model_dir = str(COSY_DIR / "pretrained_models" / "Fun-CosyVoice3-0.5B")
    THREAD_GUARD.install()
    logger.info("Loading CosyVoice 3 model...")
    cosyvoice = AutoModel(model_dir=model_dir)

    logger.info("Loading Whisper on CPU (0 VRAM)...")
    whisper_model = whisper.load_model("base", device="cpu")

    ref_text = REF_TXT_PATH.read_text(encoding="utf-8").strip() if REF_TXT_PATH.exists() else ""
    prompt_text = f"You are a helpful assistant.<|endofprompt|>{ref_text}"

    cache_dir = chap_dir / "segments"
    repaired_count = 0

    for issue in issues:
        s_idx = issue["s_idx"]
        text = issue.get("expected_text", issue.get("text", ""))
        seg_wav = cache_dir / f"seg_{s_idx:04d}.wav"
        if repair_segment(s_idx, text, seg_wav, cosyvoice, prompt_text, whisper_model):
            repaired_count += 1

    if repaired_count > 0:
        reassemble_master(chap_dir, cosyvoice.sample_rate)
        logger.info(f"=== Successfully repaired {repaired_count}/{len(issues)} segments! ===")
    # Exit status (was: always 0, even when nothing could be repaired):
    return 0 if repaired_count == len(issues) else EXIT_REPAIR_INCOMPLETE


if __name__ == "__main__":
    sys.exit(main())
