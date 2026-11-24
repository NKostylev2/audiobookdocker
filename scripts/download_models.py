#!/usr/bin/env python3
"""
Model bootstrap and download script for AI-Dubbing pipeline.
Automatically verifies and downloads:
1. Fun-CosyVoice3-0.5B (via ModelScope or Hugging Face)
2. OpenAI Whisper base model (cached in standard whisper cache or custom dir)

Safe to run multiple times: skips download if models already exist.
"""

import os
import sys
import argparse
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("ModelDownloader")


def find_root_dir() -> Path:
    override = os.environ.get("AUDIOBOOK_ROOT_DIR")
    if override and Path(override).is_dir():
        return Path(override).resolve()
    candidates = [
        Path(__file__).resolve().parent.parent,
        Path("/mnt/ai/AI-Dubbing"),
        Path("/app"),
        Path.cwd(),
    ]
    for c in candidates:
        if (c / "CosyVoice").is_dir() and (c / "voices").is_dir():
            return c.resolve()
    for c in candidates:
        if (c / "scripts").is_dir() and (c / "output").is_dir():
            return c.resolve()
    return Path(__file__).resolve().parent.parent


REQUIRED_COSY_FILES = [
    "cosyvoice3.yaml",
    "flow.pt",
    "hift.pt",
    "llm.pt",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
]


def check_cosyvoice_model(target_dir: Path) -> bool:
    if not target_dir.is_dir():
        return False
    missing = [f for f in REQUIRED_COSY_FILES if not (target_dir / f).is_file()]
    if missing:
        logger.info(f"CosyVoice model directory exists, but missing files: {missing}")
        return False
    return True


def download_cosyvoice_model(target_dir: Path, source: str = "auto", force: bool = False) -> bool:
    target_dir.mkdir(parents=True, exist_ok=True)
    if not force and check_cosyvoice_model(target_dir):
        logger.info(f"✓ CosyVoice 3 model already present at {target_dir}")
        return True

    logger.info(f"Downloading Fun-CosyVoice3-0.5B-2512 to {target_dir}...")
    errors = []

    # Strategy 1: ModelScope (primary source for CosyVoice)
    if source in ("auto", "modelscope"):
        try:
            logger.info("Attempting download via ModelScope...")
            from modelscope import snapshot_download
            snapshot_download(
                "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
                local_dir=str(target_dir),
            )
            if check_cosyvoice_model(target_dir):
                logger.info("✓ CosyVoice 3 model downloaded successfully via ModelScope!")
                return True
        except Exception as e:
            msg = f"ModelScope download failed: {e}"
            logger.warning(msg)
            errors.append(msg)

    # Strategy 2: Hugging Face fallback
    if source in ("auto", "huggingface"):
        try:
            logger.info("Attempting download via Hugging Face...")
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
                local_dir=str(target_dir),
            )
            if check_cosyvoice_model(target_dir):
                logger.info("✓ CosyVoice 3 model downloaded successfully via Hugging Face!")
                return True
        except Exception as e:
            msg = f"Hugging Face download failed: {e}"
            logger.warning(msg)
            errors.append(msg)

    logger.error(f"Failed to download CosyVoice model. Errors: {errors}")
    return False


def download_whisper_model(model_name: str = "base", download_root: str = None) -> bool:
    logger.info(f"Checking/downloading Whisper '{model_name}' model...")
    try:
        import whisper
        # whisper.load_model downloads the model if not present, then returns it
        whisper.load_model(model_name, device="cpu", download_root=download_root)
        logger.info(f"✓ Whisper '{model_name}' model verified/downloaded successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to download Whisper model '{model_name}': {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Download and verify required TTS/ASR models.")
    parser.add_argument("--root-dir", type=str, default=None, help="Root directory of AI-Dubbing pipeline")
    parser.add_argument("--source", choices=["auto", "modelscope", "huggingface"], default="auto",
                        help="Download source for CosyVoice (default: auto)")
    parser.add_argument("--whisper-model", default="base", help="Whisper model name (default: base)")
    parser.add_argument("--whisper-cache-dir", default=None, help="Custom directory for whisper weights cache")
    parser.add_argument("--check-only", action="store_true", help="Only verify existing models, do not download")
    parser.add_argument("--force", action="store_true", help="Force re-download even if files exist")
    parser.add_argument("--skip-whisper", action="store_true", help="Skip Whisper model download")
    parser.add_argument("--skip-cosyvoice", action="store_true", help="Skip CosyVoice model download")
    args = parser.parse_args()

    root_dir = Path(args.root_dir).resolve() if args.root_dir else find_root_dir()
    cosy_models_dir = root_dir / "CosyVoice" / "pretrained_models" / "Fun-CosyVoice3-0.5B"

    logger.info(f"AI-Dubbing Root: {root_dir}")
    logger.info(f"CosyVoice Model Dir: {cosy_models_dir}")

    if args.check_only:
        cosy_ok = check_cosyvoice_model(cosy_models_dir)
        logger.info(f"CosyVoice 3 Status: {'[FOUND]' if cosy_ok else '[MISSING]'}")
        if not cosy_ok:
            sys.exit(1)
        sys.exit(0)

    success = True
    if not args.skip_cosyvoice:
        if not download_cosyvoice_model(cosy_models_dir, source=args.source, force=args.force):
            success = False

    if not args.skip_whisper:
        if not download_whisper_model(model_name=args.whisper_model, download_root=args.whisper_cache_dir):
            success = False

    if not success:
        logger.error("❌ Some models could not be downloaded!")
        sys.exit(1)

    logger.info("🎉 All models verified and ready for inference!")
    sys.exit(0)


if __name__ == "__main__":
    main()
