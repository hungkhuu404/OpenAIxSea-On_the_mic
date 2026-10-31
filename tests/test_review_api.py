"""Synthetic Review behavior, with unavailable collaborators replaced at their boundary."""
import asyncio
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from api.app import create_app
from api import review
from core import config
from core.models import Atom, Job, RuleMeta, Verdict
from core.paths import clusters_dir, regressions_file
import ledger
from tests.test_ledger import make_atom, make_session, make_verdict

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def dry_offline():
    config.set_override("OFFLINE", 1)
    config.set_override("LLM_BACKEND", "off")
    config.set_override("LOOP_DRY", 1)


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


def atom_item(sid, text, flags=(), reason="LLM_OFF", near_hits=(), axis="thanh_phan"):
    ledger.add_session(make_session(sid, text))
    atom = Atom.model_validate(make_atom(text, start=0).model_dump() | {"near_hits": list(near_hits), "axis": axis})
    ledger.add_atoms(sid, [atom])
    verdict = Verdict.model_validate(make_verdict().model_dump() | {"atom_key": f"{sid}:t01.a1", "flags": list(flags), "reason_code": reason})
    ledger.append_verdicts([verdict])
    return verdict.atom_key


def write_cluster(data):
    clusters_dir().mkdir(parents=True, exist_ok=True)
    (clusters_dir() / "pr2.json").write_text(json.dumps(data), encoding="utf-8")


def fake_draft(monkeypatch):
    calls = []
    def funnel(cluster_hash):
        assert_no_event_loop()
        calls.append(cluster_hash)
        runs = [dict(result=j.status, attempts=j.attempts) for j in ledger.list_jobs() if j.cluster_hash == cluster_hash]
        return review.portable_funnel(runs)
    monkeypatch.setitem(sys.modules, "loop.draft", SimpleNamespace(funnel=funnel))
    return calls


def assert_no_event_loop():
    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()


def test_queue_risk_order_labels_effective_and_diacritics(client):
    for sid, text, flags in [("pregnancy", "Dùng cho người mang thai", ()), ("child", "Dùng cho trẻ em", ()),
                              ("old", "Một câu mới", ("PREV_RED",)), ("plain", "Một câu thường", ()),
                              ("unaccented", "Dung cho nguoi bau", ())]:
        atom_item(sid, text, flags)
    rows = client.get('/api/queue').json()
    scores = {r['atom_key'].split(':')[0]: r['score'] for r in rows}
    assert scores == dict(pregnancy=6, child=5, old=6, plain=0, unaccented=0)
    assert [r['atom_key'] for r in rows] == sorted([r['atom_key'] for r in rows], key=lambda k: (-scores[k.split(':')[0]], k))
    regressions_file().parent.mkdir(parents=True, exist_ok=True)
    regressions_file().write_text(json.dumps(dict(session_id="pregnancy", atom_id="t01.a1", item_id="labelled")) + '\n', encoding="utf-8")
    assert not any(r['atom_key'].startswith('pregnancy:') for r in client.get('/api/queue').json())
    # A verdict for a future pack must not hide the effective abstain.
    future = Verdict.model_validate(make_verdict(2).model_dump() | {"atom_key": "plain:t01.a1", "verdict": "green", "decided_by": "allow_list"})
    ledger.append_verdicts([future])
    assert any(r['atom_key'] == 'plain:t01.a1' for r in client.get('/api/queue').json())
    ledger.create_pack_version([], [], 'merge')
    assert not any(r['atom_key'] == 'plain:t01.a1' for r in client.get('/api/queue').json())


def test_queue_all_score_parts(client):
    from core.models import NearHit
    hit = NearHit(pattern="phòng … bệnh", gap=1, law_anchor="LDUOC-2016/D6/K15", law_span="phòng bệnh", span="phòng các bệnh")
    atom_item('risk', 'trẻ em mang thai tiểu đường hoàn toàn phòng các bệnh', ("PREV_RED",), 'DOSSIER_FIELD_MISSING', [hit], 'doi_tuong')
    row = client.get('/api/queue').json()[0]
    assert row['score'] == 28
    assert any("gap: 'các'" in c['text'] for c in row['chips'])
    assert row['source'] == 'Synthetic'
    assert row['verdict']['verdict'] == 'abstain'


