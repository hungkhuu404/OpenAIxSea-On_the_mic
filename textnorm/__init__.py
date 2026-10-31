"""Vietnamese matching keys, search folding, and source-preserving lookup."""

from collections import defaultdict, deque
import re
import unicodedata


TEENCODE_VERSION = "1"

_TEENCODE = {
    "ko": "không",
    "k0": "không",
    "hok": "không",
    "khong": "không",
    "dc": "được",
    "đc": "được",
    "sp": "sản phẩm",
    "vs": "với",
    "j": "gì",
}
_TONES = frozenset("\u0300\u0301\u0303\u0309\u0323")
_LAW_TRANSLATION = str.maketrans(
    {
        **dict.fromkeys("“”„‟″«»", '"'),
        **dict.fromkeys("‘’‚‛′", "'"),
        **dict.fromkeys("‐‑‒–—―−", "-"),
        **dict.fromkeys("\u200b\u200c\u200d\u2060\ufeff\u00ad", None),
    }
)


def nfc(s: str) -> str:
    """Return Unicode NFC without changing spelling or case."""
    return unicodedata.normalize("NFC", s)


def _nfc_spans(s: str) -> tuple[str, list[tuple[int, int]]]:
    # Decomposed character queues preserve provenance through composition and
    # canonical mark reordering, including several marks on one source letter.
    origins: dict[str, deque[int]] = defaultdict(deque)
    for index, char in enumerate(s):
        for part in unicodedata.normalize("NFD", char):
            origins[part].append(index)
    normalized = nfc(s)
    spans = []
    for char in normalized:
        positions = [origins[part].popleft() for part in unicodedata.normalize("NFD", char)]
        spans.append((min(positions), max(positions) + 1))
    return normalized, spans


def _tone_placement(s: str) -> str:
    chars = list(s)
    # Digits remain part of a word; punctuation such as underscores separates it.
    for word in re.finditer(r"[^\W_]+", s):
        if len(word[0]) < 2:
            continue
        left, right = word.end() - 2, word.end() - 1
        first = unicodedata.normalize("NFD", chars[left])
        second = unicodedata.normalize("NFD", chars[right])
        cluster = (first[0] + second[0]).lower()
        if cluster not in {"oa", "oe", "uy"}:
            continue
        if cluster == "uy" and left > word.start() and chars[left - 1].lower() == "q":
            continue
        # Shaped vowels (ô, ơ, ê, ư) are different vowel clusters.
        if first[1:] or len(second) != 2 or second[1] not in _TONES:
            continue
        chars[left] = nfc(first + second[1])
        chars[right] = second[0]
    return "".join(chars)


def _key_spans(s: str) -> tuple[str, list[tuple[int, int]]]:
    normalized, source_spans = _nfc_spans(s)
    normalized = _tone_placement(normalized)
    # Lower the whole string for contextual casing (for example Greek sigma).
    # A lowercase expansion such as İ -> i + dot keeps its source character.
    spans = [span for char, span in zip(normalized, source_spans) for _ in char.lower()]
    lowered = normalized.lower()
    separated = "".join(" " if unicodedata.category(char).startswith("P") else char for char in lowered)
    pieces = []
    key_spans = []
    previous_end = 0
    for token in re.finditer(r"\S+", separated):
        if pieces:
            separators = spans[previous_end : token.start()]
            pieces.append(" ")
            key_spans.append((min(start for start, _ in separators), max(end for _, end in separators)))
        token_spans = spans[token.start() : token.end()]
        replacement = _TEENCODE.get(token[0])
        if replacement is None:
            pieces.append(token[0])
            key_spans.extend(token_spans)
        else:
            # Generated characters have no individual source character. They
            # all refer to the complete original token, including internal spaces.
            source_span = (min(start for start, _ in token_spans), max(end for _, end in token_spans))
            pieces.append(replacement)
            key_spans.extend([source_span] * len(replacement))
        previous_end = token.end()
    return "".join(pieces), key_spans


def match_key(s: str) -> tuple[str, list[int]]:
    """Return an accent-preserving key and original character start indices.

    Expanded teencode characters share their source token's start index. Use
    locate for source ranges: it also retains composed marks and token endings.
    """
    key, spans = _key_spans(s)
    return key, [start for start, _ in spans]


def locate(span: str, text: str) -> tuple[int, int] | None:
    """Find the first exact match, then the first key match, in original text."""
    start = text.find(span)
    if start >= 0:
        return start, start + len(span)
    needle, _ = match_key(span)
    if not needle:
        return None
    key, spans = _key_spans(text)
    start = key.find(needle)
    if start < 0:
        return None
    matched = spans[start : start + len(needle)]
    return min(begin for begin, _ in matched), max(end for _, end in matched)


def fold(s: str) -> str:
    """Fold accents for search only; preserve punctuation."""
    decomposed = unicodedata.normalize("NFD", s)
    bare = "".join(char for char in decomposed if not unicodedata.category(char).startswith("M"))
    return " ".join(bare.translate(str.maketrans("đĐ", "dD")).lower().split())


def law_normalize(s: str) -> str:
    """Normalize legal quotations while preserving case and tone spelling."""
    translated = nfc(s).translate(_LAW_TRANSLATION).replace("…", "...")
    return re.sub(r"\s+", " ", translated).strip()


def syllables(key: str) -> list[str]:
    """Split a matching key into syllable tokens."""
    return key.split()
