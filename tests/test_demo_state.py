"""Rehearsal state, process isolation and handoff on synthetic checkouts."""
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import config, paths
import ledger
from scripts import demo_state as demo, demo_safety as safety


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'; root.mkdir()
    monkeypatch.setattr(paths, 'REPO_ROOT', root)
    monkeypatch.setattr(paths, 'LOCAL_DIR', root / '.local')
    monkeypatch.setattr(paths, 'CACHE_DIR', root / 'cache/llm')
    monkeypatch.setattr(paths, 'KERA_DIR', root / 'data/demo/kera')
    config.set_override('LEDGER_DB', '.local/ledger.db')
    config.set_override('EVAL_DIR', 'eval')
    config.set_override('WORKTREE_ROOT', str(tmp_path / 'worktrees'))
    config.set_override('LLM_LOCAL_CACHE_DIR', '.local/cache')
    config.set_override('PRESENT_DIR', str(tmp_path / 'assets'))
    config.set_override('LLM_BACKEND', 'codex')
    config.set_override('OFFLINE', 1)
    monkeypatch.setitem(sys.modules, 'rules.registry', SimpleNamespace(all_rules=lambda: []))
    def fields():
        return dict(backend='codex', offline=config.settings.OFFLINE, judge=config.settings.JUDGE_MODE,
            s1='synthetic/none', judge_model=config.settings.JUDGE_MODEL, draft='synthetic/low',
            public=config.settings.PUBLIC_MODE, video=config.settings.VIDEO_MODE, loop_dry=config.settings.LOOP_DRY)
    monkeypatch.setattr(config, 'config_fields', fields)
    safety.git('init', '-b', 'main')
    safety.git('config', 'user.name', 'Fixture Owner')
    safety.git('config', 'user.email', 'fixture@example.invalid')
    safety.write(root / 'rules/R_SYNTH.meta.json', {'id': 'R_SYNTH'})
    (root / 'rules/R_SYNTH.py').write_text('synthetic = True\n', encoding='utf-8')
    safety.git('add', 'rules'); safety.git('commit', '-m', 'Synthetic fixture')
    safety.write(Path(config.settings.PRESENT_DIR) / 'present.json', {'labeler': 'operator', 'approver': 'operator', 'other': 'keep'})
    safety.write(paths.eval_dir() / 'clusters/original.json', {'item_ids': []})
    ledger.init_db(paths.ledger_db())
    return root


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_snapshot_restore_mirror_and_worktree_boundary(world, tmp_path):
    from tests.test_ledger import make_session, make_atom, make_verdict
    ledger.add_session(make_session()); ledger.add_atoms('synthetic', [make_atom()])
    ledger.append_verdicts([make_verdict()])
    demo.snapshot('saved')
    ledger.append_verdicts([make_verdict().model_dump(mode='json') | {'flags': ['RECALLED_AFTER']}])
    ledger.add_label({'target': 'synthetic', 'kind': 'report_wrong', 'labeler': 'operator'})
    safety.write(paths.clusters_dir() / 't5-round.json', {'item_ids': ['synthetic']})
    safety.write(world / 'rules/R_SYNTH.meta.json', {'id': 'changed'})
    safety.write(demo.present_path(), {'labeler': 'changed'})
    own = paths.worktree_root() / 'owned'
    outside = tmp_path / 'outside'
    safety.git('worktree', 'add', '-b', 'rule/R_NEW', str(own))
    safety.git('worktree', 'add', '-b', 'rule/R_OUTSIDE', str(outside))
    demo.restore('saved', free_port())
    assert ledger.list_labels() == []
    assert ledger.get_session('synthetic').verdicts[0].flags == []
    assert not (paths.clusters_dir() / 't5-round.json').exists()
    assert safety.read(world / 'rules/R_SYNTH.meta.json') == {'id': 'R_SYNTH'}
    assert safety.read(demo.present_path())['labeler'] == 'operator'
    assert not own.exists() and outside.exists()
    assert 'rule/R_NEW' not in safety.git_state()['branches']


def test_restore_guards_and_config(world, monkeypatch, capsys):
    manifest = demo.snapshot('saved')
    assert manifest['config_line']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        with pytest.raises(ValueError, match='Stop the app'):
            demo.restore('saved', sock.getsockname()[1])
    for field, value in [('JUDGE_MODE', 'off'), ('JUDGE_MODEL', 'different')]:
        old = getattr(config.settings, field)
        config.set_override(field, value)
        with pytest.raises(ValueError, match='Configuration mismatch'):
            demo.restore('saved', free_port())
        config.set_override(field, old)
    config.set_override('LOOP_DRY', 1)
    demo.restore('saved', free_port())
    safety.git('checkout', '--orphan', 'unrelated')
    safety.git('commit', '--allow-empty', '-m', 'Unrelated fixture')
    with pytest.raises(ValueError, match='git pull first'):
        demo.restore('saved', free_port())


