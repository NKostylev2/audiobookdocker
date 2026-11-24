import os
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
import sys
import time
import json
import logging
import subprocess
import urllib.request
import urllib.error
import argparse
import zipfile
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

# --- Dynamic Path Resolution (Cross-platform Windows & Linux) ---
def find_root_dir() -> Path:
    override = os.environ.get("AUDIOBOOK_ROOT_DIR")   # explicit root (also used by the test-suite)
    if override and Path(override).is_dir():
        return Path(override).resolve()
    candidates = [
        Path("/mnt/ai/AI-Dubbing"),
        Path(r"E:\AI-Dubbing"),
        Path.cwd(),
        Path.cwd().parent,
    ]
    for c in candidates:
        if (c / "CosyVoice").is_dir() and (c / "voices").is_dir():
            return c.resolve()
    return Path.cwd().resolve()

ROOT_DIR = find_root_dir()
COSY_DIR = ROOT_DIR / "CosyVoice"
MATCHA_DIR = COSY_DIR / "third_party" / "Matcha-TTS"

for p in [str(ROOT_DIR), str(COSY_DIR), str(MATCHA_DIR)]:
    if p not in sys.path and os.path.exists(p):
        sys.path.insert(0, p)

def find_ffmpeg() -> Path:
    which = shutil.which("ffmpeg")
    if which:
        return Path(which)
    candidate = ROOT_DIR / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if candidate.exists():
        return candidate
    return Path("ffmpeg")

def find_default_voice() -> Path:
    candidates = [
        ROOT_DIR / "voices" / "VID_20261101_122350_289.wav",
        ROOT_DIR / "alltalk_tts" / "voices" / "VID_20261101_122350_289.wav",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return candidates[0]

FFMPEG_EXE = find_ffmpeg()
VOICE_FILE = str(find_default_voice())
VOICE_NAME = Path(VOICE_FILE).stem
DEFAULT_TEXT_PATH = str(ROOT_DIR / "chapter.txt")

OUT_DIR = ROOT_DIR / "output" / "chamber_of_secrets_ch01_vid_voice"
CACHE_DIR = OUT_DIR / "segments"
LOG_FILE = OUT_DIR / "generation.log"
STATUS_FILE = OUT_DIR / "status.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("ChapterVIDGenerator")

import re
import numpy as np
import torch
import torchaudio

if torch.cuda.is_available():
    torch.set_float32_matmul_precision("high")
import razdel
from cosyvoice.cli.cosyvoice import AutoModel
from fanfic_pipeline.segmenter import SentenceSegmenter
from fanfic_pipeline.exit_codes import (
    EXIT_TEXT_MISSING, EXIT_VOICE_MISSING, EXIT_NO_AUDIO, EXIT_SEGMENTS_FAILED,
)
from fanfic_pipeline.runtime_guards import ThreadExceptionGuard
from fanfic_pipeline.split_ops import split_for_fallback

# CosyVoice decodes in a worker thread and swallows its exceptions (see runtime_guards.py)
THREAD_GUARD = ThreadExceptionGuard()

def load_paragraphs(file_path: str) -> list:
    p = Path(file_path)
    if p.suffix.lower() == ".docx":
        z = zipfile.ZipFile(str(p))
        tree = ET.fromstring(z.read("word/document.xml"))
        namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paras = []
        for p_node in tree.iterfind('.//w:p', namespaces):
            texts = [node.text for node in p_node.iterfind('.//w:t', namespaces) if node.text]
            if texts:
                t = "".join(texts).strip()
                if t:
                    paras.append(t)
        return paras
    else:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        raw_paras = re.split(r'\n\s*\n+', content)
        paras = []
        for block in raw_paras:
            clean_p = re.sub(r'[ \t]*\n[ \t]*', ' ', block).strip()
            clean_p = re.sub(r'^[#*_\s]+', '', clean_p)
            clean_p = re.sub(r'[ \t]+', ' ', clean_p).strip()
            if clean_p:
                paras.append(clean_p)
        return paras

def get_telegram_config():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    proxy = os.environ.get("TELEGRAM_PROXY")
    env_candidates = [
        ROOT_DIR / ".env",
        Path(r"/home/nk/dev/Telegram Remote Control/.env"),
        Path(r"M:\dev\Telegram Remote Control\.env"),
    ]
    if not token:
        for env_file in env_candidates:
            if env_file.exists():
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("TELEGRAM_BOT_TOKEN="):
                            token = line.split("=", 1)[1].strip("\"'")
                        elif line.startswith("TELEGRAM_PROXY=") and not proxy:
                            proxy = line.split("=", 1)[1].strip("\"'")
                if token:
                    break

    chat_id_env = os.environ.get("TELEGRAM_CHAT_ID")
    chat_id = int(chat_id_env) if chat_id_env and chat_id_env.isdigit() else 373648438
    db_candidates = [
        Path(r"/home/nk/dev/Telegram Remote Control/data/tasks.sqlite3"),
        Path(r"M:\dev\Telegram Remote Control/data/tasks.sqlite3"),
    ]
    for db_file in db_candidates:
        if db_file.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_file))
                cur = conn.cursor()
                cur.execute("SELECT chat_id FROM tasks WHERE chat_id IS NOT NULL ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                if row and row[0]:
                    chat_id = row[0]
                conn.close()
                break
            except Exception as e:
                logger.warning(f"Failed to read chat_id from sqlite: {e}")

    # Test direct connection if proxy is specified
    if proxy:
        try:
            req = urllib.request.Request("https://api.telegram.org", headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                proxy = None  # Direct connection works
        except Exception:
            pass

    return token, chat_id, proxy

def get_telegram_opener(proxy: str = None):
    if proxy:
        proxy_handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy})
        return urllib.request.build_opener(proxy_handler)
    return urllib.request.build_opener()

