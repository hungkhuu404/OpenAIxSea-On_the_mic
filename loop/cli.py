"""Freeze correction clusters and run bounded, explicitly selected draft jobs."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

from core import paths
from core.config import config_line
from core.models import ClusterFile, Job
from loop import draft


def make_cluster(item_ids, name):
    draft._slug(name)
    if not item_ids or len(item_ids) != len(set(item_ids)):
        raise ValueError("Choose distinct correction-memory item ids")
    wanted = set(item_ids)
    rows = {}
    for line in paths.regressions_file().read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if item["item_id"] in wanted:
                if item["item_id"] in rows:
                    raise ValueError("Duplicate correction-memory item id")
                rows[item["item_id"]] = item
    if rows.keys() != wanted:
        raise ValueError("Unknown correction-memory item ids")
    items = [rows[key] for key in item_ids]
    canonical = json.dumps(sorted((i["item_id"], i["text"]) for i in items),
                           ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    path = paths.clusters_dir() / f"{name}.json"
    with draft._cluster_lock(path):
        if path.exists():
            raise ValueError("Cluster name already exists")
        if any(draft._read(p)["cluster_hash"] == digest for p in paths.clusters_dir().glob("*.json")):
            raise ValueError("This exact cluster already has a file; repeat that file instead")
        cluster = ClusterFile(name=name, item_ids=item_ids, texts=[i["text"] for i in items],
            labels=[i["label"] for i in items], cluster_hash=digest, created_at=draft._now(),
            commit=None, runs=[])
        commit = draft._save_cluster(path, cluster)
    return path, digest, commit


def run_cluster(path, repeat=1):
    from ledger import append_event, create_job
    from loop.t5 import allocate_rule_id

    if repeat < 1:
        raise ValueError("Repeat must be positive")
    path = paths.resolve_path(path)
    if path.parent != paths.clusters_dir().resolve() or path.suffix != ".json":
        raise ValueError("Cluster file must be directly inside EVAL_DIR/clusters")
    if not paths.ledger_db().is_file():
        raise ValueError("LEDGER_DB must name an existing ledger file")
    cluster = ClusterFile.model_validate(draft._read(path))
    rule_id = (cluster.runs[0].rule_id if cluster.runs
               else allocate_rule_id(draft._family(draft._source_items(cluster))))
    jobs = []
    for _ in range(repeat):
        job_id = uuid4().hex
        create_job(Job(job_id=job_id, kind="draft_rule", rule_id=rule_id,
            branch=f"rule/{rule_id}-{job_id}", cluster_key=cluster.name,
            cluster_item_ids=cluster.item_ids, cluster_hash=cluster.cluster_hash,
            status="queued", dry=False, config="", ts=draft._now()))
        append_event("job.started", dict(job_id=job_id, rule_id=rule_id, cluster_key=cluster.name))
        job = draft.run_job(job_id)
        jobs.append(job)
        print(json.dumps(dict(job_id=job.job_id, rule_id=job.rule_id, branch=job.branch,
                             status=str(job.status), attempts=[a.model_dump(mode="json") for a in job.attempts]),
                         ensure_ascii=False))
    print(json.dumps(draft.funnel(cluster.cluster_hash)))
    return jobs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("make-cluster")
    freeze.add_argument("--items", required=True)
    freeze.add_argument("--name", required=True)
    run = commands.add_parser("run-cluster")
    run.add_argument("--file", type=Path, required=True)
    run.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args(argv)
    print(config_line())
    try:
        if args.command == "make-cluster":
            path, digest, commit = make_cluster(args.items.split(","), args.name)
            print(json.dumps(dict(cluster_hash=digest, commit=commit, file=str(path)), ensure_ascii=False))
        else:
            run_cluster(args.file, args.repeat)
    except ModuleNotFoundError as exc:
        if exc.name != "loop.t5":
            raise
        print("not ready: loop.t5", file=sys.stderr)
        return 1
    except (ValueError, KeyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
