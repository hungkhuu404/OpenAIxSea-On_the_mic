"""Review API, using durable ledger and portable cluster provenance."""
import asyncio
import importlib
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Body, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from core.enums import Verdict
from core.paths import GOLDEN_CASES, REPORTS_DIR
from api.queue import clusters, ranked_queue, regressions
import ledger

router = APIRouter(prefix="/api")


class LabelRequest(BaseModel):
    target: str = Field(min_length=1)
    label: Verdict
    rule_id: str | None = None
    labeler: str = Field(min_length=1)
    auto_trigger: bool = True


class ReportRequest(BaseModel):
    verdict_id: int
    reporter: str = Field(min_length=1)
    correct_label: Verdict | None = None


class DossierRequest(BaseModel):
    sku_id: str = Field(min_length=1)
    change: dict


class DependencyNotReady(Exception):
    def __init__(self, module):
        super().__init__(module)
        self.module = module


async def work(module, function, *args):
    def call():
        try:
            dependency = importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name == module:
                raise DependencyNotReady(module) from exc
            raise
        return dependency.__dict__[function](*args)
    return await asyncio.to_thread(call)


def portable_funnel(runs):
    final = [r for r in runs if r["result"] in {"passed", "failed"}]
    first = sum(r["result"] == "passed" and len(r["attempts"]) == 1 for r in final)
    retry = sum(r["result"] == "passed" and len(r["attempts"]) > 1 for r in final)
    return {"K": len(final), "I": first, "J": retry, "R": sum(r["result"] == "failed" for r in final)}


def portable_attempts(runs, meta_attempts):
    """Keep every run, enriching only matching metadata without inventing times."""
    remaining = list(meta_attempts)
    attempts = []
    for run in sorted(runs, key=lambda r: datetime.fromisoformat(r["started"])):
        for attempt in run["attempts"]:
            match = next((a for a in remaining if a["gate_result"] == attempt["gate_result"]
                          and a["error_lines"] == attempt["error_lines"]), None)
            if match is not None:
                remaining.remove(match)
            attempts.append((match or {}) | attempt | {"job_id": run["job_id"], "run_started": run["started"]})
    return attempts + remaining


def source_items(files, item_ids=()):
    memory = {r["item_id"]: r for r in regressions()}
    sources = {item: {"item_id": item, "text": text, "ts": c["created_at"],
                      "source_note": memory.get(item, {}).get("source_note")}
               for c in files for item, text in zip(c["item_ids"], c["texts"])}
    for item_id in item_ids:
        if item_id in memory and item_id not in sources:
            item = memory[item_id]
            sources[item_id] = {"item_id": item_id, "text": item["text"], "source_note": item.get("source_note"), "ts": item["ts"]}
    return list(sources.values())


async def job_payload(job):
    data = job.model_dump(mode="json")
    data["sources"] = source_items([c for c in clusters() if c["cluster_hash"] == job.cluster_hash], job.cluster_item_ids)
    data["funnel"] = await work("loop.draft", "funnel", job.cluster_hash)
    return data


@router.get("/queue")
def queue():
    return ranked_queue()


@router.post("/labels")
async def label(body: LabelRequest):
    return await work("loop.labels", "record_label", body.target, body.label, body.rule_id, body.labeler, body.auto_trigger)


@router.post("/report-wrong")
async def report_wrong(body: ReportRequest):
    return await work("loop.labels", "record_report_wrong", body.verdict_id, body.reporter, body.correct_label)


@router.get("/jobs")
async def jobs():
    result = []
    for job in ledger.list_jobs():
        payload = await job_payload(job)
        result.append(payload)
    return result


@router.get("/jobs/{id}")
async def job(id: str):
    stored = ledger.get_job(id)
    if stored is None:
        raise HTTPException(404, "Job not found")
    return await job_payload(stored)


@router.get("/rules")
async def rules():
    result = await work("rules.registry", "all_rules")
    return [{"rule_id": r.rule_id, "meta": r.meta.model_dump(mode="json")} for r in result]


