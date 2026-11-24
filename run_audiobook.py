#!/home/nk/.venvs/cosyvoice-env/bin/python
# -*- coding: utf-8 -*-
"""
================================================================================
  🎙️ АВТОНОМНЫЙ ПАЙПЛАЙН ГЕНЕРАЦИИ И ВЕРИФИКАЦИИ АУДИОКНИГ (CosyVoice 3)
================================================================================
  Полностью автоматический запуск полного цикла без участия ассистента:
  1. Сегментация текста с сохранением 100% слов (35-65 токенов на блок).
  2. Zero-Shot синтез CosyVoice 3 с in-flight проверкой окончаний на CPU.
  3. Сборка мастер-аудио (200мс предложения, 500мс абзацы, 500мс хвостовая тишина).
  4. Пост-верификация всех сегментов на GPU через Whisper (0 проглоченных окончаний).
  5. Самоисцеление (Auto-Repair): если найдены дефекты, автоматический подбор
     сидов и разбиение реплик до 100% идеального совпадения.
  6. Опциональный рендер видео в 1080p 20fps NVENC.
  7. Отправка готовой главы в Telegram.
================================================================================
"""

import os
import sys
import re
import json
import time
import shutil
import argparse
import subprocess
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # make fanfic_pipeline importable from any cwd
from fanfic_pipeline.exit_codes import EXIT_OK, EXIT_BATCH_FAILED, EXIT_BATCH_NEEDS_REVIEW, EXIT_REPAIR_INCOMPLETE
from fanfic_pipeline.verification import run_verification

# --- ANSI Color Codes for Terminal UI ---
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_WHITE = "\033[37m"

def c_text(text: str, color: str) -> str:
    return f"{color}{text}{C_RESET}"

def find_root_dir() -> Path:
    override = os.environ.get("AUDIOBOOK_ROOT_DIR")
    if override and Path(override).is_dir():
        return Path(override).resolve()
    candidates = [
        Path(__file__).resolve().parent,
        Path("/mnt/ai/AI-Dubbing"),
        Path.cwd(),
    ]
    for c in candidates:
        if (c / "CosyVoice").is_dir() and (c / "voices").is_dir():
            return c.resolve()
    for c in candidates:
        if (c / "scripts").is_dir() and (c / "output").is_dir():
            return c.resolve()
    return Path(__file__).resolve().parent

ROOT_DIR = find_root_dir()

