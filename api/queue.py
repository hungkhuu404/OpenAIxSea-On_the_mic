"""Risk ranking and durable draft state; neither changes a verdict."""
import json
import re
from datetime import datetime
from core.paths import clusters_dir, regressions_file
import ledger


def clusters():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(clusters_dir().glob("*.json"))]


def regressions():
    path = regressions_file()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def draft_state(item_ids, jobs, files):
    local = [j.model_dump(mode="json") for j in jobs if set(j.cluster_item_ids) & set(item_ids)]
    runs = local or [r | {"ts": r["started"], "status": r["result"]} for c in files
                     if set(c["item_ids"]) & set(item_ids) for r in c["runs"]]
    if not runs:
        return None
    newest = max(runs, key=lambda r: (datetime.fromisoformat(r["ts"]), r["job_id"]))
    return {"failed": "Draft failed", "error": "Draft error", "queued": "Drafting",
            "running": "Drafting", "passed": "Draft passed"}[newest["status"]]


def ranked_queue():
    from textnorm import locate, match_key
    memory, files, jobs = regressions(), clusters(), ledger.list_jobs()
    labelled = {(r.get("session_id"), r.get("atom_id")) for r in memory}
    labels = {r["target"] for r in ledger.list_labels() if r["kind"] == "label"}
    result = []
    groups = [("pregnancy", 6, ("bầu", "mang thai", "cho con bú")),
              ("children", 5, ("trẻ em", "em bé")),
              ("disease", 3, ("tiểu đường", "đái tháo đường", "ung thư", "gan", "thận", "huyết áp", "tim mạch", "mỡ máu", "gout", "xương khớp")),
              ("absolute", 2, ("hoàn toàn", "tuyệt đối", "100%", "khỏi hẳn", "dứt điểm"))]
    for session in ledger.list_sessions():
        verdicts = {v.atom_key: v for v in ledger.effective_verdicts(session.session_id)}
        for atom in session.atoms:
            key = f"{session.session_id}:{atom.atom_id}"
            verdict = verdicts.get(key)
            if not verdict or verdict.verdict != "abstain" or (session.session_id, atom.atom_id) in labelled or key in labels:
                continue
            text, _ = match_key(atom.span)
            parts = [{"text": name, "score": score} for name, score, words in groups
                     if any(re.search(r"(?<!\w)" + re.escape(match_key(w)[0]) + r"(?!\w)", text) for w in words)]
            if "PREV_RED" in verdict.flags:
                parts.append({"text": "previously Prohibited", "score": 6})
            if verdict.reason_code == "DOSSIER_FIELD_MISSING" and atom.axis == "doi_tuong":
                parts.append({"text": "missing audience dossier field", "score": 2})
            for i, hit in enumerate(atom.near_hits):
                left, _, right = hit.pattern.partition("…")
                prefix = locate(left.strip(), hit.span)
                suffix = locate(right.strip(), hit.span[prefix[1]:]) if prefix and right.strip() else None
                gap = hit.span[prefix[1]:prefix[1] + suffix[0]].strip() if suffix else "unavailable"
                anchor = hit.law_anchor.replace("LDUOC-2016/D6/K15", "Luật Dược Art. 6 cl. 15")
                parts.append({"text": f"near hit: {anchor} '{hit.pattern}' (gap: '{gap}')", "score": 4 if i == 0 else 0})
            ids = [key] + [r["item_id"] for r in memory if (r.get("session_id"), r.get("atom_id")) == (session.session_id, atom.atom_id)]
            result.append({"atom_key": key, "atom": atom.model_dump(mode="json"), "verdict": verdict.model_dump(mode="json"),
                           "sku_id": session.sku_pin, "cluster_key": f"{session.sku_pin}:{verdict.reason_code}:{atom.axis}",
                           "source": session.source, "source_line": next(t.text for t in session.turns if t.turn_id == atom.turn_id),
                           "chips": parts, "score": sum(p["score"] for p in parts), "score_max": sum(g[1] for g in groups) + 6 + 2 + 4, "draft_state": draft_state(ids, jobs, files)})
    return sorted(result, key=lambda r: (-r["score"], r["atom_key"]))
