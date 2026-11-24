# syntax=docker/dockerfile:1
FROM pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime

LABEL maintainer="AI-Dubbing"
LABEL description="CosyVoice 3 + Whisper Russian Audiobook Pipeline"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV AUDIOBOOK_ROOT_DIR=/app
ENV PYTHONPATH=/app:/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS

# 1. Install system audio & build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    git-lfs \
    ffmpeg \
    sox \
    libsox-dev \
    libsndfile1 \
    curl \
    ca-certificates \
    && git lfs install \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Clone and cache CosyVoice upstream repo into /opt/CosyVoice
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /opt/CosyVoice

# 3. Install Python dependencies (leveraging Docker layer caching)
RUN pip install --no-cache-dir --upgrade pip "setuptools<70" wheel
COPY requirements-docker.txt /app/requirements-docker.txt
RUN pip install --no-cache-dir -r requirements-docker.txt

# 4. Copy project source code
COPY . /app
RUN if [ ! -d "/app/CosyVoice/cosyvoice" ]; then cp -r /opt/CosyVoice /app/CosyVoice; fi

# 5. Set entrypoint
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Persistent volumes for output audiobooks, models cache, and work data
VOLUME ["/app/output", "/app/work", "/app/CosyVoice/pretrained_models", "/root/.cache"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python3", "run_audiobook.py"]
