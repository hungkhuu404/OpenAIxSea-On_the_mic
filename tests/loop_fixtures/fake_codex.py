"""A subprocess fixture: no model, network or real rule drafting."""

import json
import os
from pathlib import Path
import sqlite3
import socket
import sys


capture = Path(os.environ["DRAFT_TEST_CAPTURE"])
capture.mkdir(exist_ok=True)
with (capture / "calls.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.164.0-alpha.11.1")
    raise SystemExit()
if sys.argv[1:] == ["exec", "--help"]:
    print("--sandbox --json --output-schema -o -m -c")
    raise SystemExit()

root = Path.cwd()
task = json.loads((root / "loop_input/task.json").read_text(encoding="utf-8"))
rule_id = task["rule_id"]
prompts = list(capture.glob(root.name + "-*.json"))
number = len(prompts) + 1
prompt = sys.stdin.read()
with sqlite3.connect(os.environ["DRAFT_TEST_LEDGER"]) as conn:
    job = json.loads(conn.execute("SELECT data FROM jobs WHERE job_id=?", (root.name,)).fetchone()[0])
pidfile = Path(os.environ["DRAFT_TEST_JOBS"]) / f"{root.name}.pid"
record = dict(args=sys.argv[1:], prompt=prompt, worker_pid=os.getpid(), job_pid=job["pid"],
              pidfile=pidfile.read_text(encoding="utf-8"), cwd=str(root),
              retry=(root / "loop_input/retry.md").read_text(encoding="utf-8") if number > 1 else None)
(capture / f"{root.name}-{number}.json").write_text(json.dumps(record), encoding="utf-8")
if port := os.environ.get("DRAFT_TEST_CANCEL_PORT"):
    with socket.create_connection(("127.0.0.1", int(port)), timeout=15) as connection:
        connection.sendall(b"R")
        connection.recv(1)
    raise SystemExit(0)
if os.environ.get("DRAFT_TEST_MODEL_ERROR"):
    print("model is unavailable", file=sys.stderr)
    raise SystemExit(1)

(root / "tests").mkdir(exist_ok=True)
source = ('"""chữa bệnh"""\n'
          'from rules._common import make_hit\n'
          f'RULE_ID = {rule_id!r}\nFAMILY = {task["family"]!r}\nORIGIN = "t5"\n'
          f'ANCHORS = [{task["anchor"]!r}]\nLAW_SPAN = "chữa bệnh"\nPRECEDENCE = 14\n'
          'def check(atom_view):\n    return []\n')
(root / "rules" / f"{rule_id}.py").write_text(source, encoding="utf-8")
meta = dict(id=rule_id, family=task["family"], origin="t5",
            article_id=task["anchor"].split("/K")[0], anchors=[task["anchor"]], law_span="chữa bệnh",
            source_case_ids=[s["item_id"] for s in json.loads((root / "loop_input/sources.json").read_text(encoding="utf-8"))],
            trigger="Synthetic fixture only.", precedence=14, attempts=[], revealed_case_ids=[],
            approved_by="hungkhuu404" if os.environ.get("DRAFT_TEST_APPROVED") else None,
            approved_at=None, status="proposed")
(root / "rules" / f"{rule_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
(root / "tests" / f"test_{rule_id}.py").write_text("def test_fixture():\n    assert True\n", encoding="utf-8")
output = Path(sys.argv[sys.argv.index("-o") + 1])
output.write_text(json.dumps(dict(rule_id=rule_id, summary="Synthetic fixture")), encoding="utf-8")
print(json.dumps(dict(type="item.completed", item=dict(type="file_change", changes=[
    dict(path=f"rules/{rule_id}.py"), dict(path=f"rules/{rule_id}.meta.json"),
    dict(path=f"tests/test_{rule_id}.py")]))))
raise SystemExit(int(os.environ.get("DRAFT_TEST_EXIT", "0")))
