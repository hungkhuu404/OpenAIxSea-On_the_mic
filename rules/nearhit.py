"""Source-bound review hints for interrupted legal phrases; never verdicts."""

from rules._common import excluded_right, is_disclaimer, negation_scope, syllable_matches

LAW_ANCHOR = "LDUOC-2016/D6/K15"
PATTERNS = (
    ("phòng … bệnh", ("phòng",), "bệnh", "phòng bệnh"),
    ("chữa … bệnh", ("chữa",), "bệnh", "chữa bệnh"),
    ("trị … bệnh", ("trị",), "bệnh", "điều trị bệnh"),
    ("ngừa … bệnh", ("ngừa",), "bệnh", "phòng bệnh"),
    ("giảm nhẹ … bệnh", ("giảm", "nhẹ"), "bệnh", "giảm nhẹ bệnh"),
    ("thay … thuốc", ("thay",), "thuốc", "chữa bệnh"),
)
ROOM_CONTEXT = ("viện", "nhân", "án", "xá")


def detect(atom_view):
    from core.models import NearHit
    from textnorm import match_key, syllables

    text = atom_view.text
    if is_disclaimer(text):
        return []
    words = syllables(match_key(text)[0])
    candidates = {}
    for pattern, left, right, law_span in PATTERNS:
        for start in range(len(words)):
            if tuple(words[start:start + len(left)]) != left:
                continue
            for gap in (1, 2):
                end = start + len(left) + gap
                if end < len(words) and words[end] == right:
                    phrase = " ".join(words[start:end + 1])
                    candidates[phrase] = pattern, gap, law_span
    hits = []
    for match in syllable_matches(text, candidates):
        pattern, gap, law_span = candidates[match.phrase]
        if pattern == "phòng … bệnh" and (
            match.phrase == "phòng khám bệnh" or excluded_right(text, match, ROOM_CONTEXT)
        ):
            continue
        negated, _ = negation_scope(text, match.start, match.end)
        if not negated:
            hits.append(NearHit(pattern=pattern, gap=gap, law_anchor=LAW_ANCHOR,
                                law_span=law_span, span=match.matched))
    return hits
