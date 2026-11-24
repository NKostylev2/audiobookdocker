#!/usr/bin/env python3
"""
Full parser and downloader for 'Wide Awake' (Сумерки: Белла и Эдвард).
Fetches all chapters, epilogue, and outtakes from robsten.ru,
caches raw HTML, splits into POV sections, and exports:
1. Beautiful Markdown files for reading and dubbing in output/_Архив_Сумерки_Wide_Awake/texts/
2. Structured JSON for pipelines in output/_Архив_Сумерки_Wide_Awake/parsed_json/
3. Master catalog README.md and all_chapters.json with statistics.
"""

import os
import re
import sys
import json
import time
import logging
from pathlib import Path
from bs4 import BeautifulSoup
import requests

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
sys.path.insert(0, str(ROOT_DIR))

from fanfic_pipeline.censor import censor_text
from fanfic_pipeline.robsten_scraper import get_toc, fetch_page_text, clean_and_split_sections

OUTPUT_BASE = ROOT_DIR / "output" / "_Архив_Сумерки_Wide_Awake"
TEXTS_DIR = OUTPUT_BASE / "texts"
JSON_DIR = OUTPUT_BASE / "parsed_json"

TEXTS_DIR.mkdir(parents=True, exist_ok=True)
JSON_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("WideAwakeParser")


def sanitize_filename(name: str) -> str:
    """Sanitize string for filename."""
    s = re.sub(r'[\\/*?:"<>|]', "", name)
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s)
    return s.strip("._")


def get_entry_title(entry: dict) -> str:
    """Get clean title for TOC entry."""
    title = entry.get("title", "").strip()
    if not title or title.lower() in ["new !", "new!"]:
        if entry.get("links"):
            title = entry["links"][0].get("part_name", "").strip()
    # Strip any trailing punctuation
    title = re.sub(r"^[\s\.\:\-]+|[\s\.\:\-]+$", "", title)
    return title


def format_pov_name(pov: str) -> str:
    mapping = {
        "bella": "БЕЛЛА",
        "edward": "ЭДВАРД",
        "carlisle": "КАРЛАЙЛ",
        "elizabeth": "ЭЛИЗАБЕТ МЕЙСЕН",
    }
    return mapping.get(pov.lower(), pov.upper())