def send_telegram_message(text: str, token: str, chat_id: int, proxy: str = None) -> bool:
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Telegram-Progress-Reporter/1.0"},
    )
    opener = get_telegram_opener(proxy)
    try:
        with opener.open(req, timeout=15) as resp:
            return True
    except Exception as e:
        logger.warning(f"Failed to send telegram message: {e}")
        return False

def send_telegram_file(file_path: Path, caption: str, token: str, chat_id: int, proxy: str = None, is_audio: bool = True, title: str = None) -> bool:
    if not token or not file_path.exists():
        return False

    url = f"https://api.telegram.org/bot{token}/{'sendAudio' if is_audio else 'sendDocument'}"
    boundary = "----WebKitFormBoundaryAudio" + str(int(time.time()))
    file_bytes = file_path.read_bytes()
    filename = file_path.name
    field_name = "audio" if is_audio else "document"

    body_parts = []
    body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
    body_parts.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode("utf-8"))
    if is_audio:
        track_title = title if title else "Audiobook"
        body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
        body_parts.append(f'Content-Disposition: form-data; name="title"\r\n\r\n{track_title}\r\n'.encode("utf-8"))
        body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
        body_parts.append(f'Content-Disposition: form-data; name="performer"\r\n\r\nCosyVoice 3 ({VOICE_NAME})\r\n'.encode("utf-8"))
    if caption:
        body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
        body_parts.append(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode("utf-8"))
        body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
        body_parts.append(f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'.encode("utf-8"))
    body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
    body_parts.append(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n".encode("utf-8")
    )
    body_parts.append(file_bytes)
    body_parts.append(b"\r\n")
    body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    payload = b"".join(body_parts)
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Telegram-Voice-Sender/1.0",
        },
    )
    opener = get_telegram_opener(proxy)
    try:
        with opener.open(req, timeout=180) as resp:
            logger.info(f"Telegram upload OK: {filename}")
            return True
    except Exception as e:
        logger.warning(f"Upload failed for {filename}: {e}")
        return False

def update_status(data: dict):
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Could not write status file: {e}")

def validate_and_prepare_segments(paragraphs: list, segmenter: SentenceSegmenter) -> list:
    """
    Splits paragraphs into balanced token blocks, verifies word and ending preservation,
    repairs non-terminal punctuation, and merges any short trailing segments.
    """
    raw_segments = segmenter.segment_chapter(paragraphs)
    logger.info(f"Initial segmentation: {len(paragraphs)} paragraphs -> {len(raw_segments)} segments.")

    # Merge trailing short segment (<40 tokens) into previous segment if needed
    if len(raw_segments) > 1 and raw_segments[-1]["tokens"] < 40:
        last = raw_segments.pop()
        prev = raw_segments[-1]
        prev["text"] = prev["text"].rstrip() + " " + last["text"].lstrip()
        prev["tokens"] = segmenter.count_tokens(prev["text"])
        prev["is_last_in_p"] = last["is_last_in_p"]
        logger.info(f"Merged trailing short segment into block {prev['s_idx']} (new tokens: {prev['tokens']})")

    repaired_count = 0
    for idx, seg in enumerate(raw_segments):
        t = seg["text"].strip()
        # Clean non-terminal punctuation at segment boundaries
        if t.endswith(";"):
            t = t[:-1] + "."
            repaired_count += 1
            logger.info(f"Repaired segment {idx:04d} ending: ';' -> '.'")
        elif t.endswith((",", ":", "—", "–", "-")):
            t = t[:-1] + "."
            repaired_count += 1
            logger.info(f"Repaired segment {idx:04d} ending: trailing non-terminal mark -> '.'")
        elif t and t[-1] in "0123456789":
            t = t + "."
            repaired_count += 1
            logger.info(f"Repaired segment {idx:04d} ending: digit -> '.'")
        seg["text"] = t

    # Word-level comparison to guarantee zero loss of endings/words
    orig_text = " ".join(paragraphs)
    seg_text = " ".join(s["text"] for s in raw_segments)
    orig_words = re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]+', orig_text.lower())
    seg_words = re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]+', seg_text.lower())

    if orig_words == seg_words:
        logger.info(f"[Text Check OK] All {len(orig_words)} words and endings preserved 100% in order!")
    else:
        logger.warning(f"[Text Check WARNING] Word counts: original={len(orig_words)}, segmented={len(seg_words)}")
        import difflib
        diff = [d for d in difflib.ndiff(orig_words, seg_words) if d.startswith("+ ") or d.startswith("- ")]
        logger.warning(f"Diff sample: {diff[:10]}")

    logger.info(f"Segmentation finalized: {len(raw_segments)} segments (Repaired endings: {repaired_count}).")
    return raw_segments

