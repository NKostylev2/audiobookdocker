#!/usr/bin/env python3
import os
import sys
import subprocess
from pathlib import Path

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
sys.path.insert(0, str(ROOT_DIR))

from scripts.generate_chapter_vid_voice import get_telegram_config, send_telegram_file

CH2_DIR = ROOT_DIR / "output" / "Книга_2_Тайная_комната" / "Глава_02"
master_wav = CH2_DIR / "chamber_of_secrets_ch02_vid_voice.wav"
compact_mp3 = CH2_DIR / "Книга_2_Тайная_комната_Глава_02_telegram.mp3"

if not compact_mp3.exists() or compact_mp3.stat().st_size < 1000:
    print(f"Encoding compact 48k MP3 for Telegram from {master_wav}...")
    cmd = [
        "ffmpeg", "-y", "-i", str(master_wav),
        "-codec:a", "libmp3lame", "-b:a", "48k", "-ac", "1", "-ar", "24000",
        str(compact_mp3)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

size_mb = compact_mp3.stat().st_size / 1024 / 1024
print(f"Telegram MP3 ready: {compact_mp3} ({size_mb:.2f} MB)")

token, chat_id, proxy = get_telegram_config()
chapter_title = "Змея, Лев и Выдра. Книга 2: Тайная комната. Глава 2"
caption = (
    f"🎧 <b>{chapter_title}</b>\n"
    f"🎙️ Голос: <code>VID_20261101_122350_289</code>\n"
    f"⏱️ Длительность: 109.4 мин.\n"
    f"📑 Сегментов: 463"
)

print(f"Sending to Telegram chat_id={chat_id}...")
ok = send_telegram_file(compact_mp3, caption, token, chat_id, proxy=proxy, is_audio=True, title=chapter_title)
if not ok:
    ok = send_telegram_file(compact_mp3, caption, token, chat_id, proxy=proxy, is_audio=False, title=chapter_title)

if ok:
    print("Telegram upload SUCCESS!")
else:
    print("Telegram upload FAILED!")