def test_recorded_process_is_the_only_one_stopped(world):
    from tests.test_ledger_read import job
    ledger.create_job(job('synthetic'))
    demo.snapshot('saved')
    children = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=os.name != 'nt') for _ in range(2)]
    try:
        file = paths.jobs_dir(create=True) / 'synthetic.pid'
        file.write_text(str(children[0].pid), encoding='utf-8')
        with pytest.raises(ValueError, match=str(children[0].pid)):
            demo.restore('saved', free_port())
        assert children[0].poll() is None and children[1].poll() is None
        safety.stop_jobs()
        children[0].wait(timeout=10)
        assert children[1].poll() is None and not file.exists()
        assert ledger.get_job('synthetic').status == 'error'
        assert 'cancelled by prepare' in ledger.get_job('synthetic').attempts[0].error_lines
    finally:
        for child in children:
            if child.poll() is None: child.kill()
            child.wait(timeout=10)


def fake_pipeline(monkeypatch, world):
    from pipeline.ingest import from_text
    def run(sid, purpose):
        session = ledger.get_session(sid)
        if session.verdicts: return
        text = session.turns[0].text
        atoms = [dict(atom_id=f't01.a{i}', turn_id='t01', span=span, start=start, end=start+len(span),
            axis='cong_dung', speaker_role='kol', subject_is_product=True,
            near_hits=[] if i == 1 else [dict(pattern='shared pattern', gap=1, law_anchor='synthetic', law_span='synthetic', span=span)])
            for i, (span, start) in enumerate([('dose', 0), ('pattern', 5)], 1)]
        ledger.add_atoms(sid, atoms)
        ledger.append_verdicts([dict(atom_key=f'{sid}:t01.a{i}', rule_pack_version=1, verdict='abstain',
            decided_by='abstain:DOSSIER_EMPTY', reason_code='DOSSIER_EMPTY', as_of=session.as_of,
            dossier_hash='synthetic', ts='2026-10-31T00:00:00Z') for i in (1, 2)])
    ingest = SimpleNamespace(from_text=from_text, from_reenactment=lambda path, sku, when: from_text(sku, when, 'dose pattern', 'Synthetic'))
    monkeypatch.setitem(sys.modules, 'pipeline.ingest', ingest)
    monkeypatch.setitem(sys.modules, 'pipeline.runner', SimpleNamespace(process_session=run))
    def label(target, value, rule, labeler, auto_trigger):
        assert auto_trigger is False
        ledger.add_label(dict(target=target, kind='label', label=value, rule_id=rule, labeler=labeler))
    monkeypatch.setitem(sys.modules, 'loop.labels', SimpleNamespace(record_label=label))
    monkeypatch.setattr('api.sessions.ensure_clip_session', lambda: [])
    safety.write(world / 'tests/demo/pr1_sentences.json', {'sku_id': 'demo_sku', 'as_of': '2026-10-31',
        'source': 'Synthetic', 'sentences': ['dose pattern one', 'dose pattern two', 'dose pattern three']})


def test_prepare_twice_and_key_l_reset(world, monkeypatch):
    fake_pipeline(monkeypatch, world)
    for _ in range(2):
        demo.prepare('R_EXCLUDED', free_port())
        assert len(ledger.list_labels()) == 2 and ledger.list_jobs() == []
        assert safety.read(demo.present_path())['pr1_third_target'].endswith(':t01.a2')
        assert not list(world.glob('eval.*.bak'))
        assert list((paths.LOCAL_DIR / 'prepare-backups').glob('*/eval'))
    safety.write(paths.clusters_dir() / 't5-round.json', {'item_ids': ['third']})
    demo.restore('stage', free_port())
    assert not (paths.clusters_dir() / 't5-round.json').exists()
    safety.write(paths.clusters_dir() / 't5-round.json', {'item_ids': ['third']})
    assert (paths.clusters_dir() / 't5-round.json').exists()
    config.set_override('OFFLINE', 0)
    with pytest.raises(ValueError, match='offline=1'):
        demo.prepare(port=free_port())


def test_target_selection_rejects_zero_and_ambiguous(world, monkeypatch):
    fake_pipeline(monkeypatch, world)
    ingest = demo.require('pipeline.ingest')
    session = ingest.from_text('demo', '2026-10-31', 'dose pattern', 'Synthetic')
    ledger.add_session(session); demo.require('pipeline.runner').process_session(session.session_id, 'live')
    view = ledger.get_session(session.session_id)
    assert demo.select_targets([('synthetic sentence', view)])[0].endswith('a2')
    view.atoms[0].near_hits = list(view.atoms[1].near_hits)
    with pytest.raises(ValueError, match='synthetic sentence'):
        demo.select_targets([('synthetic sentence', view)])
    view.atoms = []
    with pytest.raises(ValueError, match='synthetic sentence'):
        demo.select_targets([('synthetic sentence', view)])