@router.get("/rules/{id}")
async def rule(id: str):
    listing = await rules()
    data = next((r for r in listing if r["rule_id"] == id), None)
    if data is None:
        raise HTTPException(404, "Rule not found")
    files = clusters()
    related = [c for c in files if any(r["rule_id"] == id for r in c["runs"])]
    runs = [r for c in related for r in c["runs"] if r["rule_id"] == id]
    local = ledger.list_jobs(rule_id=id)
    data["jobs"] = []
    for stored in local:
        payload = await job_payload(stored)
        data["jobs"].append(payload)
    data["runs"] = runs
    if local:
        data["attempts"] = [a | {"job_id": j["job_id"]} for j in data["jobs"] for a in j["attempts"]]
    else:
        data["attempts"] = portable_attempts(runs, data["meta"]["attempts"])
    data["funnel"] = data["jobs"][0]["funnel"] if local else portable_funnel(runs)
    ids = data["meta"]["source_case_ids"] + [item for j in local for item in j.cluster_item_ids]
    data["sources"] = source_items(related, ids)
    data["law"] = []
    for anchor in data["meta"]["anchors"]:
        text = await work("pipeline.guardrail", "display_text", anchor)
        spans = data["meta"]["law_span"]
        spans = [spans] if isinstance(spans, str) else spans
        data["law"].append({"anchor": anchor, "text": text, "law_span": next((s for s in spans if s in text), None)})
    return data


@router.get("/rules/{id}/dryrun")
async def dryrun(id: str):
    data = jsonable_encoder(await work("loop.merge", "dry_run", id))
    counts = [len(ledger.effective_verdicts(s.session_id)) for s in ledger.list_sessions()]
    data["scope"] = {"cases": sum(n > 0 for n in counts), "claims": sum(counts),
                     "golden_cases": sum(bool(line.strip()) for line in GOLDEN_CASES.read_text(encoding="utf-8").splitlines()),
                     "measured_at": datetime.now(timezone.utc).isoformat()}
    return data


def approver(body):
    value = body.get("approved_by")
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        raise HTTPException(400, "approved_by is required")
    return value


@router.post("/rules/{id}/merge")
async def merge(id: str, body: dict = Body(default={})):
    return await work("loop.merge", "merge", id, approver(body))


@router.post("/dossier-events/update")
async def update(body: DossierRequest):
    return await work("pipeline.dossier_events", "propose_dossier_update", body.sku_id, body.change)


@router.get("/pending")
def pending():
    return ledger.list_pending(status="pending")


@router.post("/pending/{id}/approve")
async def approve(id: str, body: dict = Body(default={})):
    return await work("pipeline.dossier_events", "approve_pending", id, approver(body))


@router.get("/who-decided")
async def who_decided(session_id: str | None = None):
    ids = [session_id] if session_id else [s.session_id for s in ledger.list_sessions()]
    live = await work("pipeline.aggregate", "who_decided", [v for sid in ids for v in ledger.effective_verdicts(sid)])
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in REPORTS_DIR.glob("oracle_*.json")]
    report = max(reports, key=lambda r: datetime.fromisoformat(r["measured_at"])) if reports else None
    return {"live": live, "oracle": report}


@router.get("/review-stage")
def review_stage(target: str = ""):
    from core.paths import clusters_dir
    path = clusters_dir() / "pr2.json"
    pr2 = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if pr2:
        pr2["funnel"] = portable_funnel(pr2["runs"])
        pr2["sources"] = source_items([pr2])
    ids = {target}
    for item in regressions():
        if f"{item.get('session_id')}:{item.get('atom_id')}" == target:
            ids.add(item["item_id"])
    pr1 = next((job for job in ledger.list_jobs() if ids.intersection(job.cluster_item_ids)), None)
    return {"pr2": pr2, "pr1_job_id": pr1.job_id if pr1 else None}
