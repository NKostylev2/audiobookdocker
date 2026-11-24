import re
from pathlib import Path

import pytest

from fanfic_pipeline.segmenter import SentenceSegmenter
from fanfic_pipeline.split_ops import count_words, split_for_fallback

WORD = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+")
SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "Книга3Пролог.txt"


def words(t):
    return [w.lower() for w in WORD.findall(t)]


# ---------------------------------------------------------------- the two bugs of the old inline split
def test_sentence_without_delimiters_is_not_split_into_a_lone_period():
    """Old code returned ['.', <whole text>]: the TTS was asked to read '.' and nothing was fixed."""
    text = "Он долго смотрел на закрытую дверь и не понимал почему никто не отвечает на стук"
    assert split_for_fallback(text) is None


def test_no_comma_period_artifact():
    """Old code produced 'word,.' by appending '.' after a trailing comma."""
    halves = split_for_fallback("Он долго смотрел на закрытую дверь, и не понимал, почему никто не отвечает на стук.")
    assert halves == ["Он долго смотрел на закрытую дверь.", "и не понимал, почему никто не отвечает на стук."]
    assert not re.search(r"[,;:—–-]\s*\.", halves[0])


# ---------------------------------------------------------------- behaviour
def test_prefers_sentence_boundary_and_balances_by_length():
    text = ("Из детской донёсся ещё один грохот. — Это, — пробормотала Лили в подушку, — твой сын. "
            "— Наш сын. — Он становится твоим.")
    left, right = split_for_fallback(text)
    assert left == "Из детской донёсся ещё один грохот."
    assert right.startswith("— Это,")


def test_dialogue_too_short_to_split_safely_returns_none():
    assert split_for_fallback("— Это, — пробормотала Лили в подушку, — твой сын.") is None


@pytest.mark.parametrize("text", ["", "   ", "— Да.", "Да.", "Хорошо, ладно."])
def test_degenerate_inputs_return_none(text):
    assert split_for_fallback(text) is None


def test_attribution_dash_starts_right_half():
    halves = split_for_fallback("Гарри долго молчал и смотрел в окно — а потом тихо сказал что согласен.")
    assert halves is not None
    assert halves[1].startswith("— а потом")
    assert halves[0].endswith(".")


# ---------------------------------------------------------------- property check on a real chapter
class _Tok:
    def __init__(self, k):
        self.k = k

    def encode(self, t):
        return [0] * max(1, int(len(t) / self.k))


def _blocks(k):
    content = SAMPLE.read_text(encoding="utf-8-sig")
    paras = []
    for block in re.split(r"\n\s*\n+", content):
        p = re.sub(r"[ \t]*\n[ \t]*", " ", block).strip()
        p = re.sub(r"^[#*_\s]+", "", p)
        p = re.sub(r"[ \t]+", " ", p).strip()
        if p:
            paras.append(p)
    seg = SentenceSegmenter(tokenizer=_Tok(k), min_tokens=35, max_tokens=65, min_block_tokens=25)
    return [s["text"] for s in seg.segment_chapter(paras)]


@pytest.mark.parametrize("chars_per_token", [2.5, 3.2])
def test_contract_holds_on_every_real_block(chars_per_token):
    blocks = _blocks(chars_per_token)
    assert len(blocks) > 100
    split_count = 0
    for text in blocks:
        halves = split_for_fallback(text)
        if halves is None:
            continue
        split_count += 1
        left, right = halves
        assert left and right
        assert left.strip() != "." and right.strip() != "."
        assert not re.search(r"[,;:—–-]\s*\.\s*$", left), left          # no 'x,.' artefacts
        assert left.rstrip()[-1] in '.!?…»"”', left                      # left half is a finished sentence
        assert count_words(left) >= 3 and count_words(right) >= 3
        assert words(left) + words(right) == words(text)                  # nothing added/dropped/reordered
    assert split_count > 0   # the splitter must actually be usable, not just always "None"
