"""Safe text splitting for the 'retries exhausted' fallback.

Replaces the inline split in generate_chapter_vid_voice.py, which on a sentence without any
',;:—' delimiter produced the halves ['.', <whole text>] (the TTS was asked to read a lone
period and the real problem was never fixed), and on sentences with delimiters produced
artefacts such as 'word,.' (a comma followed by an appended period).

Contract of split_for_fallback():
  * returns exactly two non-empty halves, or None when no safe split exists
    (the caller must then keep its best candidate instead of inventing a bad split);
  * both halves contain at least `min_words` words;
  * no word is added, dropped or reordered (only punctuation at the cut is normalised);
  * the LEFT half ends with terminal punctuation and never with a dangling ',;:—';
    the RIGHT half keeps the original ending of the text (already normalised upstream by
    validate_and_prepare_segments).
"""
import re
from typing import List, Optional

import razdel

_WORD = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+")
_TERMINAL_END = (".", "!", "?", "…", "»", '"', "”")
_DANGLING = ",;:—–-"
# cut points inside one sentence: after a comma/semicolon/colon that follows a word,
# or right before a spaced dash (so a dialogue attribution starts the right half with '— ')
_CLAUSE_CUT = re.compile(r"(?<=\w)[,;:]\s+|\s[—–]\s+")


def count_words(text: str) -> int:
    return len(_WORD.findall(text))


def _terminate(half: str) -> str:
    """Strip dangling clause punctuation and make sure the half ends like a sentence."""
    half = half.strip()
    half = half.rstrip(_DANGLING + " ")
    if not half:
        return half
    return half if half.endswith(_TERMINAL_END) else half + "."


def _best_balanced(text: str, cuts: List[int], min_words: int) -> Optional[int]:
    best, best_gap = None, None
    for cut in cuts:
        left, right = text[:cut].strip(), text[cut:].strip()
        if count_words(left) < min_words or count_words(right) < min_words:
            continue
        gap = abs(len(left) - len(right))
        if best_gap is None or gap < best_gap:
            best, best_gap = cut, gap
    return best


def split_for_fallback(text: str, min_words: int = 3) -> Optional[List[str]]:
    text = (text or "").strip()
    if not text:
        return None

    # 1) prefer a sentence boundary
    sents = [s for s in razdel.sentenize(text) if s.text.strip()]
    if len(sents) >= 2:
        cut = _best_balanced(text, [s.start for s in sents[1:]], min_words)
        if cut is not None:
            return [text[:cut].strip(), text[cut:].strip()]

    # 2) otherwise a clause boundary inside the sentence(s)
    cuts = []
    for m in _CLAUSE_CUT.finditer(text):
        cuts.append(m.start() + 1 if text[m.start()].isspace() else m.end())
    cut = _best_balanced(text, cuts, min_words)
    if cut is None:
        return None
    left, right = _terminate(text[:cut]), text[cut:].strip()
    if not left or not right:
        return None
    return [left, right]