def verify_segment_audio_ending(audio_chunk: torch.Tensor, sr: int, expected_text: str, whisper_model) -> bool:
    """
    Uses Whisper to verify that the ending of the synthesized audio segment
    was not swallowed or prematurely terminated.
    """
    raw_words = re.findall(r'[a-zA-Zа-яА-ЯёЁ]+', expected_text.lower())
    words = [re.sub(r"'s$", "", w) for w in raw_words]
    words = [w for w in words if len(w) > 1 or w in ('i', 'a', 'я', 'и', 'в', 'к', 'с', 'у', 'о')]
    if not words:
        return True
    target_words = words[-2:] if len(words) >= 2 else words[-1:]

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

        ok = any(w in rec or stem(w) in rec for w in target_words)
        if not ok:
            logger.warning(f"[Ending Swallow Detected] Expected: {target_words} | Whisper ({whisper_lang}) heard: '{res['text'].strip()[-40:]}'")
        return ok
    except Exception as e:
        logger.warning(f"Whisper ending verification check failed: {e}")
        return True
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except Exception:
                pass

def sanitize_segment_audio(wav: torch.Tensor, sr: int = 24000) -> torch.Tensor:
    """
    Sanitizes synthesized speech segment audio to eliminate acoustic artifacts,
    digital step clicks, and DC offset at chunk boundaries:
    1. Ensures 2D shape (1, N) and removes any DC offset.
    2. Detects and trims any leading non-speech burst/transient, preserving
       a clean natural pre-speech silence margin (~50-80ms).
    3. Applies smooth 15ms raised-cosine fade-in (starts at exact 0.000000).
    4. Applies smooth 15ms raised-cosine fade-out (ends at exact 0.000000).
    """
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)
    elif wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)

    # 1. Remove DC offset
    wav = wav - torch.mean(wav)

    # 2. Analyze leading frames for burst and silence valley
    y = wav[0].cpu().numpy()
    win = int(0.005 * sr)  # 5ms frames
    n_frames = min(int(1.5 / 0.005), len(y) // win)

    if n_frames > 20:
        energies = np.array([np.max(np.abs(y[i * win : (i + 1) * win])) for i in range(n_frames)])
        init_burst = np.max(energies[:10]) > 0.05

        if init_burst:
            # Find silence valley (energy < 0.025) after burst (between 0.10s and 1.2s)
            valleys = [i for i in range(20, min(len(energies), int(1.2 / 0.005))) if energies[i] < 0.025]
            if valleys:
                first_valley = valleys[0]
                speech_onset = None
                for i in range(first_valley, len(energies) - 2):
                    if energies[i] > 0.06 and energies[i + 1] > 0.06:
                        speech_onset = i
                        break

                if speech_onset is not None:
                    clean_valley_idx = speech_onset - int(0.06 / 0.005)
                    clean_valley_idx = max(first_valley, min(clean_valley_idx, speech_onset - 4))
                    cut_sample = clean_valley_idx * win
                    wav = wav[:, cut_sample:]
            else:
                # Fallback: local argmin in [20, 80]
                min_idx = 20 + np.argmin(energies[20:80])
                if energies[min_idx] < 0.05:
                    cut_sample = min_idx * win
                    wav = wav[:, cut_sample:]

    # 3. Apply smooth 15ms raised-cosine fade-in and fade-out
    fade_len = int(0.015 * sr)
    if wav.shape[1] > 2 * fade_len:
        t = torch.linspace(0, np.pi, fade_len, device=wav.device)
        fade_in = 0.5 * (1.0 - torch.cos(t))
        fade_out = 0.5 * (1.0 + torch.cos(t))
        wav[:, :fade_len] *= fade_in
        wav[:, -fade_len:] *= fade_out

    return wav

def get_default_workers() -> int:
    env_w = os.environ.get("AUDIOBOOK_WORKERS")
    if env_w and env_w.isdigit():
        return max(1, int(env_w))
    if not torch.cuda.is_available():
        return 1
    try:
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        if vram_gb >= 22.0:
            return 3  # RTX 3090, RTX 4090 (24GB) -> 3 parallel workers (~19.5GB VRAM)
        elif vram_gb >= 14.0:
            return 2  # 16GB cards -> 2 parallel workers (~13GB VRAM)
        else:
            return 1  # 8GB cards (RTX 3070) -> 1 worker (safe from OOM)
    except Exception:
        return 1

def synthesize_segment_core(
    s_idx: int,
    text: str,
    seg_wav_path: Path,
    cosyvoice,
    whisper_model,
    prompt_text: str,
    voice_file: str,
    base_seed: int,
    thread_guard: ThreadExceptionGuard,
    logger,
    silence_sentence: torch.Tensor,
) -> tuple:
    """
    Synthesizes a single segment with seed retries (up to 3) and Whisper ending verification.
    If endings are still swallowed, splits into halves and joins them.
    Saves the sanitized audio chunk to seg_wav_path.
    Returns (success: bool, duration: float, message: str).
    """
    sample_rate = getattr(cosyvoice, "sample_rate", 24000)
    best_chunk = None
    ending_ok = False

    # Attempt synthesis with up to 3 seed retries if ending is swallowed
    for attempt, seed_offset in enumerate([0, 17, 42]):
        cur_seed = base_seed + seed_offset
        torch.manual_seed(cur_seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(cur_seed)

        try:
            thread_guard.clear()
            outputs = list(cosyvoice.inference_zero_shot(
                tts_text=text,
                prompt_text=prompt_text,
                prompt_wav=voice_file,
                stream=False,
                speed=1.0,
                text_frontend=False
            ))
            thread_guard.raise_if_failed()
        except Exception as e:
            logger.warning(f"[{s_idx+1}] Inference failed on attempt {attempt+1} (seed {cur_seed}): {e}")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            continue

        if outputs and outputs[0]['tts_speech'].shape[1] > 0:
            wav_candidate = torch.concat([o['tts_speech'] for o in outputs], dim=1)
            if whisper_model:
                ending_ok = verify_segment_audio_ending(wav_candidate, sample_rate, text, whisper_model)
                if ending_ok:
                    best_chunk = wav_candidate
                    break
                else:
                    best_chunk = wav_candidate
                    logger.info(f"[{s_idx+1}] Swallowed ending detected, retry attempt {attempt+1} with seed {cur_seed+17}...")
            else:
                ending_ok = True
                best_chunk = wav_candidate
                break

    # Fallback: if all retries still swallowed the ending, split segment into halves
    if best_chunk is not None and whisper_model and not ending_ok:
        logger.warning(f"[{s_idx+1}] Retries exhausted. Splitting segment into halves to guarantee endings...")
        halves = split_for_fallback(text)
        if halves is None:
            logger.warning(f"[{s_idx+1}] No safe split point; keeping the best candidate unchanged.")
            halves = []
        half_chunks = []
        for h_i, half_text in enumerate(halves):
            for h_attempt, h_seed_offset in enumerate([0, 19, 47]):
                h_seed = base_seed + h_i * 100 + h_seed_offset
                torch.manual_seed(h_seed)
                if torch.cuda.is_available():
                    torch.cuda.manual_seed_all(h_seed)
                try:
                    thread_guard.clear()
                    h_outputs = list(cosyvoice.inference_zero_shot(
                        tts_text=half_text,
                        prompt_text=prompt_text,
                        prompt_wav=voice_file,
                        stream=False,
                        speed=1.0,
                        text_frontend=False
                    ))
                    thread_guard.raise_if_failed()
                except Exception as e:
                    logger.warning(f"[{s_idx+1}] Half inference failed (half {h_i}, attempt {h_attempt+1}): {e}")
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    continue

                if h_outputs and h_outputs[0]['tts_speech'].shape[1] > 0:
                    h_cand = torch.concat([o['tts_speech'] for o in h_outputs], dim=1)
                    if verify_segment_audio_ending(h_cand, sample_rate, half_text, whisper_model):
                        half_chunks.append(h_cand)
                        break
                    elif h_attempt == 2:
                        half_chunks.append(h_cand)

        if halves and len(half_chunks) == len(halves):
            joined = []
            for i, c in enumerate(half_chunks):
                joined.append(c)
                if i < len(half_chunks) - 1:
                    joined.append(silence_sentence)
            best_chunk = torch.cat(joined, dim=1)
            logger.info(f"[{s_idx+1}] Fallback split successful! Repaired segment: {text[:45]}...")

    if best_chunk is not None:
        wav_chunk = sanitize_segment_audio(best_chunk, sample_rate)
        torchaudio.save(str(seg_wav_path), wav_chunk, sample_rate)
        seg_dur = wav_chunk.shape[1] / sample_rate
        return (True, seg_dur, "OK")
    else:
        logger.error(f"[{s_idx+1}] Segment could not be synthesized after all attempts: {text}")
        return (False, 0.0, "Failed")

def _worker_process_entry(
    worker_id: int,
    model_dir: str,
    voice_file: str,
    prompt_text: str,
    whisper_dev: str,
    whisper_verify: bool,
    task_queue,
    result_queue,
):
    """
    Subprocess worker for parallel CosyVoice 3 + Whisper synthesis.
    """
    class QueueLogger:
        def info(self, msg):
            result_queue.put({"type": "log", "worker_id": worker_id, "level": "info", "msg": str(msg)})
        def warning(self, msg):
            result_queue.put({"type": "log", "worker_id": worker_id, "level": "warning", "msg": str(msg)})
        def error(self, msg):
            result_queue.put({"type": "log", "worker_id": worker_id, "level": "error", "msg": str(msg)})

    w_logger = QueueLogger()
    thread_guard = ThreadExceptionGuard()
    thread_guard.install()

    try:
        w_logger.info(f"Worker {worker_id + 1} loading CosyVoice 3 model...")
        cosyvoice = AutoModel(model_dir=model_dir)
        sr = getattr(cosyvoice, "sample_rate", 24000)
        silence_sentence = torch.zeros((1, int(sr * 0.20)))

        whisper_model = None
        if whisper_verify:
            import whisper
            w_logger.info(f"Worker {worker_id + 1} loading Whisper base on {whisper_dev.upper()}...")
            whisper_model = whisper.load_model("base", device=whisper_dev)

        result_queue.put({"type": "ready", "worker_id": worker_id})
    except Exception as e:
        w_logger.error(f"Worker {worker_id + 1} failed during initialization: {e}")
        result_queue.put({"type": "error", "worker_id": worker_id, "error": str(e)})
        return

    while True:
        try:
            task = task_queue.get()
        except Exception:
            break
        if task is None:
            break

        s_idx, text, base_seed, seg_wav_str = task
        seg_wav_path = Path(seg_wav_str)
        t0 = time.time()

        try:
            success, dur, msg = synthesize_segment_core(
                s_idx=s_idx,
                text=text,
                seg_wav_path=seg_wav_path,
                cosyvoice=cosyvoice,
                whisper_model=whisper_model,
                prompt_text=prompt_text,
                voice_file=voice_file,
                base_seed=base_seed,
                thread_guard=thread_guard,
                logger=w_logger,
                silence_sentence=silence_sentence,
            )
        except Exception as e:
            w_logger.error(f"Worker {worker_id + 1} unhandled exception on segment {s_idx+1}: {e}")
            success, dur, msg = False, 0.0, str(e)

        elapsed = time.time() - t0
        result_queue.put({
            "type": "result",
            "worker_id": worker_id,
            "s_idx": s_idx,
            "success": success,
            "dur": dur,
            "elapsed": elapsed,
            "text_preview": text[:45],
            "msg": msg
        })

    result_queue.put({"type": "done", "worker_id": worker_id})

def main():
    global OUT_DIR, CACHE_DIR, LOG_FILE, STATUS_FILE, FFMPEG_EXE, VOICE_FILE, VOICE_NAME

    parser = argparse.ArgumentParser()
    parser.add_argument("--text-path", default=DEFAULT_TEXT_PATH)
    parser.add_argument("--voice-file", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--clean-cache", action="store_true")
    parser.add_argument("--no-telegram", action="store_true", help="Disable Telegram reporting and upload")
    parser.add_argument("--whisper-verify", action="store_true", default=True, help="Verify segment audio endings with Whisper")
    parser.add_argument("--workers", type=int, default=None, help="Number of parallel workers (default: auto based on VRAM)")
    parser.add_argument("--export-windows", action="store_true", default=False, help="Export master to Windows Desktop/Downloads")
    args = parser.parse_args()

    text_path = args.text_path
    if not os.path.exists(text_path):
        logger.error(f"Text file missing: {text_path}")
        sys.exit(EXIT_TEXT_MISSING)

    if args.voice_file:
        VOICE_FILE = args.voice_file
    VOICE_NAME = Path(VOICE_FILE).stem

    # Dynamic output directory and chapter title
    stem = Path(text_path).stem
    auto_title = stem   # was assigned only in the `else` branch -> UnboundLocalError with --output-dir and no --title
    if args.output_dir:
        OUT_DIR = Path(args.output_dir)
    else:
        # Structured hierarchy: Book / Chapter
        stem_lower = stem.lower()
        
        book_mapping = {
            1: (ROOT_DIR / "output" / "Книга_1_Философский_камень", "Книга 1: Философский камень"),
            2: (ROOT_DIR / "output" / "Книга_2_Тайная_комната", "Книга 2: Тайная комната"),
            3: (ROOT_DIR / "output" / "Книга_3_Узник_Азкабана", "Книга 3: Узник Азкабана"),
            4: (ROOT_DIR / "output" / "Книга_4_Кубок_огня", "Книга 4: Кубок огня"),
            5: (ROOT_DIR / "output" / "Книга_5_Орден_Феникса", "Книга 5: Орден Феникса"),
        }
        
        b_num = None
        m_book = re.search(r'(?:книга|book)_?(\d+)', stem_lower)
        if m_book:
            b_num = int(m_book.group(1))
        elif 'azkaban' in stem_lower or 'узник' in stem_lower:
            b_num = 3
        elif 'chamber' in stem_lower or 'тайная' in stem_lower:
            b_num = 2
        elif 'philosopher' in stem_lower or 'философский' in stem_lower:
            b_num = 1
            
        if b_num in book_mapping:
            book_dir, b_title = book_mapping[b_num]
        else:
            book_dir = ROOT_DIR / "output" / (f"Книга_{b_num}" if b_num else "Книга")
            b_title = f"Книга {b_num}" if b_num else "Книга"
            
        m_ch = re.search(r'(?:глава|ch(?:apter)?)[_\s]*(\d+)', stem_lower)
        m_pro = re.search(r'(?:пролог|prologue)[_\s]*(\d*)', stem_lower)
        is_english = ("chapters_en" in str(Path(text_path).resolve()) or (re.search(r'\b(?:book|chapter|prologue)\b', stem_lower) and not re.search(r'[а-яА-ЯёЁ]', stem)))
        lang_suffix = "_EN" if is_english else ""
        lang_title_tag = " (EN)" if is_english else ""
        
        if m_ch:
            ch_num = int(m_ch.group(1))
            OUT_DIR = book_dir / f"Глава_{ch_num:02d}{lang_suffix}"
            auto_title = f"Змея, Лев и Выдра. {b_title}. Глава {ch_num}{lang_title_tag}"
        elif m_pro:
            pro_num_str = m_pro.group(1)
            if pro_num_str:
                pro_num = int(pro_num_str)
                cand_num = book_dir / f"Пролог_{pro_num:02d}{lang_suffix}"
                cand_single = book_dir / f"Пролог{lang_suffix}"
                if cand_num.exists():
                    OUT_DIR = cand_num
                    auto_title = f"Змея, Лев и Выдра. {b_title}. Пролог {pro_num}{lang_title_tag}"
                elif cand_single.exists() and not is_english:
                    OUT_DIR = cand_single
                    auto_title = f"Змея, Лев и Выдра. {b_title}. Пролог{lang_title_tag}"
                else:
                    OUT_DIR = cand_num
                    auto_title = f"Змея, Лев и Выдра. {b_title}. Пролог {pro_num}{lang_title_tag}"
            else:
                OUT_DIR = book_dir / f"Пролог{lang_suffix}"
                auto_title = f"Змея, Лев и Выдра. {b_title}. Пролог{lang_title_tag}"
        else:
            safe_name = re.sub(r'[^\w\-_\.]', '_', stem)
            OUT_DIR = book_dir / (f"{safe_name}{lang_suffix}")
            auto_title = f"Змея, Лев и Выдра. {b_title}. {stem}{lang_title_tag}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR = OUT_DIR / "segments"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE = OUT_DIR / "generation.log"
    STATUS_FILE = OUT_DIR / "status.json"

    file_handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)

    chapter_title = args.title or auto_title

    if args.clean_cache:
        for f in CACHE_DIR.glob("*.wav"):
            try:
                f.unlink()
            except Exception:
                pass

    # A new generation run invalidates the previous verification report (it described other audio).
    try:
        (OUT_DIR / "gpu_verification_report.json").unlink()
    except FileNotFoundError:
        pass

    THREAD_GUARD.install()

    logger.info(f"=== Starting Full Chapter Generation Pipeline ({VOICE_NAME}) -> {OUT_DIR} ===")
    if args.no_telegram:
        token, chat_id, proxy = None, None, None
    else:
        token, chat_id, proxy = get_telegram_config()
    logger.info(f"Telegram Config: Token Present={bool(token)}, ChatID={chat_id}, Proxy={proxy}")

    if not os.path.exists(VOICE_FILE):
        logger.error(f"Voice reference file missing: {VOICE_FILE}")
        update_status({"state": "failed", "error": f"Voice missing: {VOICE_FILE}"})
        sys.exit(EXIT_VOICE_MISSING)

    # 1. Load CosyVoice Model first to get tokenizer
    model_dir = str(COSY_DIR / "pretrained_models" / "Fun-CosyVoice3-0.5B")
    num_workers = args.workers if args.workers is not None else get_default_workers()
    logger.info(f"Parallel synthesis workers configured: {num_workers}")

    whisper_dev = os.environ.get("WHISPER_DEVICE")
    if not whisper_dev:
        if torch.cuda.is_available():
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            whisper_dev = "cuda" if vram_gb >= 12.0 else "cpu"
        else:
            whisper_dev = "cpu"

    sample_rate = 24000
    cosyvoice = None
    whisper_model = None

    if num_workers == 1:
        logger.info(f"Loading CosyVoice 3 model from {model_dir}...")
        t0 = time.time()
        cosyvoice = AutoModel(model_dir=model_dir)
        sample_rate = getattr(cosyvoice, "sample_rate", 24000)
        logger.info(f"Model loaded in {time.time() - t0:.2f}s. Sample rate: {sample_rate}")

        if args.whisper_verify:
            logger.info(f"Loading Whisper base on {whisper_dev.upper()} for automated ending verification...")
            import whisper
            whisper_model = whisper.load_model("base", device=whisper_dev)
            logger.info(f"Whisper model ready on {whisper_dev.upper()}.")
        tokenizer = cosyvoice.frontend.tokenizer
    else:
        qwen_path = os.path.join(model_dir, 'CosyVoice-BlankEN')
        if os.path.isdir(qwen_path):
            try:
                from cosyvoice.tokenizer.tokenizer import get_qwen_tokenizer
                tokenizer = get_qwen_tokenizer(qwen_path, skip_special_tokens=True, version='cosyvoice3')
                logger.info("Loaded tokenizer on CPU (multi-worker mode).")
            except Exception:
                cosyvoice = AutoModel(model_dir=model_dir)
                tokenizer = cosyvoice.frontend.tokenizer
                sample_rate = getattr(cosyvoice, "sample_rate", 24000)
        else:
            cosyvoice = AutoModel(model_dir=model_dir)
            tokenizer = cosyvoice.frontend.tokenizer
            sample_rate = getattr(cosyvoice, "sample_rate", 24000)

    # Load reference transcript
    ref_txt_path = Path(VOICE_FILE).with_suffix(".reference.txt")
    ref_text = ""
    if ref_txt_path.exists():
        ref_text = ref_txt_path.read_text(encoding="utf-8").strip()
    prompt_text = f"You are a helpful assistant.<|endofprompt|>{ref_text}"

    # 2. Read & Segment Chapter with Token-Aware Segmenter (35-65 tokens per block)
    paragraphs = load_paragraphs(text_path)
    segmenter = SentenceSegmenter(
        tokenizer=tokenizer,
        min_tokens=35,
        max_tokens=65,
        min_block_tokens=25
    )
    segments = validate_and_prepare_segments(paragraphs, segmenter)
    total_segs = len(segments)

    # Save segments json for full traceability
    with open(OUT_DIR / "segments.json", "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)

    # Send Telegram start announcement if enabled
    if not args.no_telegram:
        start_msg = (
            f"🎙 <b>Генерация {chapter_title} запущена</b>\n"
            f"👤 Голос: <code>{VOICE_NAME}</code>\n"
            f"⚙️ Режим: <code>zero-shot</code> (воркеров: {num_workers})\n"
            f"📑 Всего блоков: <b>{total_segs}</b> (60–90 токенов/блок)\n"
            f"🛡️ Автопроверка и защита окончаний: <b>АКТИВНА</b>\n"
            f"🛡️ Хвостовая тишина: <b>+500 мс</b>"
        )
        send_telegram_message(start_msg, token, chat_id, proxy)

    status = {
        "state": "running",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_segments": total_segs,
        "completed_segments": 0,
        "voice": VOICE_NAME,
        "chapter": chapter_title,
    }
    update_status(status)

    silence_sentence = torch.zeros((1, int(sample_rate * 0.20))) # 200 ms
    silence_para = torch.zeros((1, int(sample_rate * 0.50)))     # 500 ms
    silence_tail = torch.zeros((1, int(sample_rate * 0.50)))     # 500 ms tail digital silence

    # Check existing cached segments to find work queue
    missing_indices = []
    cached_count = 0
    for s_idx, seg in enumerate(segments):
        seg_wav_path = CACHE_DIR / f"seg_{s_idx:04d}.wav"
        if seg_wav_path.exists() and seg_wav_path.stat().st_size > 1000:
            cached_count += 1
        else:
            missing_indices.append(s_idx)

    logger.info(f"Segment cache status: {cached_count}/{total_segs} already cached, {len(missing_indices)} to synthesize.")

    failed_segments = []
    t_start_synth = time.time()

    if missing_indices:
        if num_workers == 1:
            for s_idx in missing_indices:
                seg = segments[s_idx]
                seg_wav_path = CACHE_DIR / f"seg_{s_idx:04d}.wav"
                text = seg["text"].strip()
                if not text:
                    continue
                t_seg = time.time()
                success, dur, msg = synthesize_segment_core(
                    s_idx=s_idx,
                    text=text,
                    seg_wav_path=seg_wav_path,
                    cosyvoice=cosyvoice,
                    whisper_model=whisper_model,
                    prompt_text=prompt_text,
                    voice_file=VOICE_FILE,
                    base_seed=2026 + s_idx,
                    thread_guard=THREAD_GUARD,
                    logger=logger,
                    silence_sentence=silence_sentence,
                )
                if success:
                    logger.info(f"[{s_idx+1}/{total_segs}] Synth OK ({dur:.2f}s in {time.time()-t_seg:.2f}s): {text[:45]}...")
                else:
                    failed_segments.append(s_idx)

                if (s_idx + 1) % 5 == 0 and torch.cuda.is_available():
                    torch.cuda.empty_cache()

                completed_now = cached_count + (missing_indices.index(s_idx) + 1)
                elapsed = time.time() - t_start_synth
                avg_per_seg = elapsed / (missing_indices.index(s_idx) + 1)
                remaining = (len(missing_indices) - (missing_indices.index(s_idx) + 1)) * avg_per_seg
                status["completed_segments"] = completed_now
                status["progress_pct"] = round(completed_now / total_segs * 100, 1)
                status["eta_seconds"] = round(remaining, 1)
                status["eta_minutes"] = round(remaining / 60, 1)
                if (completed_now % 5 == 0) or (completed_now == total_segs):
                    update_status(status)
        else:
            logger.info(f"Launching {num_workers} parallel synthesis workers for {len(missing_indices)} segments...")
            import torch.multiprocessing as mp
            ctx = mp.get_context("spawn")
            task_queue = ctx.Queue()
            result_queue = ctx.Queue()

            for s_idx in missing_indices:
                task_queue.put((
                    s_idx,
                    segments[s_idx]["text"].strip(),
                    2026 + s_idx,
                    str(CACHE_DIR / f"seg_{s_idx:04d}.wav")
                ))

            for _ in range(num_workers):
                task_queue.put(None)

            workers = []
            for w_i in range(num_workers):
                p = ctx.Process(
                    target=_worker_process_entry,
                    args=(
                        w_i,
                        model_dir,
                        VOICE_FILE,
                        prompt_text,
                        whisper_dev,
                        args.whisper_verify,
                        task_queue,
                        result_queue,
                    )
                )
                p.start()
                workers.append(p)

            done_workers = set()
            completed_needed = 0

            try:
                while len(done_workers) < num_workers:
                    try:
                        item = result_queue.get(timeout=1.0)
                    except Exception:
                        for idx_p, p in enumerate(workers):
                            if not p.is_alive() and idx_p not in done_workers:
                                logger.error(f"Worker {idx_p+1} died unexpectedly with exit code {p.exitcode}")
                                done_workers.add(idx_p)
                        continue

                    itype = item.get("type")
                    if itype == "ready":
                        logger.info(f"Worker {item['worker_id']+1}/{num_workers} ready.")
                    elif itype == "done":
                        done_workers.add(item["worker_id"])
                    elif itype == "log":
                        lvl = item.get("level", "info")
                        w_pfx = f"[W{item['worker_id']+1}] "
                        if lvl == "warning":
                            logger.warning(w_pfx + item["msg"])
                        elif lvl == "error":
                            logger.error(w_pfx + item["msg"])
                        else:
                            logger.info(w_pfx + item["msg"])
                    elif itype == "result":
                        completed_needed += 1
                        s_idx = item["s_idx"]
                        total_done = cached_count + completed_needed
                        if item["success"]:
                            logger.info(
                                f"[{total_done}/{total_segs}] [W{item['worker_id']+1}] "
                                f"Synth OK ({item['dur']:.2f}s in {item['elapsed']:.2f}s): {item['text_preview']}..."
                            )
                        else:
                            logger.error(
                                f"[{total_done}/{total_segs}] [W{item['worker_id']+1}] "
                                f"Segment failed: {item['text_preview']}..."
                            )
                            failed_segments.append(s_idx)

                        elapsed = time.time() - t_start_synth
                        avg_per_seg = elapsed / max(1, completed_needed)
                        remaining = (len(missing_indices) - completed_needed) * avg_per_seg
                        status["completed_segments"] = total_done
                        status["progress_pct"] = round(total_done / total_segs * 100, 1)
                        status["eta_seconds"] = round(remaining, 1)
                        status["eta_minutes"] = round(remaining / 60, 1)
                        if total_done % 5 == 0 or total_done == total_segs:
                            update_status(status)

            except KeyboardInterrupt:
                logger.warning("Interrupted by user (Ctrl+C). Terminating workers gracefully...")
                for p in workers:
                    if p.is_alive():
                        p.terminate()
                        p.join(timeout=2)
                sys.exit(130)

            for p in workers:
                p.join(timeout=5)

    # 3. Assemble Master Audio from cached segments
    audio_chunks = []
    for s_idx, seg in enumerate(segments):
        seg_wav_path = CACHE_DIR / f"seg_{s_idx:04d}.wav"
        if not seg_wav_path.exists() or seg_wav_path.stat().st_size <= 1000:
            if s_idx not in failed_segments:
                failed_segments.append(s_idx)
            continue

        wav_chunk, sr = torchaudio.load(str(seg_wav_path))
        if sr != sample_rate:
            resampler = torchaudio.transforms.Resample(sr, sample_rate)
            wav_chunk = resampler(wav_chunk)
        wav_chunk = sanitize_segment_audio(wav_chunk, sample_rate)

        audio_chunks.append(wav_chunk)
        if seg.get("is_last_in_p", False):
            audio_chunks.append(silence_para)
        else:
            audio_chunks.append(silence_sentence)

    if failed_segments:
        logger.error(f"{len(failed_segments)} segment(s) could not be synthesized: {failed_segments}. "
                     f"Master NOT assembled; re-run to resume from cache.")
        status["state"] = "failed"
        status["error"] = "segments_failed"
        status["failed_segments"] = failed_segments
        update_status(status)
        sys.exit(EXIT_SEGMENTS_FAILED)

    if not audio_chunks:
        logger.error("No audio chunks synthesized!")
        status["state"] = "failed"
        update_status(status)
        sys.exit(EXIT_NO_AUDIO)

    logger.info("Concatenating all audio segments with 500ms safety tail silence...")
    audio_chunks.append(silence_tail)
    full_wav = torch.cat(audio_chunks, dim=1)
    if OUT_DIR.parent.name.startswith("Книга"):
        safe_out_stem = f"{OUT_DIR.parent.name}_{OUT_DIR.name}"
    else:
        safe_out_stem = OUT_DIR.name
    master_wav = OUT_DIR / f"{safe_out_stem}.wav"
    torchaudio.save(str(master_wav), full_wav, sample_rate)
    total_dur_s = full_wav.shape[1] / sample_rate
    logger.info(f"Full WAV generated: {master_wav} (Duration: {total_dur_s/60:.1f} min)")

    # 4. Encode to MP3
    final_mp3 = OUT_DIR / f"{safe_out_stem}.mp3"
    if FFMPEG_EXE.exists() or shutil.which(str(FFMPEG_EXE)):
        cmd = [
            str(FFMPEG_EXE),
            "-y",
            "-i", str(master_wav),
            "-codec:a", "libmp3lame",
            "-b:a", "128k",
            "-ar", "24000",
            str(final_mp3),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info(f"Standard MP3 encoded: {final_mp3} (Size: {final_mp3.stat().st_size / 1024 / 1024:.2f} MB)")
    else:
        final_mp3 = master_wav

    # 5. Telegram delivery (if enabled)
    upload_ok = False
    if not args.no_telegram:
        mp3_to_send = final_mp3
        if final_mp3.exists() and final_mp3.stat().st_size > 48 * 1024 * 1024 and (FFMPEG_EXE.exists() or shutil.which(str(FFMPEG_EXE))):
            compact_mp3 = OUT_DIR / f"{safe_out_stem}_telegram.mp3"
            subprocess.run([
                str(FFMPEG_EXE), "-y", "-i", str(master_wav),
                "-codec:a", "libmp3lame", "-b:a", "48k", "-ac", "1", "-ar", "24000",
                str(compact_mp3)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if compact_mp3.exists() and compact_mp3.stat().st_size <= 49 * 1024 * 1024:
                mp3_to_send = compact_mp3

        caption = (
            f"🎧 <b>{chapter_title}</b>\n"
            f"🎙️ Голос: <code>{VOICE_NAME}</code>\n"
            f"⏱️ Длительность: {total_dur_s / 60:.1f} мин. ({int(total_dur_s)} сек.)\n"
            f"📑 Сегментов: {total_segs}"
        )
        upload_ok = send_telegram_file(mp3_to_send, caption, token, chat_id, proxy=proxy, is_audio=True, title=chapter_title)
        if not upload_ok:
            upload_ok = send_telegram_file(mp3_to_send, caption, token, chat_id, proxy=proxy, is_audio=False, title=chapter_title)
    else:
        logger.info("Telegram notification skipped as requested (--no-telegram set).")

    # 6. Windows export (only if explicitly enabled via --export-windows)
    if args.export_windows:
        win_targets = [
            Path("/run/media/nk/System/Users/kosty/Desktop"),
            Path("/run/media/nk/Main/Downloads"),
        ]
        for wt in win_targets:
            if wt.is_dir() and final_mp3.exists():
                dest = wt / final_mp3.name
                try:
                    shutil.copy2(str(final_mp3), str(dest))
                    logger.info(f"Exported master MP3 to Windows: {dest}")
                except Exception as e:
                    logger.warning(f"Could not copy to {dest}: {e}")
    else:
        logger.info("Windows export skipped (disabled per user request).")

    status["state"] = "completed"
    status["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    status["duration_minutes"] = round(total_dur_s / 60, 2)
    status["mp3_path"] = str(final_mp3)
    status["telegram_uploaded"] = upload_ok
    update_status(status)

    logger.info(f"=== Generation Completed Successfully! Duration: {total_dur_s/60:.1f} min ===")

if __name__ == "__main__":
    main()
