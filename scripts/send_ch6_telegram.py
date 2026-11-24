#!/usr/bin/env python3
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
sys.path.insert(0, str(ROOT_DIR))

from scripts.generate_chapter_vid_voice import get_telegram_config, get_telegram_opener

CH6_DIR = ROOT_DIR / "output" / "Книга_2_Тайная_комната" / "Глава_06"
compact_mp3 = CH6_DIR / "Книга_2_Тайная_комната_Глава_06_telegram.mp3"

if not compact_mp3.exists():
    print(f"File not found: {compact_mp3}")
    sys.exit(1)

token, chat_id, proxy = get_telegram_config()
chapter_title = "Змея, Лев и Выдра. Книга 2: Тайная комната. Глава 6"
caption = (
    f"🎧 <b>{chapter_title}</b>\n"
    f"🎙️ Голос: <code>VID_20261101_122350_289</code>\n"
    f"⏱️ Длительность: 120.6 мин.\n"
    f"📑 Сегментов: 646"
)

def send_file(file_path: Path, caption: str, token: str, chat_id: int, proxy: str = None, is_audio: bool = True):
    url = f"https://api.telegram.org/bot{token}/{'sendAudio' if is_audio else 'sendDocument'}"
    boundary = "----WebKitFormBoundaryAudio" + str(int(time.time()))
    file_bytes = file_path.read_bytes()
    filename = file_path.name
    field_name = "audio" if is_audio else "document"

    body_parts = []
    body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
    body_parts.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode("utf-8"))
    if is_audio:
        body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
        body_parts.append(f'Content-Disposition: form-data; name="title"\r\n\r\n{chapter_title}\r\n'.encode("utf-8"))
        body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
        body_parts.append(f'Content-Disposition: form-data; name="performer"\r\n\r\nCosyVoice 3 (VID_20261101_122350_289)\r\n'.encode("utf-8"))
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
        with opener.open(req, timeout=360) as resp:
            print(f"Telegram upload OK: {filename}")
            return True
    except Exception as e:
        print(f"Upload failed: {e}")
        return False

print(f"Sending {compact_mp3.name} ({compact_mp3.stat().st_size / 1024 / 1024:.2f} MB) to Telegram...")
ok = send_file(compact_mp3, caption, token, chat_id, proxy, is_audio=True)
if not ok:
    ok = send_file(compact_mp3, caption, token, chat_id, proxy, is_audio=False)
print("Finished. Result:", ok)
