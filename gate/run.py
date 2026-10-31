"""Structural rule gate; candidates never execute before static checks pass."""

import argparse
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from core import paths
from core.config import Settings, config_line, settings
from core.models import AtomView, GateCheck, GateResult

PURITY_IMPORTS = ("re", "unicodedata", "typing", "dataclasses", "functools", "itertools", "textnorm", "rules._common")
PURITY_FORBIDDEN = ("open", "eval", "exec", "__import__", "input", "globals", "locals", "vars", "getattr",
                    "setattr", "delattr", "compile", "breakpoint", "time", "datetime", "random", "os", "sys",
                    "socket", "subprocess", "pathlib", "io", "requests", "urllib", "http", "builtins",
                    "read_text", "read_bytes", "write_text", "write_bytes")


def _purity(tree):
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            errors.extend(f"import forbidden: {name.name}" for name in node.names if name.name not in PURITY_IMPORTS)
        elif isinstance(node, ast.ImportFrom) and (node.level or node.module not in PURITY_IMPORTS):
            errors.append(f"import forbidden: {node.module}")
        names = []
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        elif isinstance(node, ast.alias):
            names = [node.name, node.asname or ""]
        for name in names:
            if name in PURITY_FORBIDDEN or name.startswith("__"):
                errors.append(f"name forbidden: {name}")
    return list(dict.fromkeys(errors))


def _scope(candidate, rule_id, ci, base):
    if ci:
        command = ["git", "diff", "--name-only", "-z", f"{base}...HEAD"]
    else:
        command = ["git", "status", "--porcelain", "-uall", "-z"]
    result = subprocess.run(command, cwd=candidate, capture_output=True, check=True)
    entries = result.stdout.decode("utf-8").split("\0")
    changed = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        changed.append(entry if ci else entry[3:])
        if not ci and ("R" in entry[:2] or "C" in entry[:2]):
            changed.append(entries[index])
            index += 1
    own = {f"rules/{rule_id}.py", f"rules/{rule_id}.meta.json", f"tests/test_{rule_id}.py"}
    return [f"unexpected changed path: {path}" for path in changed if path not in own
            and not path.startswith("loop_input/")
            and not (ci and path.startswith(("eval/", "cache/llm/")))]


def _docstring(tree, anchors, index):
    from textnorm import law_normalize
    quote = ast.get_docstring(tree)
    if not quote:
        return ["law docstring is missing"]
    pieces = list(filter(None, (law_normalize(part) for part in re.split(r"…|\.\.\.", quote))))
    if not pieces:
        return ["law docstring contains no quotation"]
    bodies = {}
    for anchor in anchors:
        if anchor in index:
            file = index[anchor]["file"]
            body = (paths.LAW_DIR / file).read_text(encoding="utf-8").split("---", 2)[-1]
            bodies[file] = law_normalize(body)
    cursors = dict.fromkeys(bodies, 0)
    for piece in pieces:
        for file, body in bodies.items():
            position = body.find(piece, cursors[file])
            if position >= 0:
                cursors[file] = position + len(piece)
                break
        else:
            return ["docstring is not a verbatim ordered law quotation"]
    return []


def _anchors(tree, meta, rule_id, index):
    from textnorm import law_normalize
    forbidden = json.loads((Path(__file__).parent / "data/forbidden_anchors.json").read_text(encoding="utf-8"))
    blocked = forbidden["exact"] + forbidden["prefixes"]
    errors = []
    anchors = meta.get("anchors", [])
    if not anchors or not isinstance(anchors, list) or not all(isinstance(anchor, str) and anchor for anchor in anchors):
        return ["anchors must be a nonempty list"]
    for anchor in anchors:
        if anchor not in index:
            errors.append(f"unknown anchor: {anchor}")
        if any(anchor == item or anchor.startswith(item + "/") for item in blocked):
            errors.append(f"forbidden anchor: {anchor}")
    spans = meta.get("law_span", [])
    spans = [spans] if isinstance(spans, str) else spans
    if not spans or not isinstance(spans, list):
        errors.append("law_span must be a phrase or ordered list")
    else:
        for span in spans:
            if not isinstance(span, str) or not span or not any(law_normalize(span) in law_normalize(index[anchor]["text"])
                                                              for anchor in anchors if anchor in index):
                errors.append("law_span is not in any anchor")
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for name in node.targets:
                if isinstance(name, ast.Name):
                    try:
                        constants[name.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    for name, expected in (("RULE_ID", rule_id), ("FAMILY", meta.get("family")),
                           ("ORIGIN", meta.get("origin")), ("ANCHORS", anchors), ("LAW_SPAN", meta.get("law_span"))):
        if name not in constants or constants[name] != expected:
            errors.append(f"module/meta mismatch: {name}")
    if meta.get("id") != rule_id:
        errors.append("metadata id differs from candidate id")
    if type(meta.get("precedence")) is int and constants.get("PRECEDENCE") != meta["precedence"]:
        errors.append("module/meta mismatch: PRECEDENCE")
    return errors


def _test_environment():
    defaults = Settings(environ={})
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE")
           if name in os.environ}
    env.update({name: str(getattr(defaults, name)) for name in Settings.model_fields
                if name != "OPENAI_API_KEY" and getattr(defaults, name) is not None})
    env.update(OFFLINE="1", LLM_BACKEND=settings.LLM_BACKEND, PYTHONDONTWRITEBYTECODE="1")
    return env