@pytest.mark.parametrize('portable', [False, True])
@pytest.mark.parametrize('status,title', [('failed', 'Draft failed'), ('error', 'Draft error'), ('running', 'Drafting'), ('passed', 'Draft passed')])
def test_draft_state_survives_reload(client, portable, status, title):
    key = atom_item('draft', 'Một câu chưa rõ')
    if portable:
        data = fixture('cluster_fake.json'); data['item_ids'] = [key]; data['runs'][0]['result'] = status
        write_cluster(data)
    else:
        job = fixture('job_fake.json') | dict(status=status, cluster_item_ids=[key])
        ledger.create_job(Job.model_validate(job))
    before = ledger.effective_verdicts('draft')[0].model_dump()
    assert client.get('/api/queue').json()[0]['draft_state'] == title
    with TestClient(create_app()) as fresh:
        assert fresh.get('/api/queue').json()[0]['draft_state'] == title
    assert ledger.effective_verdicts('draft')[0].model_dump() == before
    assert ledger.list_labels() == []


@pytest.mark.parametrize('auto_trigger', [None, False, True])
def test_label_passthrough_worker(client, monkeypatch, auto_trigger):
    seen = []
    def record(*args):
        assert_no_event_loop(); seen.append(args); return {'item_id': 'label-one'}
    monkeypatch.setitem(sys.modules, 'loop.labels', SimpleNamespace(record_label=record))
    body = dict(target='s:a', label='red', rule_id='R04', labeler='operator')
    if auto_trigger is not None:
        body['auto_trigger'] = auto_trigger
    assert client.post('/api/labels', json=body).status_code == 200
    assert seen == [('s:a', 'red', 'R04', 'operator', auto_trigger is not False)]


def test_worker_routes_and_approval(client, monkeypatch, tmp_path):
    golden = tmp_path / "public-synthetic.jsonl"
    golden.write_text('{}\n{}\n', encoding="utf-8")
    monkeypatch.setattr(review, "GOLDEN_CASES", golden)
    seen = []
    def work(*args):
        assert_no_event_loop(); seen.append(args); return {'ok': True}
    monkeypatch.setitem(sys.modules, 'loop.merge', SimpleNamespace(merge=work, dry_run=work))
    monkeypatch.setitem(sys.modules, 'loop.labels', SimpleNamespace(record_report_wrong=work))
    for body in ({}, {'approved_by': ''}, {'approved_by': None}, {'approved_by': '  '}):
        assert client.post('/api/rules/R04/merge', json=body).status_code == 400
    dry = client.get('/api/rules/R04/dryrun')
    assert dry.status_code == 200
    assert dry.json()['scope']['golden_cases'] == 2
    assert dry.json()['scope']['claims'] == 0
    assert dry.json()['scope']['measured_at']
    assert client.post('/api/rules/R04/merge', json={'approved_by': 'operator'}).status_code == 200
    assert client.post('/api/report-wrong', json=dict(verdict_id=1, reporter='operator')).status_code == 200
    assert seen == [('R04',), ('R04', 'operator'), (1, 'operator', None)]


@pytest.mark.parametrize("module_state", ["missing", "trimmed", "legacy"])
def test_deferred_dossier_routes_are_always_503(client, monkeypatch, module_state):
    def forbidden(*args, **kwargs):
        pytest.fail("Deferred dossier operation was invoked")

    module = None if module_state == "missing" else SimpleNamespace()
    if module_state == "legacy":
        module.propose_dossier_update = forbidden
        module.approve_pending = forbidden
    monkeypatch.setitem(sys.modules, "pipeline.dossier_events", module)
    for path, body in [
        ("/api/dossier-events/update", {"sku_id": "demo_sku", "change": {}}),
        ("/api/pending/p1/approve", {"approved_by": "operator"}),
        ("/api/dossier-events/update", {}),
        ("/api/pending/p1/approve", {}),
    ]:
        response = client.post(path, json=body)
        assert response.status_code == 503
        assert response.json() == {"todo": "pipeline.dossier_events"}