def test_export_import_cache_conflicts_and_no_git(world, monkeypatch, tmp_path):
    # A synthetic clip atom links exactly one content-addressed cache row.
    from tests.test_ledger import make_session, make_atom, make_verdict
    ledger.add_session(make_session('clip-a1'))
    atom = make_atom().model_copy(update={'s1_call_id': 'a' * 64})
    ledger.add_atoms('clip-a1', [atom])
    cache = paths.local_cache_dir() / 'aa' / ('a' * 64 + '.json')
    safety.write(cache, {'request_sha256': 'a' * 64})
    safety.write(demo.present_path(), {'labeler': 'source', 'pr1_third_target': 'third'})
    demo.snapshot('stage')
    handoff = tmp_path / 'handoff'
    demo.export('stage', handoff)
    second = tmp_path / 'second'; second.mkdir()
    shutil.copytree(world / 'rules', second / 'rules')
    monkeypatch.setattr(paths, 'REPO_ROOT', second)
    monkeypatch.setattr(paths, 'LOCAL_DIR', second / '.local')
    config.set_override('PRESENT_DIR', str(tmp_path / 'second-assets'))
    safety.write(Path(config.settings.PRESENT_DIR) / 'present.json', {'labeler': 'destination', 'other': 'keep'})
    demo.import_state(handoff); demo.import_state(handoff)
    assert safety.read(demo.present_path()) == {'labeler': 'destination', 'other': 'keep', 'pr1_third_target': 'third'}
    demo.restore('stage', free_port())
    assert len(ledger.get_session('clip-a1').atoms) == 1
    safety.write(paths.local_cache_dir() / 'aa' / cache.name, {'different': True})
    with pytest.raises(ValueError, match='Conflicting'):
        demo.import_state(handoff)


@pytest.mark.parametrize('command', ['snapshot', 'restore', 'prepare', 'export', 'import'])
def test_public_refuses_every_command(world, command):
    config.set_override('PUBLIC_MODE', 1)
    args = {'snapshot': ['--name', 'x'], 'restore': ['--name', 'x'], 'prepare': [],
        'export': ['--name', 'x', '--to', 'unused'], 'import': ['--from', 'unused']}[command]
    with pytest.raises(SystemExit) as error:
        demo.main([command, *args])
    assert error.value.code == 1


def test_stage_banner_and_missing_rule(world, capsys):
    demo.snapshot('stage'); demo.record('stage')
    config.set_override('UI_STAGE', 1)
    config.set_override('JUDGE_MODE', 'off')
    demo.stage_warning()
    assert 'STAGE CONFIGURATION MISMATCH' in capsys.readouterr().out
    config.set_override('JUDGE_MODE', 'two')
    (world / 'rules/R_SYNTH.py').unlink()
    with pytest.raises(ValueError, match='git pull first'):
        demo.restore('stage', free_port())


def test_restore_preflights_present_before_mutating_ledger(world):
    demo.snapshot('saved')
    ledger.add_label(dict(target='synthetic', kind='report_wrong', labeler='operator'))
    config.set_override('PRESENT_DIR', None)
    with pytest.raises(ValueError, match='PRESENT_DIR'):
        demo.restore('saved', free_port())
    assert len(ledger.list_labels()) == 1


def test_missing_module_exact_only(world, monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name=name)
    monkeypatch.setattr(demo, 'import_module', missing)
    with pytest.raises(ValueError, match='not ready: pipeline.runner'):
        demo.require('pipeline.runner')
    monkeypatch.setattr(demo, 'import_module', lambda name: missing('internal_dependency'))
    with pytest.raises(ModuleNotFoundError):
        demo.require('pipeline.runner')


def test_copy_without_git_and_stale_pid(world, monkeypatch):
    # The path is the fixture's known .git directory, never the user's checkout.
    repository = (world / '.git').resolve()
    assert repository.parent == world.resolve()
    moved = (world / '.fixture-git').resolve()
    assert moved.parent == world.resolve()
    repository.rename(moved)
    fake_pipeline(monkeypatch, world)
    paths.jobs_dir(create=True).joinpath('stale.pid').write_text('2147483646', encoding='utf-8')
    demo.prepare(port=free_port())
    assert safety.read(demo.snapshot_path('stage') / 'manifest.json')['head'] is None
    demo.restore('stage', free_port())
    assert len(ledger.list_labels()) == 2
