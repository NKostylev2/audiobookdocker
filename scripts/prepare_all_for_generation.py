#!/usr/bin/env python3
"""
Prepares all 56 chapters of 'Сумерки: Wide Awake' for CosyVoice 3 synthesis:
1. Loads Qwen2 BlankEN tokenizer on CPU.
2. For each chapter:
   - Prepends spoken section announcements ('Глава первая... Часть первая. Белла').
   - Splits into balanced 35-65 token blocks (SentenceSegmenter).
   - Sanitizes non-terminal punctuation (;, ,, :, — -> .).
   - Verifies 100% word count and word order preservation.
   - Computes target ending words and stems for Whisper in-flight verification.
   - Assigns voice profiles, reference WAVs, prompts (<|endofprompt|>), and seeds.
   - Configures audio mastering (200ms sentence, 500ms paragraph, 1500ms POV, 500ms tail, 24kHz mono).
3. Exports:
   - output/_Архив_Сумерки_Wide_Awake/prepared_chapters/<chapter_dir>/manifest.json
   - output/_Архив_Сумерки_Wide_Awake/prepared_chapters/<chapter_dir>/<sec_name>/segments.json
   - output/_Архив_Сумерки_Wide_Awake/prepared_chapters/<chapter_dir>/<sec_name>/paragraphs.txt
   - output/_Архив_Сумерки_Wide_Awake/prepared_chapters/queue.json
"""

import os
import sys
import re
import json
import time
import logging
from pathlib import Path
from transformers import AutoTokenizer

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
sys.path.insert(0, str(ROOT_DIR))

from fanfic_pipeline.segmenter import SentenceSegmenter
from fanfic_pipeline.number_words import get_section_announcement
from scripts.generate_chapter_vid_voice import validate_and_prepare_segments

OUTPUT_BASE = ROOT_DIR / "output" / "_Архив_Сумерки_Wide_Awake"
PARSED_JSON_DIR = OUTPUT_BASE / "parsed_json"
PREPARED_DIR = OUTPUT_BASE / "prepared_chapters"
VOICES_DIR = OUTPUT_BASE / "voices"

PREPARED_DIR.mkdir(parents=True, exist_ok=True)
VOICES_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("WideAwakePrep")

# Voice Profiles
VOICE_PROFILES = {
    "bella": {
        "character": "Белла Свон",
        "wav": str(VOICES_DIR / "female_03_ru.wav"),
        "ref_text": "Темнота, заброшенность, одиночество, удушье, скрывались в каждом углу и наблюдали за мной исподтишка.",
        "prompt_text": "You are a helpful assistant.<|endofprompt|>Темнота, заброшенность, одиночество, удушье, скрывались в каждом углу и наблюдали за мной исподтишка.",
        "base_seed": 5026,
    },
    "edward": {
        "character": "Эдвард Каллен",
        "wav": str(VOICES_DIR / "male_05_ru.wav"),
        "ref_text": "Я ненавидел смотреть, как она мучается каждую ночь от кошмаров. Я просто не мог оторвать от неё взгляда.",
        "prompt_text": "You are a helpful assistant.<|endofprompt|>Я ненавидел смотреть, как она мучается каждую ночь от кошмаров. Я просто не мог оторвать от неё взгляда.",
        "base_seed": 6026,
    },
    "carlisle": {
        "character": "Карлайл Каллен",
        "wav": str(VOICES_DIR / "male_05_ru.wav"),
        "ref_text": "Я ненавидел смотреть, как она мучается каждую ночь от кошмаров. Я просто не мог оторвать от неё взгляда.",
        "prompt_text": "You are a helpful assistant.<|endofprompt|>Я ненавидел смотреть, как она мучается каждую ночь от кошмаров. Я просто не мог оторвать от неё взгляда.",
        "base_seed": 7026,
    },
    "elizabeth": {
        "character": "Элизабет Мейсен",
        "wav": str(VOICES_DIR / "female_03_ru.wav"),
        "ref_text": "Темнота, заброшенность, одиночество, удушье, скрывались в каждом углу и наблюдали за мной исподтишка.",
        "prompt_text": "You are a helpful assistant.<|endofprompt|>Темнота, заброшенность, одиночество, удушье, скрывались в каждом углу и наблюдали за мной исподтишка.",
        "base_seed": 8026,
    },
}


def sanitize_filename(name: str) -> str:
    s = re.sub(r'[\\/*?:"<>|]', "", name)
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s)
    return s.strip("._")