def _own_tests(candidate, rule_id):
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", f"tests/test_{rule_id}.py"],
                            cwd=candidate, env=_test_environment(), capture_output=True,
                            encoding="utf-8", timeout=30, check=False)
    return [] if result.returncode == 0 else [f"own tests failed (exit {result.returncode})"]


def _overlap(candidate, rule_id, meta):
    if type(meta.get("precedence")) is int:
        return []
    from rules.registry import all_rules
    from textnorm import match_key, nfc
    from gate.score import case_as_of
    spec = importlib.util.spec_from_file_location(f"rules._gate_{rule_id}", candidate / "rules" / f"{rule_id}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    others = [info for info in all_rules() if info.rule_id != rule_id and info.meta.approved_by is not None
              and info.meta.status == "active" and "check" in info.module.__dict__]
    items = []
    for file in (paths.GOLDEN_CASES, paths.regressions_file()):
        if file.is_file():
            items.extend(json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip())
    for item in items:
        text = nfc(item["text"])
        view = AtomView(text=text, key=match_key(text)[0], axis=item.get("axis", "cong_dung"),
                        speaker_role=item.get("speaker_role", "khong_ro"), category_norm="unknown",
                        as_of=case_as_of(item), turn_text=text)
        if module.check(view) and any(info.module.check(view) for info in others):
            return [f"overlap on {item.get('case_id', item.get('item_id'))} requires integer precedence"]
    return []


def run_gate(rule_id, candidate_dir, log=None, sources=None, *, ci=False, base="origin/main"):
    if not re.fullmatch(r"[A-Za-z0-9_]+", rule_id):
        raise ValueError("Invalid rule id")
    candidate = Path(candidate_dir).resolve()
    checks, errors, observed, revealed = [], [], [], set()

    def record(number, problems, detail="passed"):
        checks.append(GateCheck(id=number, passed=not problems, detail="; ".join(problems) if problems else detail))
        errors.extend(f"{rule_id} check {number}: {problem}" for problem in problems)

    def attempt(number, function):
        try:
            record(number, function())
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, ImportError) as error:
            record(number, [str(error).replace("\n", " ")])

    try:
        tree = ast.parse((candidate / "rules" / f"{rule_id}.py").read_text(encoding="utf-8"))
        meta = json.loads((candidate / "rules" / f"{rule_id}.meta.json").read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("Metadata must be an object")
    except (OSError, ValueError, SyntaxError) as error:
        tree, meta = ast.parse(""), {}
        record(1, [str(error).replace("\n", " ")])
    else:
        record(1, _purity(tree))
    index = {row["id"]: row for row in json.loads((paths.LAW_DIR / "index.json").read_text(encoding="utf-8"))}
    attempt(2, lambda: _docstring(tree, meta.get("anchors", []), index))
    attempt(3, lambda: _scope(candidate, rule_id, ci, base))
    attempt(5, lambda: _anchors(tree, meta, rule_id, index))
    static_failed = any(not check.passed for check in checks)
    if static_failed:
        record(4, ["blocked by failed static checks"])
        record(6, ["blocked by failed static checks"])
    else:
        attempt(4, lambda: _own_tests(candidate, rule_id))
        attempt(6, lambda: _overlap(candidate, rule_id, meta))
    if ci or log is None:
        record(7, [], "not provided")
    else:
        from gate.logscan import scan_log
        attempt(7, lambda: scan_log(log, candidate, rule_id)[1])
    failed = sorted(check.id for check in checks if not check.passed)
    if failed:
        checks.extend(GateCheck(id=number, passed=False, detail="blocked by failed structural checks") for number in (8, 9, 10))
        errors.append(f"{rule_id}: behaviour blocked by failed checks {','.join(map(str, failed))}")
    else:
        try:
            from gate.behaviour import run_behaviour
        except ModuleNotFoundError as error:
            if error.name != "gate.behaviour":
                raise
            checks.extend(GateCheck(id=number, passed=False, detail="not ready: gate.behaviour") for number in (8, 9, 10))
            errors.append(f"{rule_id}: not ready: gate.behaviour")
        else:
            result = run_behaviour(rule_id, candidate_dir, sources=sources, ci=ci)
            checks.extend(GateCheck.model_validate(check) for check in result.checks)
            errors.extend(result.error_lines)
            observed.extend(result.observed_changes)
            revealed.update(result.revealed_case_ids)
    if ci:
        checks = [GateCheck(id=check.id, passed=True, detail="not provided") if check.id == 8 else check for check in checks]
    golden_ids = [json.loads(line)["case_id"] for line in paths.GOLDEN_CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    revealed.update(case_id for case_id in golden_ids if any(case_id in error for error in errors))
    return GateResult(passed=all(check.passed for check in checks) and not errors,
                      checks=sorted(checks, key=lambda check: check.id), error_lines=errors,
                      observed_changes=observed, revealed_case_ids=sorted(revealed))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--log")
    parser.add_argument("--sources")
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    print(config_line(), file=sys.stderr)
    result = run_gate(args.rule, args.candidate_dir, log=args.log, sources=args.sources, ci=args.ci, base=args.base)
    print(result.model_dump_json())
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
