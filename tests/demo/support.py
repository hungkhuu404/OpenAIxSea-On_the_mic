"""Assertions and reporting helpers; no production decision logic."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from time import perf_counter


def record(value):
    return value if isinstance(value, dict) else value.model_dump(mode="json")


def select_pr1_target(sentence, atoms, verdicts, pattern="phòng … bệnh"):
    by_id = {row["atom_key"].split(":", 1)[1]: row for row in map(record, verdicts)}
    selected = []
    for atom in map(record, atoms):
        verdict = by_id[atom["atom_id"]]
        if (verdict["verdict"] == "abstain" and verdict["reason_code"] == "DOSSIER_EMPTY"
                and any(record(hit)["pattern"] == pattern for hit in atom["near_hits"])):
            selected.append(atom)
    if len(selected) != 1:
        raise AssertionError(f"PR1 sentence {sentence!r}: expected one target; observed {len(selected)}")
    return selected[0]


def read_expected(path):
    if not path:
        raise AssertionError("DEMO_EXPECTED is unset; a partial rehearsal cannot pass")
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise AssertionError("DEMO_EXPECTED is unreadable or invalid JSON") from None
    return value


def expected_agreement(expected, session, verdicts, match_key):
    """Count agreement only; never return expected text or a local path."""
    changed = {"status": "expected file shape changed"}
    if not isinstance(expected, dict):
        return changed
    runs = expected.get("runs")
    if not isinstance(runs, dict) or not isinstance(runs.get("2025-01-08"), dict):
        return changed
    expected_turns = runs["2025-01-08"].get("turns")
    if not isinstance(expected_turns, list):
        return changed
    by_turn = {}
    for atom in session["atoms"]:
        by_turn.setdefault(atom["turn_id"], []).append(atom)
    by_atom = {row["atom_key"].split(":", 1)[1]: row for row in verdicts}
    turn_matches = turn_total = atom_matches = atom_total = 0
    reasons = {}
    for row in verdicts:
        reason = row["reason_code"]
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    severity = {"green": 0, "abstain": 1, "yellow": 2, "red": 3}
    for turn in expected_turns:
        if (not isinstance(turn, dict) or not isinstance(turn.get("atoms"), list)
                or not isinstance(turn.get("turn"), str)
                or "proposed_turn_verdict" not in turn):
            return changed
        actual_atoms = by_turn.get(turn.get("turn"), [])
        actual_rows = [by_atom[a["atom_id"]] for a in actual_atoms if a["atom_id"] in by_atom]
        colour = turn.get("proposed_turn_verdict")
        if colour is not None and (not isinstance(colour, str) or colour not in severity):
            return changed
        if colour in severity:
            turn_total += 1
            if actual_rows:
                observed = max((v["verdict"] for v in actual_rows), key=severity.__getitem__)
                turn_matches += observed == colour
        used = set()
        for wanted in turn["atoms"]:
            if not isinstance(wanted, dict):
                return changed
            if (not isinstance(wanted.get("proposed_verdict"), str)
                    or wanted["proposed_verdict"] not in severity or not isinstance(wanted.get("span"), str)):
                return changed
            atom_total += 1
            key, _ = match_key(wanted["span"])
            turn_text = next((t["text"] for t in session["turns"] if t["turn_id"] == turn.get("turn")), "")
            whole_key, offset_map = match_key(turn_text)
            starts = [match.start() for match in re.finditer(re.escape(key), whole_key)] if key else []
            if not starts:
                continue
            # Compare located character ranges in the same turn. Repeated
            # spans are kept distinct by preferring the greatest overlap.
            scored = []
            for atom in actual_atoms:
                if atom["atom_id"] in used:
                    continue
                positions = {i for i, p in enumerate(offset_map) if atom["start"] <= p < atom["end"]}
                overlap = max(len(set(range(start, start + len(key))) & positions)
                              / len(set(range(start, start + len(key))) | positions) for start in starts)
                if overlap >= 0.5 and atom["atom_id"] in by_atom:
                    scored.append((overlap, atom["atom_id"]))
            if scored:
                _, atom_id = max(scored)
                used.add(atom_id)
                atom_matches += by_atom[atom_id]["verdict"] == wanted["proposed_verdict"]
    return {"status": "compared", "turn_matches": turn_matches, "turn_total": turn_total,
            "atom_matches": atom_matches, "atom_total": atom_total, "reason_counts": reasons}


@dataclass
class DemoReport:
    path: Path
    config: str = ""
    scenes: dict = field(default_factory=dict)
    soft: list = field(default_factory=list)
    started: float = field(default_factory=perf_counter)

    def add(self, scene, status, detail=""):
        # A skip/failure during setup has no call report, so it is retained.
        self.scenes[scene] = (status, re.sub(r"\s+", " ", detail).strip())

    def render(self):
        lines = [self.config or "Configuration unavailable: demo setup failed", "", "Scene | Result | Detail", "--- | --- | ---"]
        for scene, (status, detail) in self.scenes.items():
            lines.append(f"{scene} | {status} | {detail.replace('|', '/')}")
        counts = {status: sum(row[0] == status for row in self.scenes.values()) for status in ("PASS", "FAIL", "SKIP")}
        lines.extend(["", " · ".join(f"{key} {value}" for key, value in counts.items()),
                      f"Total: {(perf_counter() - self.started) * 1000:.3f} ms", "", "Soft checks (report only)"])
        for value in self.soft:
            lines.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return "\n".join(lines) + "\n"

    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.render(), encoding="utf-8", newline="\n")
