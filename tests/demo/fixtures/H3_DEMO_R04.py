"""chữa bệnh"""

RULE_ID = "H3_DEMO_R04"
FAMILY = "R04"
ORIGIN = "t5"
ANCHORS = ["LDUOC-2016/D6/K15"]
LAW_SPAN = "chữa bệnh"
PRECEDENCE = 35


def check(atom_view):
    from core.models import RuleHit

    phrase = "làm tan mọi bệnh"
    if phrase not in atom_view.text:
        return []
    return [RuleHit(rule_id=RULE_ID, family=FAMILY, matched=phrase,
                    law_anchor=ANCHORS[0], law_span=LAW_SPAN,
                    precedence=PRECEDENCE, ambiguous_negation=False)]