def find_python_bin() -> Path:
    override = os.environ.get("AUDIOBOOK_PYTHON_BIN")
    if override and Path(override).is_file():
        return Path(override).resolve()
    candidates = [
        Path("/home/nk/.venvs/cosyvoice-env/bin/python"),
        Path(sys.executable),
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return Path(sys.executable)

PYTHON_BIN = find_python_bin()
CHAPTERS_RU_DIR = ROOT_DIR / "output" / "Potter" / "chapters_ru"
CHAPTERS_EN_DIR = ROOT_DIR / "output" / "Potter" / "chapters_en"

def get_default_chapters_dir() -> Path:
    override = os.environ.get("AUDIOBOOK_CHAPTERS_DIR")
    if override and Path(override).exists():
        return Path(override)
    lang = os.environ.get("AUDIOBOOK_LANG", "").lower()
    if lang == "en":
        return CHAPTERS_EN_DIR if CHAPTERS_EN_DIR.exists() else CHAPTERS_RU_DIR
    if CHAPTERS_RU_DIR.exists() and list(CHAPTERS_RU_DIR.glob("*.txt")):
        return CHAPTERS_RU_DIR
    if CHAPTERS_EN_DIR.exists() and list(CHAPTERS_EN_DIR.glob("*.txt")):
        return CHAPTERS_EN_DIR
    return CHAPTERS_RU_DIR

CHAPTERS_DIR = get_default_chapters_dir()
OUTPUT_DIR = ROOT_DIR / "output"
SCRIPTS_DIR = ROOT_DIR / "scripts"

GENERATE_SCRIPT = SCRIPTS_DIR / "generate_chapter_vid_voice.py"
VERIFY_SCRIPT = SCRIPTS_DIR / "verify_chapter_endings_gpu.py"
REPAIR_SCRIPT = SCRIPTS_DIR / "auto_repair_chapter.py"

DEFAULT_BG_VIDEO = Path("/home/nk/Загрузки/export to linux/book2_video_ref.mp4")

BOOK_MAPPING = {
    1: (OUTPUT_DIR / "Книга_1_Философский_камень", "Книга 1: Философский камень"),
    2: (OUTPUT_DIR / "Книга_2_Тайная_комната", "Книга 2: Тайная комната"),
    3: (OUTPUT_DIR / "Книга_3_Узник_Азкабана", "Книга 3: Узник Азкабана"),
    4: (OUTPUT_DIR / "Книга_4_Кубок_огня", "Книга 4: Кубок огня"),
    5: (OUTPUT_DIR / "Книга_5_Орден_Феникса", "Книга 5: Орден Феникса"),
}


def parse_chapter_info(file_path: Path):
    """Parses Book number, title and Chapter number/type from filename."""
    stem = file_path.stem
    stem_lower = stem.lower()

    b_num = None
    m_book = re.search(r'(?:книга|book)[_\s]*(\d+)', stem_lower)
    if m_book:
        b_num = int(m_book.group(1))
    elif 'azkaban' in stem_lower or 'узник' in stem_lower:
        b_num = 3
    elif 'chamber' in stem_lower or 'тайная' in stem_lower:
        b_num = 2
    elif 'philosopher' in stem_lower or 'философский' in stem_lower:
        b_num = 1

    if b_num in BOOK_MAPPING:
        book_dir, book_title = BOOK_MAPPING[b_num]
    else:
        book_dir = OUTPUT_DIR / (f"Книга_{b_num}" if b_num else "Книга")
        book_title = f"Книга {b_num}" if b_num else "Книга"

    m_ch = re.search(r'(?:глава|ch(?:apter)?)[_\s]*(\d+)', stem_lower)
    m_pro = re.search(r'(?:пролог|prologue)[_\s]*(\d*)', stem_lower)

    if m_ch:
        ch_num = int(m_ch.group(1))
        chap_dir = book_dir / f"Глава_{ch_num:02d}"
        chap_name = f"Глава {ch_num}"
    elif m_pro:
        pro_num_str = m_pro.group(1)
        if pro_num_str:
            pro_num = int(pro_num_str)
            candidate_numbered = book_dir / f"Пролог_{pro_num:02d}"
            candidate_single = book_dir / "Пролог"
            if candidate_numbered.exists():
                chap_dir = candidate_numbered
                chap_name = f"Пролог {pro_num}"
            elif candidate_single.exists():
                chap_dir = candidate_single
                chap_name = "Пролог"
            else:
                chap_dir = candidate_numbered
                chap_name = f"Пролог {pro_num}"
        else:
            chap_dir = book_dir / "Пролог"
            chap_name = "Пролог"
    else:
        safe_name = re.sub(r'[^\w\-_\.]', '_', stem)
        chap_dir = book_dir / safe_name
        chap_name = stem

    return {
        "file_path": file_path,
        "book_num": b_num,
        "book_title": book_title,
        "book_dir": book_dir,
        "chap_name": chap_name,
        "chap_dir": chap_dir,
    }


def get_chapter_status(info: dict):
    """Determines generation and verification status of a chapter."""
    chap_dir = info["chap_dir"]
    report_file = chap_dir / "gpu_verification_report.json"
    segments_file = chap_dir / "segments.json"
    cache_dir = chap_dir / "segments"

    if report_file.exists():
        try:
            with open(report_file, "r", encoding="utf-8") as f:
                rep = json.load(f)
            issues = rep.get("issues_count", len(rep.get("issues", [])))
            tot = rep.get("total_segments", 0)
            if issues == 0:
                return {
                    "code": "VERIFIED_OK",
                    "badge": c_text("✓ ГОТОВО (0 ошибок)", C_GREEN + C_BOLD),
                    "total": tot,
                    "issues": 0
                }
            else:
                return {
                    "code": "HAS_ISSUES",
                    "badge": c_text(f"⚠ ТРЕБУЕТ ПОЧИНКИ ({issues} дефектов)", C_YELLOW + C_BOLD),
                    "total": tot,
                    "issues": issues
                }
        except Exception:
            pass

    if segments_file.exists():
        try:
            with open(segments_file, "r", encoding="utf-8") as f:
                segs = json.load(f)
            tot = len(segs)
            done = len(list(cache_dir.glob("seg_*.wav"))) if cache_dir.exists() else 0
            if done >= tot and tot > 0:
                return {
                    "code": "GENERATED",
                    "badge": c_text("✓ Сгенерировано (ждет верификации)", C_CYAN),
                    "total": tot,
                    "issues": -1
                }
            else:
                return {
                    "code": "IN_PROGRESS",
                    "badge": c_text(f"⏳ В процессе ({done}/{tot} блоков)", C_MAGENTA),
                    "total": tot,
                    "done": done
                }
        except Exception:
            pass

    if cache_dir.exists():
        done = len(list(cache_dir.glob("seg_*.wav")))
        if done > 0:
            return {
                "code": "IN_PROGRESS",
                "badge": c_text(f"⏳ В процессе ({done} блоков)", C_MAGENTA),
                "done": done
            }

    return {
        "code": "PENDING",
        "badge": c_text("⚪ ОЖИДАЕТ ГЕНЕРАЦИИ", C_WHITE + C_DIM),
        "total": 0
    }


def scan_all_chapters():
    """Scans CHAPTERS_DIR and groups files by book."""
    if not CHAPTERS_DIR.exists():
        return []

    files = sorted(list(CHAPTERS_DIR.glob("*.txt")))
    chapters = []
    for f in files:
        # Ignore temp or backup files
        if f.name.startswith(".") or f.name.endswith(".bak"):
            continue
        info = parse_chapter_info(f)
        status = get_chapter_status(info)
        info["status"] = status
        chapters.append(info)

    # Sort by Book number, then Prologue first, then Chapter numbers
    def sort_key(item):
        b = item["book_num"] or 99
        c_name = item["chap_name"]
        if "Пролог" in c_name:
            m = re.search(r'\d+', c_name)
            p_num = int(m.group(0)) if m else 0
            return (b, 0, p_num)
        m = re.search(r'\d+', c_name)
        c_num = int(m.group(0)) if m else 999
        return (b, 1, c_num)

    chapters.sort(key=sort_key)
    return chapters


def render_video_nvenc(master_mp3: Path, output_mp4: Path, bg_video: Path = None):
    """Renders 1080p 20fps 2500k NVENC video from master audio."""
    if bg_video is None or not bg_video.exists():
        bg_video = DEFAULT_BG_VIDEO

    if not bg_video.exists():
        print(c_text(f"  [!] Фоновое видео не найдено: {bg_video}. Пропуск рендера видео.", C_YELLOW))
        return False

    ffmpeg = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    temp_mp4 = output_mp4.with_suffix(".tmp.mp4")
    if temp_mp4.exists():
        temp_mp4.unlink()

    print(c_text(f"  🎬 Запуск NVENC видеорендера: 1080p @ 20 fps, 2500 kbps...", C_CYAN))
    print(c_text(f"     Фон:   {bg_video}", C_DIM))
    print(c_text(f"     Аудио: {master_mp3}", C_DIM))
    print(c_text(f"     Выход: {output_mp4}", C_DIM))

    cmd = [
        ffmpeg, "-y",
        "-stream_loop", "-1", "-i", str(bg_video),
        "-i", str(master_mp3),
        "-map", "0:v",
        "-map", "1:a",
        "-vf", "fps=20,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-c:v", "h264_nvenc",
        "-preset", "p5",
        "-tune", "hq",
        "-b:v", "2500k",
        "-maxrate", "2500k",
        "-bufsize", "5000k",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-shortest",
        str(temp_mp4)
    ]

    t0 = time.time()
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(c_text(f"  ❌ Ошибка FFmpeg при рендере видео: {res.stderr[-300:]}", C_RED))
        if temp_mp4.exists():
            temp_mp4.unlink()
        return False

    temp_mp4.rename(output_mp4)
    dur_render = time.time() - t0
    size_mb = output_mp4.stat().st_size / 1024 / 1024
    print(c_text(f"  ✓ Видео успешно создано за {dur_render/60:.1f} мин ({size_mb:.1f} MB) -> {output_mp4.name}", C_GREEN + C_BOLD))
    return True



# Result of one chapter run. Previously run_pipeline_for_chapter() returned True even when
# defects remained, and the final banner always claimed "100% verified".
RESULT_OK = "ok"          # generated, verified, 0 unresolved defects
RESULT_ISSUES = "issues"  # chapter assembled, but defects remain / repair did not finish cleanly
RESULT_FAILED = "failed"  # a pipeline stage failed; the chapter is not usable as-is

BATCH_CODES = ("PENDING", "IN_PROGRESS", "HAS_ISSUES", "GENERATED")

_PROGRESS_MARKERS = (
    "Synthesizing seg", "verified OK", "Retry attempt", "Master", "Full WAV", "Standard MP3",
    # messages the generator actually logs (the filter above never matched them):
    "Synth OK", "Swallowed ending", "Retries exhausted", "Fallback split", "Text Check",
)
_ERROR_RE = re.compile(r"(Traceback \(most recent call last\)|Exception in thread|^\w*(Error|Exception)\b)")


def classify_worker_line(line: str):
    """Returns 'warn', 'info' or None for one line of worker output.

    Tracebacks and 'Exception in thread ...' used to be dropped by the filter, so a crashing
    worker looked like a silent one.
    """
    if "ERROR" in line or "WARNING" in line or _ERROR_RE.search(line):
        return "warn"
    if any(m in line for m in _PROGRESS_MARKERS):
        return "info"
    if "[1/" in line or "[10/" in line or "Progress:" in line or "100%" in line:
        return "info"
    return None


def plan_batch(chapters, clean_cache=False):
    """Decides what a batch run does with each chapter. Pure function (unit-tested).

    * GENERATED chapters (all segments synthesized, awaiting verification) are now included and
      run verification only -- previously they were silently skipped forever.
    * --clean-cache never wipes IN_PROGRESS (hours of partial synthesis) or GENERATED chapters.
    """
    plan = []
    for ch in chapters:
        code = ch["status"]["code"]
        if code not in BATCH_CODES:
            continue
        warning = None
        clean = False
        if clean_cache:
            if code in ("PENDING", "HAS_ISSUES"):
                clean = True
            else:
                warning = (f"--clean-cache пропущен для '{ch['chap_name']}' (статус {code}): "
                           f"кэш содержит незавершённую/непроверенную работу")
        plan.append({
            "chapter": ch,
            "clean_cache": clean,
            "skip_generation": code == "GENERATED",
            "warning": warning,
        })
    return plan


def exit_code_for(results) -> int:
    """Process exit status for a list of RESULT_* values."""
    if any(r == RESULT_FAILED for r in results):
        return EXIT_BATCH_FAILED
    if any(r == RESULT_ISSUES for r in results):
        return EXIT_BATCH_NEEDS_REVIEW
    return EXIT_OK


def _print_verifier_output(output: str):
    for line in output.splitlines():
        if "SWALLOW ISSUE" in line or "issues" in line or "Verified [" in line or "Verification complete" in line:
            print(f"    {line}")


def run_pipeline_for_chapter(
    text_file: Path,
    video: bool = False,
    no_telegram: bool = False,
    clean_cache: bool = False,
    device: str = "cuda",
    bg_video: Path = None,
    skip_generation: bool = False
):
    """
    Executes the autonomous end-to-end pipeline for a single chapter:
    1. Generation (CosyVoice 3 + Whisper CPU in-flight)          [skipped if skip_generation]
    2. GPU Whisper Verification (Whisper CUDA)
    3. Self-Healing Auto-Repair (if issues detected, loops until 0 issues or max passes)
    4. Optional NVENC Video Render

    Returns RESULT_OK, RESULT_ISSUES or RESULT_FAILED (see module constants).
    """
    info = parse_chapter_info(text_file)
    chap_dir = info["chap_dir"]
    chap_name = info["chap_name"]
    book_title = info["book_title"]

    print("\n" + "=" * 76)
    print(c_text(f" 🚀 ЗАПУСК АВТОНОМНОГО ЦИКЛА: {book_title} — {chap_name}", C_BOLD + C_CYAN))
    print(c_text(f" 📄 Текст: {text_file.resolve()}", C_DIM))
    print(c_text(f" 📁 Папка: {chap_dir}", C_DIM))
    print("=" * 76)

    t_global_start = time.time()

    # Pre-flight check text content
    try:
        with open(text_file, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()
        words = len(re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]+', content))
        print(c_text(f" 📊 Исходный текст: {len(content)} симв., ~{words} слов", C_WHITE))
    except Exception as e:
        print(c_text(f" ❌ Ошибка чтения файла {text_file}: {e}", C_RED))
        return RESULT_FAILED

    # -------------------------------------------------------------
    # ЭТАП 1: Синтез речи (CosyVoice 3)
    # -------------------------------------------------------------
    if skip_generation:
        print("\n" + c_text(" [1/4] Синтез пропущен: все блоки уже сгенерированы (ждут верификации).", C_DIM))
    else:
        print("\n" + c_text(" [1/4] СИНТЕЗ РЕЧИ (CosyVoice 3 + in-flight Whisper CPU)...", C_BOLD + C_YELLOW))
        gen_cmd = [
            str(PYTHON_BIN),
            str(GENERATE_SCRIPT),
            "--text-path", str(text_file.resolve())
        ]
        if no_telegram:
            gen_cmd.append("--no-telegram")
        if clean_cache:
            gen_cmd.append("--clean-cache")

        tail = deque(maxlen=30)   # last lines of worker output, shown if the worker fails
        p = subprocess.Popen(gen_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in iter(p.stdout.readline, ''):
            line_s = line.strip()
            if not line_s:
                continue
            tail.append(line_s)
            kind = classify_worker_line(line_s)
            if kind == "warn":
                print(c_text(f"    {line_s}", C_YELLOW))
            elif kind == "info":
                print(f"    {line_s}")
        p.wait()

        if p.returncode != 0:
            print(c_text(f" ❌ Ошибка генерации главы (код {p.returncode})!", C_RED + C_BOLD))
            print(c_text("    Последние строки вывода генератора:", C_DIM))
            for t_line in tail:
                print(c_text(f"      {t_line}", C_DIM))
            return RESULT_FAILED

        print(c_text(" ✓ Синтез завершён. CosyVoice выгружен из VRAM.", C_GREEN))

    # -------------------------------------------------------------
    # ЭТАП 2: Пост-верификация на GPU Whisper (CUDA)
    # -------------------------------------------------------------
    print("\n" + c_text(f" [2/4] ПОСТ-ВЕРИФИКАЦИЯ НА GPU ({device.upper()} Whisper base)...", C_BOLD + C_YELLOW))
    verify_cmd = [
        str(PYTHON_BIN),
        str(VERIFY_SCRIPT),
        "--chapter-dir", str(chap_dir),
        "--device", device
    ]
    report_file = chap_dir / "gpu_verification_report.json"

    # run_verification() deletes the previous report first, so a crashed verifier can never
    # be mistaken for "0 issues".
    vres = run_verification(verify_cmd, report_file)
    _print_verifier_output(vres.output)
    if not vres.ok:
        print(c_text(f" ❌ Верификация не выполнена: {vres.reason}. Глава НЕ считается проверенной.", C_RED + C_BOLD))
        return RESULT_FAILED
    issues_count = vres.issues_count

    # -------------------------------------------------------------
    # ЭТАП 3: Автономное самоисцеление (Auto-Repair Loop)
    # -------------------------------------------------------------
    max_repair_passes = 3
    repair_pass = 0
    repair_incomplete = False   # state of the LAST repair pass: master may be stale if it did not finish cleanly

    while issues_count > 0 and repair_pass < max_repair_passes:
        repair_pass += 1
        print("\n" + c_text(f" [3/4] 🛡️ САМОИСЦЕЛЕНИЕ: Проход {repair_pass}/{max_repair_passes} (дефектов: {issues_count})...", C_BOLD + C_MAGENTA))

        rep_cmd = [
            str(PYTHON_BIN),
            str(REPAIR_SCRIPT),
            "--chapter-dir", str(chap_dir)
        ]
        r_proc = subprocess.run(rep_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in r_proc.stdout.splitlines():
            if "Attempting self-healing" in line or "PERFECT" in line or "repaired via" in line or "Master" in line or "repaired" in line:
                print(f"    {line}")

        repair_incomplete = r_proc.returncode != 0
        if repair_incomplete:
            print(c_text(f"    ⚠️ Самоисцеление завершилось с кодом {r_proc.returncode}: "
                         f"часть сегментов не исправлена или мастер не пересобран.", C_YELLOW + C_BOLD))
            if r_proc.returncode != EXIT_REPAIR_INCOMPLETE:
                for t_line in r_proc.stdout.splitlines()[-15:]:
                    print(c_text(f"      {t_line}", C_DIM))

        # Re-verify on GPU
        print(c_text(f"    Повторная проверка на GPU после исцеления...", C_CYAN))
        vres = run_verification(verify_cmd, report_file)
        _print_verifier_output(vres.output)
        if not vres.ok:
            print(c_text(f" ❌ Повторная верификация не выполнена: {vres.reason}. Результат ремонта неизвестен.", C_RED + C_BOLD))
            return RESULT_FAILED
        issues_count = vres.issues_count

        if issues_count == 0:
            if repair_incomplete:
                print(c_text("    Верификатор больше не видит дефектов, но ремонт завершился с ошибкой.", C_YELLOW))
            else:
                print(c_text("    ✓ Верификатор больше не видит дефектов окончаний.", C_GREEN + C_BOLD))
            break
        else:
            print(c_text(f"    Осталось дефектов после исцеления: {issues_count}", C_YELLOW))

    clean = issues_count == 0 and not repair_incomplete
    if clean:
        print(c_text(" ✓ Верификация пройдена: дефектов окончаний не найдено.", C_GREEN + C_BOLD))
    elif issues_count > 0:
        print(c_text(f" ⚠️ Внимание: после {repair_pass} проходов исцеления осталось {issues_count} спорных мест.", C_YELLOW + C_BOLD))
    else:
        print(c_text(" ⚠️ Внимание: последний проход самоисцеления завершился с ошибкой; "
                     "мастер мог не быть пересобран. Повторите: run_audiobook.py <глава> --repair-only", C_YELLOW + C_BOLD))

    # -------------------------------------------------------------
    # ЭТАП 4: Опциональный NVENC Видеорендер
    # -------------------------------------------------------------
    safe_stem = f"{chap_dir.parent.name}_{chap_dir.name}" if chap_dir.parent.name.startswith("Книга") else chap_dir.name
    master_mp3 = chap_dir / f"{safe_stem}.mp3"
    master_wav = chap_dir / f"{safe_stem}.wav"
    output_mp4 = chap_dir / f"{safe_stem}_1080p_20fps.mp4"

    if video and master_mp3.exists():
        print("\n" + c_text(" [4/4] РЕНДЕР ВИДЕО 1080p (NVENC)...", C_BOLD + C_YELLOW))
        render_video_nvenc(master_mp3, output_mp4, bg_video=bg_video)
    else:
        print("\n" + c_text(" [4/4] Рендер видео пропущен (флаг --video не указан).", C_DIM))

    total_time = time.time() - t_global_start

    # Summary Display
    print("\n" + "=" * 76)
    if clean:
        print(c_text(f" 🎉 ГЛАВА УСПЕШНО ГОТОВА: {book_title} — {chap_name}", C_BOLD + C_GREEN))
    else:
        print(c_text(f" ⚠️ ГЛАВА СОБРАНА, НО ТРЕБУЕТ ВНИМАНИЯ: {book_title} — {chap_name}", C_BOLD + C_YELLOW))
    print(c_text(f" ⏱️ Общее время выполнения: {total_time/60:.1f} мин.", C_WHITE))
    if master_mp3.exists():
        size_mb = master_mp3.stat().st_size / 1024 / 1024
        print(c_text(f" 🎵 Мастер MP3: {master_mp3} ({size_mb:.1f} MB)", C_WHITE))
    if master_wav.exists():
        size_mb = master_wav.stat().st_size / 1024 / 1024
        print(c_text(f" 🔊 Мастер WAV: {master_wav} ({size_mb:.1f} MB)", C_WHITE))
    if video and output_mp4.exists():
        size_mb = output_mp4.stat().st_size / 1024 / 1024
        print(c_text(f" 🎬 Видео MP4:  {output_mp4} ({size_mb:.1f} MB)", C_WHITE))
    if clean:
        print(c_text(" 🛡️ Проверка:   окончания последних слов блоков подтверждены (0 дефектов)", C_GREEN + C_BOLD))
    else:
        print(c_text(f" 🛡️ Проверка:   НЕ пройдена полностью (дефектов: {issues_count}"
                     f"{', ремонт завершился с ошибкой' if repair_incomplete else ''})", C_YELLOW + C_BOLD))
    print("=" * 76 + "\n")
    return RESULT_OK if clean else RESULT_ISSUES


def _print_batch_summary(results):
    """results: list of (chapter_info, RESULT_*). Prints an honest summary, returns the exit code."""
    ok = [c for c, r in results if r == RESULT_OK]
    issues = [c for c, r in results if r == RESULT_ISSUES]
    failed = [c for c, r in results if r == RESULT_FAILED]
    print("\n" + "=" * 76)
    print(c_text(f" ИТОГ ПАКЕТНОГО ЗАПУСКА: всего {len(results)} | готово {len(ok)} | "
                 f"с дефектами {len(issues)} | с ошибкой {len(failed)}", C_BOLD))
    for c in issues:
        print(c_text(f"   ⚠️ требует внимания: {c['chap_name']} ({c['file_path'].name})", C_YELLOW))
    for c in failed:
        print(c_text(f"   ❌ ошибка этапа:    {c['chap_name']} ({c['file_path'].name})", C_RED))
    if not issues and not failed:
        print(c_text(" Все главы успешно обработаны и проверены.", C_GREEN + C_BOLD))
    print("=" * 76)
    return exit_code_for([r for _, r in results])


def interactive_menu():
    """Interactive CLI menu when run without parameters."""
    while True:
        chapters = scan_all_chapters()

        print("\n" + "=" * 78)
        print(c_text("        🎙️  АВТОНОМНЫЙ ПАЙПЛАЙН ГЕНЕРАЦИИ АУДИОКНИГ (CosyVoice 3)", C_BOLD + C_CYAN))
        print(c_text("             RTX 3070 8GB | Whisper Verification & Auto-Repair", C_DIM))
        print("=" * 78)

        if not chapters:
            print(c_text(f"Файлы глав не найдены в {CHAPTERS_DIR}", C_RED))
            print("Поместите файлы .txt в эту папку и повторите запуск.")
            break

        current_book = None
        pending_indices = []

        for idx, ch in enumerate(chapters, 1):
            if ch["book_title"] != current_book:
                current_book = ch["book_title"]
                print("\n " + c_text(f"📖 {current_book}", C_BOLD + C_WHITE))

            status = ch["status"]
            badge = status["badge"]
            badge_code = status["code"]
            if badge_code in BATCH_CODES:
                pending_indices.append(idx)

            details = ""
            if status.get("total", 0) > 0:
                details = f"({status['total']} блоков)"

            print(f"   [{c_text(f'{idx:2d}', C_BOLD)}] {ch['chap_name']:<15} {ch['file_path'].name:<32} {badge} {details}")

        print("\n" + "-" * 78)
        print(c_text(" Команды:", C_BOLD))
        print("   " + c_text("[1-N]", C_GREEN) + "       Сгенерировать выбранную главу (Синтез -> Проверка -> Самоисцеление)")
        print("   " + c_text("[A / all]", C_YELLOW) + "   Сгенерировать ВСЕ ожидающие главы подряд (" + f"{len(pending_indices)} глав)" + ")")
        print("   " + c_text("[V <N>]", C_CYAN) + "     Только проверить качество главы на GPU Whisper")
        print("   " + c_text("[R <N>]", C_MAGENTA) + "  Принудительно запустить самоисцеление главы")
        print("   " + c_text("[Q]", C_DIM) + "          Выход")
        print("=" * 78)

        choice = input(c_text(" Введите команду или номер: ", C_BOLD)).strip()
        if not choice:
            continue

        if choice.lower() in ("q", "quit", "exit"):
            print(c_text("До встречи!", C_GREEN))
            break

        # Verification only
        if choice.lower().startswith("v "):
            num_str = choice[2:].strip()
            if num_str.isdigit() and 1 <= int(num_str) <= len(chapters):
                target = chapters[int(num_str) - 1]
                print(c_text(f"\n🔍 Запуск GPU верификации для {target['chap_name']}...", C_CYAN))
                subprocess.run([
                    str(PYTHON_BIN), str(VERIFY_SCRIPT),
                    "--chapter-dir", str(target["chap_dir"]),
                    "--device", "cuda"
                ])
                input("\nНажмите Enter для возврата в меню...")
            continue

        # Repair only
        if choice.lower().startswith("r "):
            num_str = choice[2:].strip()
            if num_str.isdigit() and 1 <= int(num_str) <= len(chapters):
                target = chapters[int(num_str) - 1]
                print(c_text(f"\n🛡️ Запуск самоисцеления для {target['chap_name']}...", C_MAGENTA))
                subprocess.run([
                    str(PYTHON_BIN), str(REPAIR_SCRIPT),
                    "--chapter-dir", str(target["chap_dir"])
                ])
                # Re-verify
                subprocess.run([
                    str(PYTHON_BIN), str(VERIFY_SCRIPT),
                    "--chapter-dir", str(target["chap_dir"]),
                    "--device", "cuda"
                ])
                input("\nНажмите Enter для возврата в меню...")
            continue

        # Batch All
        if choice.lower() in ("a", "all"):
            if not pending_indices:
                print(c_text("\nВсе главы уже сгенерированы и проверены на 100%!", C_GREEN + C_BOLD))
                input("\nНажмите Enter для возврата в меню...")
                continue

            ask_vid = input(c_text(" Рендерить видео (NVENC) для каждой главы? [y/N]: ", C_YELLOW)).strip().lower()
            do_video = ask_vid in ("y", "yes", "да")

            batch_results = []
            for p_idx in pending_indices:
                target = chapters[p_idx - 1]
                batch_results.append((target, run_pipeline_for_chapter(
                    target["file_path"],
                    video=do_video,
                    no_telegram=False,
                    clean_cache=False,
                    skip_generation=target["status"]["code"] == "GENERATED"
                )))
            _print_batch_summary(batch_results)
            input("\nНажмите Enter для возврата в меню...")
            continue

        # Single chapter by number
        if choice.isdigit() and 1 <= int(choice) <= len(chapters):
            target = chapters[int(choice) - 1]

            ask_vid = input(c_text(f" Рендерить также видео (NVENC) для {target['chap_name']}? [y/N]: ", C_YELLOW)).strip().lower()
            do_video = ask_vid in ("y", "yes", "да")

            ask_clean = False
            if target["status"]["code"] == "VERIFIED_OK":
                ask_re = input(c_text(" Глава уже готова. Перегенерировать с нуля? [y/N]: ", C_YELLOW)).strip().lower()
                if ask_re not in ("y", "yes", "да"):
                    continue
                ask_clean = True

            run_pipeline_for_chapter(
                target["file_path"],
                video=do_video,
                no_telegram=False,
                clean_cache=ask_clean
            )
            input("\nНажмите Enter для возврата в меню...")
            continue

        print(c_text(f"Неизвестная команда: {choice}", C_RED))


def _verify_and_report(info: dict, device: str) -> int:
    """Runs the verifier once and returns an honest process exit code (0 clean / 1 could not verify / 3 defects)."""
    chap_dir = info["chap_dir"]
    vres = run_verification(
        [str(PYTHON_BIN), str(VERIFY_SCRIPT), "--chapter-dir", str(chap_dir), "--device", device],
        chap_dir / "gpu_verification_report.json",
    )
    print(vres.output)
    if not vres.ok:
        print(c_text(f"❌ Верификация не выполнена: {vres.reason}", C_RED + C_BOLD))
        return EXIT_BATCH_FAILED
    if vres.issues_count > 0:
        print(c_text(f"⚠️ Найдено дефектов: {vres.issues_count}", C_YELLOW + C_BOLD))
        return EXIT_BATCH_NEEDS_REVIEW
    print(c_text("✓ Дефектов окончаний не найдено.", C_GREEN + C_BOLD))
    return EXIT_OK


def main():
    parser = argparse.ArgumentParser(
        description="Автономный мастер-пайплайн генерации аудиокниг (CosyVoice 3 + Whisper GPU)."
    )
    parser.add_argument("chapter_path", nargs="?", default=None, help="Путь или имя файла главы (например, Книга3Пролог.txt)")
    parser.add_argument("--all", action="store_true", help="Сгенерировать все ожидающие главы подряд")
    parser.add_argument("--status", action="store_true", help="Показать статус всех глав и выйти")
    parser.add_argument("--video", action="store_true", help="Рендерить 1080p 20fps NVENC видео")
    parser.add_argument("--bg-video", default=None, help="Путь к фоновому видео для рендера")
    parser.add_argument("--no-telegram", action="store_true", help="Отключить отправку в Telegram")
    parser.add_argument("--clean-cache", action="store_true", help="Очистить кэш сегментов и перегенерировать с нуля")
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"], help="Устройство для верификации (по умолчанию: cuda)")
    parser.add_argument("--verify-only", action="store_true", help="Только запустить верификацию на GPU")
    parser.add_argument("--repair-only", action="store_true", help="Только запустить самоисцеление дефектов")

    args = parser.parse_args()

    # Just print status
    if args.status:
        chapters = scan_all_chapters()
        print("\n" + "=" * 74)
        print(c_text(" СТАТУС ГЛАВ АУДИОКНИГ", C_BOLD + C_CYAN))
        print("=" * 74)
        current_book = None
        for idx, ch in enumerate(chapters, 1):
            if ch["book_title"] != current_book:
                current_book = ch["book_title"]
                print("\n " + c_text(f"📖 {current_book}", C_BOLD))
            status = ch["status"]
            print(f"  [{idx:2d}] {ch['chap_name']:<15} {ch['file_path'].name:<32} {status['badge']}")
        print("=" * 74 + "\n")
        return

    # Batch run all
    if args.all:
        chapters = scan_all_chapters()
        plan = plan_batch(chapters, clean_cache=args.clean_cache)
        if not plan:
            print(c_text("Все главы уже полностью готовы!", C_GREEN + C_BOLD))
            return
        print(c_text(f"Найдено {len(plan)} ожидающих глав. Запуск пакетного цикла...", C_CYAN + C_BOLD))
        for item in plan:
            if item["warning"]:
                print(c_text(f"  ⚠️ {item['warning']}", C_YELLOW))
        bg_vid = Path(args.bg_video) if args.bg_video else None
        batch_results = []
        for item in plan:
            ch = item["chapter"]
            batch_results.append((ch, run_pipeline_for_chapter(
                ch["file_path"],
                video=args.video,
                no_telegram=args.no_telegram,
                clean_cache=item["clean_cache"],
                device=args.device,
                bg_video=bg_vid,
                skip_generation=item["skip_generation"]
            )))
        sys.exit(_print_batch_summary(batch_results))

    # Specific chapter provided
    if args.chapter_path:
        target_path = Path(args.chapter_path)
        if not target_path.exists():
            for search_dir in [CHAPTERS_DIR, CHAPTERS_EN_DIR, CHAPTERS_RU_DIR]:
                if search_dir.exists():
                    cand = search_dir / args.chapter_path
                    if cand.exists():
                        target_path = cand
                        break
            else:
                matches = []
                for search_dir in [CHAPTERS_DIR, CHAPTERS_EN_DIR, CHAPTERS_RU_DIR]:
                    if search_dir.exists():
                        matches = list(search_dir.glob(f"*{args.chapter_path}*"))
                        if matches:
                            target_path = matches[0]
                            break
                if not target_path.exists():
                    print(c_text(f"Файл не найден: {args.chapter_path}", C_RED))
                    sys.exit(1)

        info = parse_chapter_info(target_path)

        if args.verify_only:
            print(c_text(f"🔍 Запуск GPU верификации для {info['chap_name']}...", C_CYAN))
            sys.exit(_verify_and_report(info, args.device))

        if args.repair_only:
            print(c_text(f"🛡️ Запуск самоисцеления для {info['chap_name']}...", C_MAGENTA))
            r_proc = subprocess.run([
                str(PYTHON_BIN), str(REPAIR_SCRIPT),
                "--chapter-dir", str(info["chap_dir"])
            ])
            v_code = _verify_and_report(info, args.device)
            if r_proc.returncode != 0:
                print(c_text(f"⚠️ Самоисцеление завершилось с кодом {r_proc.returncode}.", C_YELLOW + C_BOLD))
                v_code = max(v_code, EXIT_BATCH_NEEDS_REVIEW) if v_code != EXIT_BATCH_FAILED else v_code
            sys.exit(v_code)

        bg_vid = Path(args.bg_video) if args.bg_video else None
        result = run_pipeline_for_chapter(
            target_path,
            video=args.video,
            no_telegram=args.no_telegram,
            clean_cache=args.clean_cache,
            device=args.device,
            bg_video=bg_vid
        )
        sys.exit(exit_code_for([result]))

    # No arguments provided -> launch interactive menu
    try:
        interactive_menu()
    except KeyboardInterrupt:
        print("\n\n" + c_text("Процесс прерван пользователем. Прогресс сохранен.", C_YELLOW))
        sys.exit(0)


if __name__ == "__main__":
    main()