def compute_target_ending_words(text: str) -> dict:
    words = re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]+', text.lower())
    if not words:
        return {"words": [], "stems": []}
    targets = words[-2:] if len(words) >= 2 else words[-1:]
    stems = [w[:max(3, len(w)-2)] for w in targets]
    return {
        "words": targets,
        "stems": stems,
    }


def prepare_all_chapters():
    t0 = time.time()
    tok_path = str(ROOT_DIR / "CosyVoice" / "pretrained_models" / "Fun-CosyVoice3-0.5B" / "CosyVoice-BlankEN")
    logger.info(f"Loading Qwen2 BlankEN tokenizer from {tok_path} on CPU...")
    tokenizer = AutoTokenizer.from_pretrained(tok_path)
    logger.info("Tokenizer loaded successfully.")

    segmenter = SentenceSegmenter(
        tokenizer=tokenizer,
        min_tokens=35,
        max_tokens=65,
        min_block_tokens=25,
    )

    all_chapters_file = PARSED_JSON_DIR / "all_chapters.json"
    with open(all_chapters_file, "r", encoding="utf-8") as f:
        master_catalog = json.load(f)

    catalog_entries = master_catalog["chapters"]
    logger.info(f"Found {len(catalog_entries)} chapters in catalog to prepare.")

    queue_entries = []
    total_segments_all = 0
    total_words_all = 0

    for idx, cat_entry in enumerate(catalog_entries, 1):
        file_base = cat_entry["file_name"].replace(".md", "")
        parsed_json_file = PARSED_JSON_DIR / f"{file_base}.json"
        if not parsed_json_file.exists():
            logger.warning(f"File not found: {parsed_json_file}, skipping...")
            continue

        with open(parsed_json_file, "r", encoding="utf-8") as f:
            chapter_data = json.load(f)

        header = chapter_data.get("header", "")
        title = chapter_data.get("title", "")
        display_name = chapter_data.get("display_name", "")
        start_num = chapter_data.get("start_num", 0)
        end_num = chapter_data.get("end_num", 0)
        sections = chapter_data.get("sections", [])

        ch_dir_name = sanitize_filename(file_base)
        ch_dir = PREPARED_DIR / ch_dir_name
        ch_dir.mkdir(parents=True, exist_ok=True)

        prepared_sections = []
        ch_total_segs = 0
        ch_total_words = 0

        for sec_idx, sec in enumerate(sections):
            pov = sec["pov"].lower()
            voice_prof = VOICE_PROFILES.get(pov, VOICE_PROFILES["bella"])
            announcement = get_section_announcement(
                chapter_num=start_num,
                sec_idx=sec_idx,
                pov=pov,
                start_num=start_num,
                end_num=end_num,
                chapter_title=title,
            )

            # Combine announcement with text paragraphs
            content_paras = sec.get("paragraphs", [])
            full_paras = [announcement] + content_paras

            # Validate and segment
            segs = validate_and_prepare_segments(full_paras, segmenter)

            # Enrich segments with verification targets and global metadata
            enriched_segs = []
            sec_words = 0
            for s_i, seg in enumerate(segs):
                txt = seg["text"].strip()
                tokens = seg["tokens"]
                is_last = seg["is_last_in_p"]
                p_idx = seg["p_idx"]
                ending_targets = compute_target_ending_words(txt)

                w_count = len(re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]+', txt))
                sec_words += w_count

                enriched_segs.append({
                    "seg_index": s_i,
                    "p_idx": p_idx,
                    "s_idx": s_i,
                    "text": txt,
                    "tokens": tokens,
                    "is_last_in_p": is_last,
                    "word_count": w_count,
                    "target_ending_words": ending_targets["words"],
                    "target_stems": ending_targets["stems"],
                })

            sec_id = f"sec{sec_idx+1:02d}_{pov}"
            sec_dir = ch_dir / sec_id
            sec_dir.mkdir(parents=True, exist_ok=True)

            # Save section files
            with open(sec_dir / "paragraphs.txt", "w", encoding="utf-8") as f:
                f.write("\n\n".join(full_paras))

            with open(sec_dir / "segments.json", "w", encoding="utf-8") as f:
                json.dump(enriched_segs, f, ensure_ascii=False, indent=2)

            ch_total_segs += len(enriched_segs)
            ch_total_words += sec_words

            prepared_sections.append({
                "sec_id": sec_id,
                "sec_index": sec_idx,
                "pov": pov,
                "pov_display": sec.get("pov_display", pov.upper()),
                "character": voice_prof["character"],
                "announcement": announcement,
                "voice": {
                    "wav": voice_prof["wav"],
                    "ref_text": voice_prof["ref_text"],
                    "prompt_text": voice_prof["prompt_text"],
                    "base_seed": voice_prof["base_seed"],
                },
                "total_segments": len(enriched_segs),
                "total_words": sec_words,
                "paragraphs_count": len(full_paras),
                "segments_file": f"{sec_id}/segments.json",
                "paragraphs_file": f"{sec_id}/paragraphs.txt",
            })

        total_segments_all += ch_total_segs
        total_words_all += ch_total_words

        # Master audio target paths
        wav_target = ch_dir / f"{ch_dir_name}.wav"
        mp3_target = ch_dir / f"{ch_dir_name}.mp3"

        # Chapter Manifest
        manifest = {
            "chapter_index": idx,
            "chapter_slug": ch_dir_name,
            "header": header,
            "title": title,
            "display_name": display_name,
            "start_num": start_num,
            "end_num": end_num,
            "status": "ready",
            "total_sections": len(prepared_sections),
            "total_segments": ch_total_segs,
            "total_words": ch_total_words,
            "estimated_minutes": round(ch_total_words / 130.0, 1),
            "mastering": {
                "sample_rate": 24000,
                "channels": 1,
                "pause_sentence_ms": 200,
                "pause_paragraph_ms": 500,
                "pause_pov_ms": 1500,
                "pause_tail_ms": 500,
                "mp3_bitrate_kbps": 128,
                "target_wav": str(wav_target),
                "target_mp3": str(mp3_target),
            },
            "sections": prepared_sections,
        }

        with open(ch_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        queue_entries.append({
            "index": idx,
            "chapter_slug": ch_dir_name,
            "display_name": display_name,
            "start_num": start_num,
            "end_num": end_num,
            "status": "ready",
            "total_sections": len(prepared_sections),
            "total_segments": ch_total_segs,
            "total_words": ch_total_words,
            "estimated_minutes": round(ch_total_words / 130.0, 1),
            "manifest_file": f"{ch_dir_name}/manifest.json",
            "dir_path": str(ch_dir),
        })

        logger.info(f"[{idx}/{len(catalog_entries)}] ✓ Prepared {display_name}: {ch_total_segs} segments, {ch_total_words:,} words across {len(prepared_sections)} sections.")

    # Save Queue Manifest
    queue_file = PREPARED_DIR / "queue.json"
    queue_data = {
        "work_title": "Сумерки: Wide Awake",
        "total_chapters": len(queue_entries),
        "total_segments": total_segments_all,
        "total_words": total_words_all,
        "estimated_total_hours": round(total_words_all / 130.0 / 60.0, 2),
        "prepared_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "queue": queue_entries,
    }
    with open(queue_file, "w", encoding="utf-8") as f:
        json.dump(queue_data, f, ensure_ascii=False, indent=2)

    # Save Queue README
    readme_file = PREPARED_DIR / "README.md"
    with open(readme_file, "w", encoding="utf-8") as f:
        f.write("# Очередь готовых к генерации глав «Сумерки: Wide Awake»\n\n")
        f.write(f"- **Всего глав/произведений в очереди:** {len(queue_entries)}\n")
        f.write(f"- **Всего подготовленных речевых сегментов:** {total_segments_all:,}\n")
        f.write(f"- **Суммарный объем слов:** {total_words_all:,}\n")
        f.write(f"- **Расчетное время звучания:** ~{queue_data['estimated_total_hours']} часов\n\n")
        f.write("Каждая глава содержит полностью отвалидированный `manifest.json`, готовые 35–65 токенные сегменты, назначенные голоса, сиды, паузы и целевые окончания для проверки Whisper.\n\n")
        f.write("| № | Глава | Секций | Сегментов | Слов | Время (~мин) | Папка |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for q in queue_entries:
            f.write(f"| {q['index']} | **{q['display_name']}** | {q['total_sections']} | {q['total_segments']:,} | {q['total_words']:,} | ~{q['estimated_minutes']} | [`{q['chapter_slug']}`](./{q['chapter_slug']}) |\n")

    logger.info(f"=== ALL CHAPTERS PREPARED in {time.time()-t0:.1f}s ===")
    logger.info(f"Total chapters: {len(queue_entries)}, Total segments: {total_segments_all:,}, Total words: {total_words_all:,}")
    logger.info(f"Queue manifest: {queue_file}")


if __name__ == "__main__":
    prepare_all_chapters()
