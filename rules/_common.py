"""Source-bound matching and record builders shared by pure rules."""

import re
from dataclasses import dataclass


DISCLAIMER = "Thực phẩm này không phải là thuốc và không có tác dụng thay thế thuốc chữa bệnh"
NEGATIONS = ("không", "chẳng", "chưa", "đừng")


@dataclass(frozen=True)
class SyllableMatch:
    phrase: str
    matched: str
    start: int
    end: int


def syllable_matches(text, phrases):
    """Return whole, contiguous syllable runs in source order."""
    from textnorm import match_key

    key, offsets = match_key(text)
    matches = []
    for phrase in phrases:
        phrase_key, _ = match_key(phrase)
        for match in re.finditer(r"(?<!\w)" + re.escape(phrase_key) + r"(?!\w)", key):
            start = offsets[match.start()]
            end = offsets[match.end() - 1] + 1
            matches.append(SyllableMatch(phrase, text[start:end], start, end))
    return sorted(matches, key=lambda match: (match.start, match.end))


def excluded_right(text, match, words):
    from textnorm import match_key

    key, _ = match_key(text[match.end:])
    following = re.match(r"\s*(\w+)", key)
    return following is not None and following.group(1) in words


def is_disclaimer(text):
    """Use LCS, including gaps, rather than a greedy syllable scan."""
    from textnorm import match_key, syllables

    reference = syllables(match_key(DISCLAIMER)[0])
    observed = syllables(match_key(text)[0])
    previous = [0] * (len(reference) + 1)
    for word in observed:
        current = [0]
        for index, expected in enumerate(reference, 1):
            current.append(previous[index - 1] + 1 if word == expected
                           else max(previous[index], current[-1]))
        previous = current
    return previous[-1] * 4 >= len(reference) * 3


def negation_scope(text, start, end):
    """Return (clearly negated, ambiguous) within the trigger's clause."""
    from textnorm import match_key, syllables

    boundaries = list(re.finditer(r"[,;.]+|\b(?:nhưng|mà)\b", text, re.IGNORECASE))
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.start() for m in boundaries if m.start() >= end), default=len(text))
    preceding = syllables(match_key(text[left:start])[0])
    clause = syllables(match_key(text[left:right])[0])
    clear = any(word in NEGATIONS for word in preceding[-3:])
    return clear, not clear and any(word in NEGATIONS for word in clause)


def make_hit(rule_id, family, matched, law_anchor, law_span, precedence,
             ambiguous_negation=False):
    from core.models import RuleHit

    return RuleHit(rule_id=rule_id, family=family, matched=matched,
                   law_anchor=law_anchor, law_span=law_span, precedence=precedence,
                   ambiguous_negation=ambiguous_negation)


def make_session_flag(flag_code, rule_id, legal_basis, message):
    from core.models import SessionFlag

    return SessionFlag(flag_code=flag_code, rule_id=rule_id, verdict="red",
                       legal_basis=legal_basis, message=message)
