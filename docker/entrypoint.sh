#!/bin/bash
set -e

echo "======================================================"
echo "  AI-Dubbing: Russian Audiobook TTS Pipeline Container"
echo "  CosyVoice 3 + Whisper GPU Verification"
echo "======================================================"

# Ensure working directory
cd /app

# Ensure PYTHONPATH contains all needed directories
export PYTHONPATH="/app:/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS:${PYTHONPATH}"
export AUDIOBOOK_ROOT_DIR="/app"
export PYTHONUNBUFFERED=1

# Check if CosyVoice submodule or directory is missing
if [ ! -d "/app/CosyVoice/cosyvoice" ]; then
    echo "[INFO] /app/CosyVoice not found or empty."
    if [ -d "/opt/CosyVoice/cosyvoice" ]; then
        echo "[INFO] Copying cached CosyVoice from /opt/CosyVoice..."
        cp -rn /opt/CosyVoice/* /app/CosyVoice/ 2>/dev/null || cp -r /opt/CosyVoice /app/
    else
        echo "[INFO] Cloning CosyVoice repository with submodules..."
        git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
    fi
fi

# Ensure Matcha-TTS submodule exists
if [ ! -d "/app/CosyVoice/third_party/Matcha-TTS/matcha" ]; then
    echo "[INFO] Initializing Matcha-TTS submodule in CosyVoice..."
    cd /app/CosyVoice && git submodule update --init --recursive && cd /app
fi

# Automatic Model Verification & Download
if [ "${SKIP_MODEL_DOWNLOAD:-0}" != "1" ]; then
    echo "[INFO] Checking and preparing models (CosyVoice 3 + Whisper)..."
    python3 scripts/download_models.py || {
        echo "[ERROR] Model download failed. Please check internet connection or volume mounts."
        exit 1
    }
else
    echo "[INFO] SKIP_MODEL_DOWNLOAD is set. Skipping automatic model check."
fi

echo "======================================================"
echo "  Environment ready!"
echo "======================================================"

# If no arguments provided, run interactive audiobook orchestrator
if [ $# -eq 0 ]; then
    exec python3 run_audiobook.py
else
    exec "$@"
fi
