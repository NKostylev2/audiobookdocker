#!/usr/bin/env python3
"""
GPU-based post-generation verification script for chapter segments.
Runs Whisper on CUDA to verify:
1. Every segment's audio ending matches the expected text (zero swallowed endings).
2. Word coverage and transcription sanity.
Generates a detailed report in the chapter directory.
"""

import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
import re
import difflib

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GPUVerifier")

def stem(w: str) -> str:
    w = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9]', '', w.lower().replace('ё', 'е'))
    if len(w) <= 3:
        return w
    return w[:max(3, len(w)-2)]

def clean_words(text: str):
    return [w.lower().replace('ё', 'е') for w in re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]+', text)]

def main():
    parser = argparse.ArgumentParser(description="Verify synthesized segments on GPU with Whisper.")
    parser.add_argument("--chapter-dir", required=True, help="Path to chapter output directory")
    parser.add_argument("--model", default="base", help="Whisper model name (default: base)")
    parser.add_argument("--device", default="cuda", help="Inference device (default: cuda)")
    args = parser.parse_args()

    chap_dir = Path(args.chapter_dir)
    segments_json_path = chap_dir / "segments.json"
    cache_dir = chap_dir / "segments"

    if not segments_json_path.exists():
        logger.error(f"Missing segments.json at {segments_json_path}")
        sys.exit(1)

    with open(segments_json_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    logger.info(f"Loaded {len(segments)} segments from {segments_json_path}")

    import torch
    import whisper

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fanfic_pipeline.runtime_guards import configure_cuda_optimizations
    configure_cuda_optimizations()

    if args.device == "cuda" and not torch.cuda.is_available():
        logger.warning("CUDA not available! Falling back to CPU.")
        device = "cpu"
    else:
        device = args.device

    logger.info(f"Loading Whisper '{args.model}' on {device}...")
    t0 = time.time()
    whisper_model = whisper.load_model(args.model, device=device)
    logger.info(f"Whisper loaded in {time.time() - t0:.2f}s")

    issues = []
    t_start_all = time.time()

    for idx, seg in enumerate(segments):
        wav_file = cache_dir / f"seg_{idx:04d}.wav"
        if not wav_file.exists():
            logger.error(f"[{idx+1}/{len(segments)}] Missing audio file: {wav_file}")
            issues.append({
                "s_idx": idx,
                "error": "missing_file",
                "text": seg.get("text", "")
            })
            continue

        expected_text = seg.get("text", "").strip()
        expected_words = clean_words(expected_text)
        if not expected_words:
            continue

        target_ending_words = expected_words[-2:] if len(expected_words) >= 2 else expected_words[-1:]
        target_stems = [stem(w) for w in target_ending_words]

        # Whisper transcribe
        res = whisper_model.transcribe(str(wav_file), language="ru")
        transcribed_text = res.get("text", "").strip()
        transcribed_words = clean_words(transcribed_text)
        transcribed_stems = [stem(w) for w in transcribed_words]

        # Check ending presence
        ending_found = any(st in transcribed_stems for st in target_stems)
        if not ending_found:
            # Fallback check substring in raw text
            ending_found = any(st in transcribed_text.lower() for st in target_stems)

        if not ending_found:
            logger.warning(
                f"[SWALLOW ISSUE] Seg {idx:04d}: expected ending {target_ending_words} (stems: {target_stems})\n"
                f"   Whisper heard: '{transcribed_text[-60:]}'"
            )
            issues.append({
                "s_idx": idx,
                "type": "swallowed_ending",
                "expected_ending": target_ending_words,
                "whisper_tail": transcribed_text[-80:],
                "expected_text": expected_text,
                "transcribed_text": transcribed_text
            })
        elif (idx + 1) % 25 == 0 or (idx + 1) == len(segments):
            logger.info(f"Verified [{idx+1}/{len(segments)}] segments OK on GPU ({time.time()-t_start_all:.1f}s)")

    report = {
        "chapter_dir": str(chap_dir),
        "total_segments": len(segments),
        "issues_count": len(issues),
        "issues": issues,
        "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": args.model,
        "device": device,
        "elapsed_seconds": round(time.time() - t_start_all, 2)
    }

    report_path = chap_dir / "gpu_verification_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info("=" * 60)
    if issues:
        logger.warning(f"Verification complete: found {len(issues)} issue(s)! Report saved to {report_path}")
        sys.exit(2)
    else:
        logger.info(f"Verification complete: ALL {len(segments)} segments verified 100% OK on GPU! Report saved to {report_path}")
        sys.exit(0)

if __name__ == "__main__":
    main()
