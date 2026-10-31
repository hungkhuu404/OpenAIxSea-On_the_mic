"""Deterministic correction-memory clusters and isolated draft-job dispatch."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from threading import RLock, Thread
from uuid import uuid4

from core import paths
from core.config import config_fields, settings
from core.models import ClusterFile, Job, RegressionItem

_LOCK = RLock()


def _clusters():
    return [ClusterFile.model_validate_json(file.read_text(encoding="utf-8"))
            for file in sorted(paths.clusters_dir().glob("*.json"))]


def allocate_rule_id(family):
    import ledger
    with _LOCK:
        used = {file.name.split(".")[0] for file in (paths.REPO_ROOT / "rules").glob("*")}
        used.update(job.rule_id for job in ledger.list_jobs())
        used.update(run.rule_id for cluster in _clusters() for run in cluster.runs)
        for suffix in "bcdefghijklmnopqrstuvwxyz":
            if family + suffix not in used:
                return family + suffix
        raise ValueError(f"No free lowercase rule suffix for {family}")


def _similar(left, right):
    def grams(text):
        return Counter(text[index:index + 3] for index in range(len(text) - 2))
    a, b = grams(left), grams(right)
    denominator = math.sqrt(sum(count * count for count in a.values()) * sum(count * count for count in b.values()))
    return sum(count * b[gram] for gram, count in a.items()) / denominator if denominator else float(left == right)


def maybe_trigger():
    import ledger
    from rules.registry import all_rules
    with _LOCK:
        file = paths.regressions_file()
        if not file.exists():
            return []
        used = {item_id for cluster in _clusters() for item_id in cluster.item_ids}
        families = {rule.rule_id: rule.meta.family for rule in all_rules()}
        items = {item.item_id: item for line in file.read_text(encoding="utf-8").splitlines() if line.strip()
                 for item in [RegressionItem.model_validate_json(line)]}
        groups = {}
        for item in sorted(items.values(), key=lambda item: (not bool(item.near_hit_patterns), item.item_id)):
            if item.item_id in used or not item.rule_ids or item.origin_verdict is None:
                continue
            origin = item.origin_verdict
            key = (origin.reason_code if origin.verdict == "abstain" else origin.decided_by,
                   item.label, families[item.rule_ids[0]])
            subgroups = groups.setdefault(key, [])
            if item.near_hit_patterns:
                for pattern in sorted(set(item.near_hit_patterns)):
                    group = next((members for marker, members in subgroups if marker == pattern), None)
                    if group is None:
                        group = []
                        subgroups.append((pattern, group))
                    group.append(item)
            else:
                group = next((members for _, members in subgroups if all(
                    _similar(item.text, member.text) >= settings.T5_TAU for member in members)), None)
                if group is None:
                    group = []
                    subgroups.append((None, group))
                group.append(item)
        jobs = []
        for key, subgroups in groups.items():
            for _, members in subgroups:
                members = [item for item in members if item.item_id not in used]
                if len(members) < 3:
                    continue
                from loop.draft import run_job
                pairs = sorted((item.item_id, item.text) for item in members)
                digest = hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
                name = "t5-" + digest[:8]
                ts = datetime.now(timezone.utc).isoformat()
                cluster = ClusterFile(name=name, item_ids=[item.item_id for item in members],
                    texts=[item.text for item in members], labels=[item.label for item in members],
                    cluster_hash=digest, created_at=ts, commit="", runs=[])
                rule_id = allocate_rule_id(key[2])
                job_id = uuid4().hex
                cluster_key = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
                job = Job(job_id=job_id, rule_id=rule_id, branch=f"rule/{rule_id}-{job_id}",
                    cluster_key=cluster_key, cluster_item_ids=cluster.item_ids, cluster_hash=digest,
                    status="queued", dry=bool(settings.LOOP_DRY), config=str(config_fields()["cfg"]), ts=ts)
                paths.clusters_dir().mkdir(parents=True, exist_ok=True)
                (paths.clusters_dir() / (name + ".json")).write_text(cluster.model_dump_json(indent=2) + "\n", encoding="utf-8")
                ledger.create_job(job)
                ledger.append_event("t5.cluster", dict(cluster_key=cluster_key, size=len(members), item_ids=cluster.item_ids))
                ledger.append_event("job.started", dict(job_id=job_id, rule_id=rule_id, cluster_key=cluster_key))
                Thread(target=run_job, args=(job_id,), daemon=True).start()
                used.update(cluster.item_ids)
                jobs.append(job)
        return jobs