@pytest.mark.parametrize("transitive", ["internal_dependency", "textnorm", "rules.registry"])
def test_missing_dependency_exact_only(client, monkeypatch, transitive):
    real = review.importlib.import_module
    def missing(name):
        if name == 'loop.labels':
            raise ModuleNotFoundError(name=name)
        return real(name)
    monkeypatch.setattr(review.importlib, 'import_module', missing)
    response = client.post('/api/labels', json=dict(target='s:a', label='red', labeler='operator'))
    assert response.status_code == 503 and response.json() == {'todo': 'loop.labels'}
    def broken(name):
        raise ModuleNotFoundError(name=transitive)
    monkeypatch.setattr(review.importlib, 'import_module', broken)
    with pytest.raises(ModuleNotFoundError) as exc:
        client.post('/api/labels', json=dict(target='s:a', label='red', labeler='operator'))
    assert exc.value.name == transitive


@pytest.mark.parametrize('portable', [False, True])
def test_funnel_excludes_nonfinal(client, monkeypatch, portable):
    fake_draft(monkeypatch)
    data = fixture('cluster_fake.json')
    runs = []
    for index, status in enumerate(['error', 'queued', 'running', 'passed', 'passed', 'failed']):
        run = copy.deepcopy(data['runs'][0]); run.update(job_id=f'job-{index}', result=status)
        if index == 3:
            run['attempts'] = run['attempts'][:1]; run['attempts'][0]['gate_result'] = 'pass'
        runs.append(run)
        if not portable:
            job = fixture('job_fake.json') | dict(job_id=run['job_id'], status=status)
            job['attempts'] = job['attempts'][:len(run['attempts'])]
            ledger.create_job(Job.model_validate(job))
    data['runs'] = runs
    write_cluster(data)
    meta = RuleMeta.model_validate(fixture('rule_meta_fake.json'))
    monkeypatch.setitem(sys.modules, 'rules.registry', SimpleNamespace(all_rules=lambda: [SimpleNamespace(rule_id='R04_DEMO', meta=meta)]))
    monkeypatch.setitem(sys.modules, 'pipeline.guardrail', SimpleNamespace(display_text=lambda anchor: 'chữa bệnh'))
    response = client.get('/api/rules/R04_DEMO')
    assert response.status_code == 200
    assert response.json()['funnel'] == dict(K=3, I=1, J=1, R=1)
    if portable:
        assert response.json()['attempts']
        assert response.json()['sources'][0]['text'] == data['texts'][0]
        data['runs'] = runs[:1]; write_cluster(data)
        assert client.get('/api/rules/R04_DEMO').json()['funnel'] == dict(K=0, I=0, J=0, R=0)
    else:
        assert client.get('/api/jobs/job-0').json()['dry'] is True
        assert client.get('/api/jobs/missing').status_code == 404


def test_who_decided_latest_and_empty(client, monkeypatch, tmp_path):
    monkeypatch.setattr(review, 'REPORTS_DIR', tmp_path)
    atom_item('live', 'Một câu')
    data = client.get('/api/who-decided').json()
    assert data['oracle'] is None
    assert data['live']['human'] == data['live']['denominator'] == 1
    for stamp in ['2026-10-21T09:00:00Z', '2026-10-21T10:00:00Z']:
        (tmp_path / f'oracle_{stamp[11:13]}.json').write_text(json.dumps({'measured_at': stamp, 'standin': True}), encoding='utf-8')
    assert client.get('/api/who-decided').json()['oracle']['measured_at'].endswith('10:00:00Z')