def process_all_chapters():
    t0 = time.time()
    toc = get_toc(force_refresh=True)
    logger.info(f"Loaded TOC with {len(toc)} entries.")

    catalog_entries = []
    total_words_all = 0
    total_chars_all = 0

    for idx, entry in enumerate(toc, 1):
        header = entry.get("header", "").strip()
        title = get_entry_title(entry)
        start_num = entry.get("start_num", 0)
        end_num = entry.get("end_num", 0)
        links = entry.get("links", [])

        # Construct file base name
        if start_num == 999:
            base_slug = "Эпилог"
            display_name = "Эпилог"
            if title:
                base_slug += f"_{sanitize_filename(title)}"
                display_name += f". {title}"
        elif start_num >= 1000:
            outtake_num = start_num - 1000
            base_slug = f"Ауттейк_{outtake_num:02d}"
            display_name = f"Ауттейк {outtake_num}"
            if title:
                base_slug += f"_{sanitize_filename(title)}"
                display_name += f". {title}"
        else:
            if start_num == end_num:
                base_slug = f"Глава_{start_num:02d}"
                display_name = f"Глава {start_num}"
            else:
                base_slug = f"Глава_{start_num:02d}-{end_num:02d}"
                display_name = f"Глава {start_num}-{end_num}"
            if title:
                base_slug += f"_{sanitize_filename(title)}"
                display_name += f". {title}"

        logger.info(f"[{idx}/{len(toc)}] Processing: {display_name} ({len(links)} parts)...")

        # Fetch HTML snippets
        html_snippets = []
        for l_idx, link in enumerate(links):
            url = link["url"]
            # fetch_page_text uses cache if available; add tiny sleep only if uncached
            html = fetch_page_text(url)
            html_snippets.append(html)
            time.sleep(0.15)  # polite rate-limiting

        # Determine default initial POV
        title_header = f"{header} {title}"
        if any(k in title_header for k in ["Эдвард POV", "от лица Эдварда"]):
            default_pov = "edward"
        elif any(k in title_header for k in ["Карлайл", "от лица Карлайла"]):
            default_pov = "carlisle"
        elif any(k in title_header for k in ["Элизабет", "от лица Элизабет"]):
            default_pov = "elizabeth"
        else:
            default_pov = "bella"

        # Split into sections
        raw_sections = clean_and_split_sections(html_snippets, default_pov=default_pov)

        # Filter out trivial sections that are just leftover titles (<120 chars matching title/banner)
        sections = []
        for s in raw_sections:
            text = s["text"].strip()
            if len(text) < 120 and any(kw in text.lower() for kw in ["wide awake", "глава", "ауттейк", "эпилог", "ginger snappy", "double-stuf"]):
                continue
            if not text:
                continue
            sections.append(s)

        # Apply censor for dubbing (also preserve raw if needed)
        processed_sections = []
        entry_total_chars = 0
        entry_total_words = 0

        for sec_i, sec in enumerate(sections, 1):
            text = sec["text"].strip()
            # Split paragraphs
            paras = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
            censored_paras = []
            sec_censored_words = []
            for p in paras:
                cp, reps = censor_text(p)
                censored_paras.append(cp)
                if reps:
                    sec_censored_words.extend(reps)

            censored_text = "\n\n".join(censored_paras)
            words = len(re.findall(r"\b\w+\b", censored_text))
            chars = len(censored_text)

            entry_total_words += words
            entry_total_chars += chars

            processed_sections.append({
                "section_index": sec_i,
                "pov": sec["pov"],
                "pov_display": format_pov_name(sec["pov"]),
                "char_count": chars,
                "word_count": words,
                "paragraph_count": len(censored_paras),
                "censored_replacements_count": len(sec_censored_words),
                "paragraphs": censored_paras,
                "text": censored_text,
            })

        total_words_all += entry_total_words
        total_chars_all += entry_total_chars
        est_minutes = round(entry_total_words / 130.0, 1) if entry_total_words else 0.0

        # Construct JSON record
        chapter_record = {
            "index": idx,
            "header": header,
            "title": title,
            "display_name": display_name,
            "start_num": start_num,
            "end_num": end_num,
            "total_chars": entry_total_chars,
            "total_words": entry_total_words,
            "estimated_audio_minutes": est_minutes,
            "sections_count": len(processed_sections),
            "pov_sequence": [s["pov_display"] for s in processed_sections],
            "links": links,
            "sections": processed_sections,
        }

        # Save individual JSON
        json_file = JSON_DIR / f"{base_slug}.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(chapter_record, f, ensure_ascii=False, indent=2)

        # Save Markdown file
        md_file = TEXTS_DIR / f"{base_slug}.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(f"# {display_name}\n\n")
            f.write(f"> **Характеристики:** {entry_total_words:,} слов | {entry_total_chars:,} знаков | ~{est_minutes} мин. звучания\n")
            f.write(f"> **POV структура:** {' → '.join(s['pov_display'] for s in processed_sections)}\n\n")
            f.write("---\n\n")

            for s in processed_sections:
                f.write(f"## [{s['pov_display']}]\n\n")
                f.write(s["text"])
                f.write("\n\n---\n\n")

        pov_summary = " → ".join(s["pov_display"] for s in processed_sections)
        catalog_entries.append({
            "index": idx,
            "display_name": display_name,
            "file_name": md_file.name,
            "words": entry_total_words,
            "chars": entry_total_chars,
            "minutes": est_minutes,
            "sections_count": len(processed_sections),
            "povs": pov_summary,
        })
        logger.info(f"  ✓ Saved {md_file.name}: {entry_total_words:,} words, {len(processed_sections)} sections ({pov_summary})")

    # Save Master JSON
    all_json_path = JSON_DIR / "all_chapters.json"
    master_data = {
        "work_title": "Сумерки: Wide Awake",
        "source": "robsten.ru",
        "total_entries": len(catalog_entries),
        "total_words": total_words_all,
        "total_chars": total_chars_all,
        "estimated_total_audio_hours": round(total_words_all / 130.0 / 60.0, 2),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chapters": catalog_entries,
    }
    with open(all_json_path, "w", encoding="utf-8") as f:
        json.dump(master_data, f, ensure_ascii=False, indent=2)

    # Save Master Catalog README.md
    readme_path = TEXTS_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("# «Сумерки: Wide Awake» — Каталог всех глав\n\n")
        f.write(f"- **Всего произведений/глав:** {len(catalog_entries)} (52 главы, Эпилог, 6 Ауттейков)\n")
        f.write(f"- **Общий объем текста:** {total_words_all:,} слов / {total_chars_all:,} символов\n")
        f.write(f"- **Примерная общая длительность озвучки:** ~{master_data['estimated_total_audio_hours']} часов\n")
        f.write(f"- **Форматы:** Markdown (`texts/*.md`), JSON (`parsed_json/*.json`)\n\n")
        f.write("| № | Глава | POV структура | Слов | Знаков | Время (~мин) | Файл |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for e in catalog_entries:
            f.write(f"| {e['index']} | **{e['display_name']}** | `{e['povs']}` | {e['words']:,} | {e['chars']:,} | ~{e['minutes']} | [{e['file_name']}](./{e['file_name']}) |\n")

    logger.info(f"=== ALL DONE in {time.time() - t0:.1f}s! ===")
    logger.info(f"Total entries: {len(catalog_entries)}, Total words: {total_words_all:,}, Total chars: {total_chars_all:,}")
    logger.info(f"Markdown catalog: {readme_path}")
    logger.info(f"JSON catalog: {all_json_path}")


if __name__ == "__main__":
    process_all_chapters()
