#!/usr/bin/env python3
"""
AO3 Work Parser and Chapter Downloader / Extractor.
Parses full work HTML from Archive of Our Own, extracts metadata,
cleans paragraph text according to project audio-pipeline standards,
and exports:
1. Formatted Markdown files: output/<Work_Title>/texts/
2. Clean Raw TXT files:      output/<Work_Title>/texts_raw_txt/
3. Structured JSON files:     output/<Work_Title>/parsed_json/
4. Master Catalog README.md and all_chapters.json
"""

import os
import re
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from bs4 import BeautifulSoup

ROOT_DIR = Path("/mnt/ai/AI-Dubbing")
sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("AO3Parser")


def sanitize_filename(name: str) -> str:
    """Sanitize string for Windows and Linux safe filenames."""
    s = re.sub(r'[\\/*?:"<>|]', "", name)
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s)
    return s.strip("._")


def clean_text_paragraph(text: str) -> str:
    """Normalize paragraph text: collapse internal whitespace/newlines to single space."""
    return re.sub(r"\s+", " ", text).strip()


def parse_work(html_path: Path, nav_path: Path = None, output_dir: Path = None):
    t0 = time.time()
    logger.info(f"Reading HTML: {html_path}")

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, "html.parser")

    # Load navigation metadata (dates, URLs) if available
    nav_map = {}
    if nav_path and nav_path.exists():
        with open(nav_path, "r", encoding="utf-8") as f:
            nav_data = json.load(f)
            nav_map = {item["index"]: item for item in nav_data}

    # Extract Work Metadata
    title_el = soup.select_one("h2.title")
    title = title_el.get_text(strip=True) if title_el else "Unknown Work"

    author_el = soup.select_one("a[rel=\"author\"]")
    author = author_el.get_text(strip=True) if author_el else "Anonymous"
    author_url = f"https://archiveofourown.org{author_el['href']}" if author_el and "href" in author_el.attrs else ""

    fandoms = [a.get_text(strip=True) for a in soup.select("dd.fandom a")]
    rating = soup.select_one("dd.rating").get_text(strip=True) if soup.select_one("dd.rating") else ""
    warnings = [a.get_text(strip=True) for a in soup.select("dd.warning a")]
    categories = [a.get_text(strip=True) for a in soup.select("dd.category a")]
    relationships = [a.get_text(strip=True) for a in soup.select("dd.relationship a")]
    characters = [a.get_text(strip=True) for a in soup.select("dd.character a")]
    tags = [a.get_text(strip=True) for a in soup.select("dd.freeform a")]
    language = soup.select_one("dd.language").get_text(strip=True) if soup.select_one("dd.language") else "English"

    # Work Summary
    summary_el = soup.select_one(".work.meta .summary blockquote, #workskin > .preface .summary blockquote")
    summary = summary_el.get_text(separator="\n\n", strip=True) if summary_el else ""

    # Work Notes
    work_notes_el = soup.select_one("#workskin > .preface .notes blockquote")
    work_notes = work_notes_el.get_text(separator="\n\n", strip=True) if work_notes_el else ""

    if output_dir is None:
        folder_name = sanitize_filename(title)
        output_dir = ROOT_DIR / "output" / folder_name

    texts_dir = output_dir / "texts"
    raw_txt_dir = output_dir / "texts_raw_txt"
    json_dir = output_dir / "parsed_json"

    texts_dir.mkdir(parents=True, exist_ok=True)
    raw_txt_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    # Locate chapter containers
    chapters = soup.select("#chapters > div.chapter")
    if not chapters:
        # Single-chapter work fallback
        userstuff = soup.select_one("#chapters .userstuff, #workskin .userstuff")
        if userstuff:
            chapters = [userstuff]

    logger.info(f"Found {len(chapters)} chapters in '{title}' by {author}")

    catalog_entries = []
    total_words_all = 0
    total_chars_all = 0
    total_paras_all = 0

    for idx, ch in enumerate(chapters, 1):
        # Extract title & subtitle
        h3 = ch.select_one("h3.title")
        raw_h3 = h3.get_text(separator=" ", strip=True) if h3 else f"Chapter {idx}"
        raw_h3 = re.sub(r"\s+", " ", raw_h3)

        # Nav info
        nav_info = nav_map.get(idx, {})
        nav_date = nav_info.get("date", "")
        nav_url = nav_info.get("url", "")

        # Try to parse subtitle if "Chapter X: Subtitle"
        match = re.search(r"^Chapter\s+\d+\s*[:\-–—]\s*(.*)$", raw_h3, re.IGNORECASE)
        subtitle = match.group(1).strip() if match else raw_h3
        subtitle_clean = re.sub(r'^[\\/*?:"<>|\s]+|[\\/*?:"<>|\s]+$', "", subtitle)
        if not subtitle_clean:
            subtitle_clean = f"Chapter_{idx:02d}"

        slug = f"Chapter_{idx:02d}_{sanitize_filename(subtitle_clean)}"
        txt_filename = f"{idx:02d}. Chapter {idx:02d} - {subtitle_clean}.txt"
        txt_filename_safe = re.sub(r'[\\/*?:"<>|]', "", txt_filename)

        # Extract Notes and Summary for this chapter
        prefaces = ch.select(".chapter.preface")
        notes_before = ""
        notes_after = ""
        ch_summary = ""

        article = ch.select_one(".userstuff[role=\"article\"]")
        if not article:
            article = ch.select_one(".userstuff") or ch

        for pref in prefaces:
            # Check if this preface is before or after the article in DOM tree
            is_before = True
            if article and pref in article.find_all_next():
                # pref comes after article start, check if it's after article end
                if pref not in article.descendants:
                    is_before = False

            s_el = pref.select_one(".summary blockquote, .summary")
            if s_el:
                ch_summary = s_el.get_text(separator="\n\n", strip=True)

            n_el = pref.select_one(".notes blockquote, .notes")
            if n_el:
                n_text = n_el.get_text(separator="\n\n", strip=True)
                # Strip leading "Notes:" label if repeated
                n_text = re.sub(r"^Notes:\s*", "", n_text).strip()
                if is_before:
                    notes_before = n_text
                else:
                    notes_after = n_text

        # Extract Paragraphs
        paras = []
        for p in article.find_all("p"):
            # Replace <br> with spaces to avoid joining words
            for br in p.find_all("br"):
                br.replace_with(" ")
            cleaned = clean_text_paragraph(p.get_text())
            if cleaned:
                paras.append(cleaned)

        full_chapter_text = "\n\n".join(paras)
        words = sum(len(re.findall(r"\b\w+\b", p)) for p in paras)
        chars = len(full_chapter_text)
        est_minutes = round(words / 130.0, 1) if words else 0.0

        total_words_all += words
        total_chars_all += chars
        total_paras_all += len(paras)

        # JSON record
        chapter_record = {
            "work_title": title,
            "chapter_index": idx,
            "chapter_number": idx,
            "heading": raw_h3,
            "subtitle": subtitle,
            "published_date": nav_date,
            "ao3_url": nav_url,
            "word_count": words,
            "char_count": chars,
            "paragraph_count": len(paras),
            "estimated_audio_minutes": est_minutes,
            "summary": ch_summary,
            "notes_before": notes_before,
            "notes_after": notes_after,
            "paragraphs": paras,
            "full_text": full_chapter_text
        }

        json_file = json_dir / f"{slug}.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(chapter_record, f, ensure_ascii=False, indent=2)

        # Raw TXT (story text only, double newlines)
        txt_file = raw_txt_dir / txt_filename_safe
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write(full_chapter_text)

        # Formatted Markdown
        md_file = texts_dir / f"{slug}.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(f"# {raw_h3}\n\n")
            meta_line = f"> **Характеристики:** {words:,} слов | {chars:,} знаков | ~{est_minutes} мин. звучания"
            if nav_date:
                meta_line += f" | Дата: {nav_date}"
            if nav_url:
                meta_line += f" | [AO3 Ссылка]({nav_url})"
            f.write(f"{meta_line}\n\n")

            if ch_summary:
                f.write(f"> **Описание (Summary):**\n> {ch_summary.replace(chr(10), chr(10) + '> ')}\n\n")

            if notes_before:
                f.write(f"<details><summary><b>Примечания автора (Начало)</b></summary>\n\n{notes_before}\n\n</details>\n\n")

            f.write("---\n\n")
            f.write(full_chapter_text)
            f.write("\n\n---\n\n")

            if notes_after:
                f.write(f"<details><summary><b>Примечания автора (Конец)</b></summary>\n\n{notes_after}\n\n</details>\n\n")

        catalog_entries.append({
            "index": idx,
            "heading": raw_h3,
            "subtitle": subtitle,
            "date": nav_date,
            "words": words,
            "chars": chars,
            "paragraphs": len(paras),
            "minutes": est_minutes,
            "md_file": md_file.name,
            "txt_file": txt_file.name,
            "json_file": json_file.name,
            "ao3_url": nav_url,
        })

        if idx % 5 == 0 or idx == len(chapters):
            logger.info(f"[{idx:02d}/{len(chapters)}] Parsed {raw_h3}: {words:,} words, {chars:,} chars (~{est_minutes} min)")

    est_total_hours = round(total_words_all / 130.0 / 60.0, 2)

    # Master JSON
    master_json_path = json_dir / "all_chapters.json"
    master_data = {
        "work_title": title,
        "author": author,
        "author_url": author_url,
        "work_url": "https://archiveofourown.org/works/23863018",
        "fandoms": fandoms,
        "rating": rating,
        "warnings": warnings,
        "categories": categories,
        "relationships": relationships,
        "characters": characters,
        "tags": tags,
        "language": language,
        "summary": summary,
        "work_notes": work_notes,
        "total_chapters": len(catalog_entries),
        "total_paragraphs": total_paras_all,
        "total_words": total_words_all,
        "total_chars": total_chars_all,
        "estimated_total_audio_hours": est_total_hours,
        "parsed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chapters": catalog_entries
    }

    with open(master_json_path, "w", encoding="utf-8") as f:
        json.dump(master_data, f, ensure_ascii=False, indent=2)

    # Master Catalog README.md
    readme_path = texts_dir / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(f"# «{title}» — Каталог всех глав\n\n")
        f.write(f"- **Автор:** [{author}]({author_url})\n")
        f.write(f"- **Фэндом:** {', '.join(fandoms)}\n")
        f.write(f"- **Рейтинг:** {rating} | **Язык:** {language}\n")
        f.write(f"- **Предупреждения:** {', '.join(warnings)}\n")
        f.write(f"- **Персонажи:** {', '.join(characters[:10])}...\n")
        f.write(f"- **Всего глав:** {len(catalog_entries)}\n")
        f.write(f"- **Общий объем текста:** {total_words_all:,} слов / {total_chars_all:,} символов ({total_paras_all:,} абзацев)\n")
        f.write(f"- **Примерная длительность озвучки:** ~{est_total_hours} часов (~{round(total_words_all / 130.0):,} минут)\n")
        f.write(f"- **Форматы:**\n")
        f.write(f"  - Markdown: `output/{output_dir.name}/texts/*.md`\n")
        f.write(f"  - Clean TXT: `output/{output_dir.name}/texts_raw_txt/*.txt`\n")
        f.write(f"  - Structured JSON: `output/{output_dir.name}/parsed_json/*.json`\n\n")

        if summary:
            f.write(f"### Описание (Summary)\n\n> {summary.replace(chr(10), chr(10) + '> ')}\n\n")

        f.write("### Список всех глав\n\n")
        f.write("| № | Название главы | Дата | Слов | Знаков | Аудио (~мин) | Markdown | Raw TXT |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for e in catalog_entries:
            f.write(f"| {e['index']} | **{e['subtitle']}** | {e['date']} | {e['words']:,} | {e['chars']:,} | ~{e['minutes']} | [{e['md_file']}](./{e['md_file']}) | [{e['txt_file']}](../texts_raw_txt/{e['txt_file']}) |\n")

    logger.info(f"=== PARSING COMPLETE in {time.time() - t0:.2f}s ===")
    logger.info(f"Work: {title} ({len(catalog_entries)} chapters)")
    logger.info(f"Total: {total_words_all:,} words | {total_chars_all:,} characters | ~{est_total_hours} hours of audio")
    logger.info(f"Saved to: {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Parse AO3 fanfiction work")
    parser.add_argument("--html", type=Path, default=ROOT_DIR / "output/Things_Only_Get_Stranger/raw_html/full_work.html",
                        help="Path to full work HTML file")
    parser.add_argument("--nav", type=Path, default=ROOT_DIR / "output/Things_Only_Get_Stranger/raw_html/navigate.json",
                        help="Path to navigate.json metadata")
    parser.add_argument("--output-dir", type=Path, default=ROOT_DIR / "output/Things_Only_Get_Stranger",
                        help="Target output directory")

    args = parser.parse_args()
    parse_work(args.html, args.nav, args.output_dir)


if __name__ == "__main__":
    main()