def test_public_write_routes(monkeypatch):
    from api import public
    monkeypatch.setattr(public, 'prepare_replay', lambda: ledger.init_db(config.settings.LEDGER_DB))
    with TestClient(create_app(public=True)) as client:
        for path in ['/labels', '/report-wrong', '/rules/R04/merge', '/dossier-events/update', '/pending/p1/approve']:
            assert client.post('/api' + path, json={}).status_code == 403


@pytest.mark.parametrize("status", ["error", "queued", "running"])
def test_local_nonfinal_alone_has_empty_funnel(client, monkeypatch, status):
    calls = fake_draft(monkeypatch)
    stored = fixture("job_fake.json") | {"status": status}
    ledger.create_job(Job.model_validate(stored))
    result = client.get("/api/jobs/" + stored["job_id"]).json()
    assert result["funnel"] == dict(K=0, I=0, J=0, R=0)
    assert result["config"] == stored["config"]
    assert result["dry"] is True
    assert result["attempts"] == stored["attempts"]
    assert calls == [stored["cluster_hash"]]


def test_stage_uses_cluster_filename_and_label_alias(client):
    key = atom_item("stage", "Một câu")
    regressions_file().parent.mkdir(parents=True, exist_ok=True)
    regressions_file().write_text(json.dumps({"item_id": "item-one", "session_id": "stage", "atom_id": "t01.a1"}) + "\n", encoding="utf-8")
    ledger.create_job(Job.model_validate(fixture("job_fake.json") | {"cluster_item_ids": ["item-one"]}))
    data = fixture("cluster_fake.json")
    data["name"] = "different-name"
    data["runs"][0]["result"] = "failed"
    write_cluster(data)
    stage = client.get("/api/review-stage", params={"target": key}).json()
    assert stage["pr1_job_id"] == "demo-job-1"
    assert stage["pr2"]["runs"][0]["result"] == "failed"
    assert stage["pr2"]["funnel"] == dict(K=1, I=0, J=0, R=1)


def test_newest_draft_and_original_gap_text(client):
    from core.models import NearHit
    hit = NearHit(pattern="phòng … bệnh", gap=1, law_anchor="LDUOC-2016/D6/K15", law_span="phòng bệnh", span="PHÒNG CÁC BỆNH")
    key = atom_item("latest", hit.span, near_hits=[hit])
    for number, status in [(1, "passed"), (2, "failed")]:
        ledger.create_job(Job.model_validate(fixture("job_fake.json") | {
            "job_id": f"job-{number}", "cluster_item_ids": [key], "status": status,
            "ts": f"2026-10-21T0{number}:00:00Z"}))
    item = client.get("/api/queue").json()[0]
    assert item["draft_state"] == "Draft failed"
    assert "gap: 'CÁC'" in item["chips"][0]["text"]
    assert item["atom"]["span"] == hit.span



def test_real_record_only_label(client, monkeypatch):
    import importlib
    try:
        labels = importlib.import_module("loop.labels")
    except ModuleNotFoundError as exc:
        if exc.name != "loop.labels":
            raise
        pytest.skip("not ready: loop.labels")
    config.set_override("REGRESSION_AUTOCOMMIT", 0)
    session = make_session("record-only", "Một câu chưa rõ").model_copy(update={"sku_pin": "demo_sku"})
    ledger.add_session(session)
    ledger.add_atoms(session.session_id, [make_atom(session.turns[0].text, start=0)])
    ledger.append_verdicts([Verdict.model_validate(make_verdict().model_dump() | {"atom_key": "record-only:t01.a1"})])
    response = client.post("/api/labels", json=dict(target="record-only:t01.a1", label="red", rule_id="R04", labeler="operator", auto_trigger=False))
    assert response.status_code == 200
    assert response.json()["session_id"] == "record-only"
    assert ledger.list_jobs() == []
    assert ledger.list_labels()[0]["labeler"] == "operator"
    assert client.get("/api/queue").json() == []
    assert ledger.effective_verdicts(session.session_id)[0].verdict == "abstain"
    assert client.post("/api/labels", json=dict(target="unknown:a", label="red", labeler="operator", auto_trigger=False)).status_code == 400
