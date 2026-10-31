"""chữa bệnh"""

from rules._common import make_hit

RULE_ID = "R04x"
FAMILY = "R04"
ORIGIN = "t5"
ANCHORS = ["LDUOC-2016/D6/K15"]
LAW_SPAN = "chữa bệnh"
PRECEDENCE = 14


def check(atom_view):
    if "demo trigger" in atom_view.text:
        return [make_hit(RULE_ID, FAMILY, "demo trigger", ANCHORS[0], LAW_SPAN, PRECEDENCE)]
    return []
