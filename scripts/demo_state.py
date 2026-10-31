"""Local rehearsal snapshots and portable, verified clip-cache handoff."""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import hashlib
from importlib import import_module
import json
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory

from core import config, paths
import ledger
from scripts import demo_safety as safety


def snapshot_path(name):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('Invalid snapshot name')
    return paths.snapshots_dir() / name


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def backup(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def copy_tree(source, destination):
    for relative, file in safety.files(source).items():
        target = safety.inside(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)


def present_path():
    if not config.settings.PRESENT_DIR:
        raise ValueError('PRESENT_DIR is required')
    path = Path(config.settings.PRESENT_DIR) / 'present.json'
    if not path.is_file():
        raise ValueError('PRESENT_DIR/present.json is required')
    return path


def record(name):
    safety.write(paths.snapshots_dir() / 'last.json', {'name': name})


def snapshot(name):
    safety.guard()
    target = snapshot_path(name)
    if target.exists():
        raise ValueError('Snapshot exists; move it aside by hand')
    if not paths.ledger_db().is_file():
        raise ValueError('Ledger does not exist')
    paths.snapshots_dir(create=True)
    with TemporaryDirectory(dir=paths.snapshots_dir(), prefix='snapshot-') as directory:
        folder = Path(directory)
        backup(paths.ledger_db(), folder / 'ledger.db')
        copy_tree(paths.eval_dir(), folder / 'eval')
        for meta in (paths.REPO_ROOT / 'rules').glob('*.meta.json'):
            copy_tree_file(meta, folder / 'rules' / meta.name)
        if config.settings.PRESENT_DIR:
            copy_tree_file(present_path(), folder / 'present.json')
        fields = config.config_fields()
        with closing(sqlite3.connect(folder / 'ledger.db')) as conn:
            pack = json.loads(conn.execute('SELECT data FROM rule_packs ORDER BY version DESC LIMIT 1').fetchone()[0])['version']
        manifest = safety.git_state() | {'name': name, 'time': datetime.now(timezone.utc).isoformat(),
            'pack_version': pack, 'config_fields': fields,
            'config_line': ' '.join(f'{key}={value}' for key, value in fields.items())}
        safety.write(folder / 'manifest.json', manifest)
        folder.rename(target)
    return manifest


def copy_tree_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def restore(name, port=8000):
    safety.guard()
    safety.port_free(port)
    running = safety.jobs()
    if running:
        raise ValueError('\n'.join(f'Running job {file.stem} PID {pid}: {safety.stop_command(pid)}' for file, pid in running))
    folder = snapshot_path(name)
    manifest = safety.read(folder / 'manifest.json')
    safety.check_config(manifest)
    current = safety.git_state()
    if manifest['head'] and current['head'] and safety.git('merge-base', '--is-ancestor', manifest['head'], current['head'], check=False).returncode:
        raise ValueError('git pull first: snapshot HEAD is not an ancestor')
    metas = safety.files(folder / 'rules')
    for relative in metas:
        if '/' in relative or not relative.endswith('.meta.json'):
            raise ValueError('Invalid rule metadata member')
        if not (paths.REPO_ROOT / 'rules' / relative.replace('.meta.json', '.py')).is_file():
            raise ValueError('git pull first: missing rule module ' + relative)
    # Validate all deletion targets before changing any state.
    destination = eval_root()
    existing = safety.files(destination)
    wanted = safety.files(folder / 'eval')
    present_target = present_path() if (folder / 'present.json').is_file() else None
    cleanup = []
    if current['head'] and manifest['head']:
        for tree in current['worktrees']:
            location = Path(tree['worktree']).resolve()
            if tree.get('branch', '').startswith('refs/heads/rule/') and location.is_relative_to(paths.worktree_root()) and tree not in manifest['worktrees']:
                cleanup.append((location, tree['branch'].removeprefix('refs/heads/')))
        protected = {tree.get('branch', '').removeprefix('refs/heads/') for tree in current['worktrees'] if Path(tree['worktree']).resolve() not in {row[0] for row in cleanup}}
    else:
        protected = set()
    backup(folder / 'ledger.db', paths.ledger_db())
    for meta in (paths.REPO_ROOT / 'rules').glob('*.meta.json'):
        if meta.name not in metas:
            meta.unlink()
    for relative, file in metas.items():
        copy_tree_file(file, paths.REPO_ROOT / 'rules' / relative)
    for relative, file in existing.items():
        if relative not in wanted:
            file.unlink()
    copy_tree(folder / 'eval', destination)
    if present_target:
        copy_tree_file(folder / 'present.json', present_target)
    for location, _ in cleanup:
        safety.git('worktree', 'remove', '--force', str(location))
    if current['head'] and manifest['head']:
        for branch in set(current['branches']) - set(manifest['branches']) - protected:
            safety.git('branch', '-D', branch)
    record(name)
    with closing(sqlite3.connect(paths.ledger_db())) as conn:
        verdicts = conn.execute('SELECT COUNT(*) FROM verdicts').fetchone()[0]
        labels = conn.execute('SELECT COUNT(*) FROM labels').fetchone()[0]
    print(f"pack={manifest['pack_version']} verdicts={verdicts} labels={labels} running_jobs=0")
    print(config.config_line())


def require(module):
    try:
        return import_module(module)
    except ModuleNotFoundError as error:
        if error.name != module:
            raise
        raise ValueError('not ready: ' + module) from error


def eval_root():
    root = paths.eval_dir().resolve()
    if (root == paths.REPO_ROOT.resolve() or root in paths.REPO_ROOT.resolve().parents
            or paths.ledger_db().is_relative_to(root)
            or paths.snapshots_dir().resolve().is_relative_to(root)
            or root.is_relative_to(paths.snapshots_dir().resolve())):
        raise ValueError('Unsafe EVAL_DIR')
    return root


def select_targets(sessions):
    candidates = []
    for text, session in sessions:
        verdicts = {row.atom_key: row for row in session.verdicts}
        patterns = {}
        for atom in session.atoms:
            verdict = verdicts.get(f'{session.session_id}:{atom.atom_id}')
            if verdict and verdict.verdict == 'abstain' and verdict.reason_code == 'DOSSIER_EMPTY':
                for pattern in {hit.pattern for hit in atom.near_hits}:
                    patterns.setdefault(pattern, []).append(atom)
        candidates.append((text, session, patterns))
    common = set.intersection(*(set(row[2]) for row in candidates)) if candidates else set()
    if len(common) != 1:
        raise ValueError('PR1 sentences have no unique shared pattern: ' + '; '.join(row[0] for row in candidates))
    pattern, = common
    result = []
    for text, session, patterns in candidates:
        if len(patterns[pattern]) != 1:
            raise ValueError('PR1 sentence has ambiguous targets: ' + text)
        result.append(f'{session.session_id}:{patterns[pattern][0].atom_id}')
    return result


def prepare(exclude_rule=None, port=8000):
    safety.guard()
    print(config.config_line())
    if not config.settings.OFFLINE or config.settings.REGRESSION_AUTOCOMMIT or config.settings.LOOP_OPEN_PR:
        raise ValueError('prepare requires offline=1 autocommit=0 open_pr=0')
    safety.port_free(port)
    present = present_path()
    options = safety.read(present)
    if not options.get('labeler'):
        raise ValueError('present.json requires labeler')
    backends = {safety.read(file)['backend'] for file in paths.CACHE_DIR.glob('*/*.json')}
    if backends - {config.settings.LLM_BACKEND}:
        raise ValueError('Cache backend differs from LLM_BACKEND; backend is part of the cache key')
    ingest, runner, labels = (require(name) for name in ('pipeline.ingest', 'pipeline.runner', 'loop.labels'))
    safety.stop_jobs()
    evaluation = eval_root()
    safety.files(evaluation)
    if evaluation.exists():
        archived = paths.LOCAL_DIR / 'prepare-backups' / stamp() / 'eval'
        archived.parent.mkdir(parents=True, exist_ok=True)
        evaluation.rename(archived)
    old = paths.ledger_db()
    if old.exists():
        # Checkpoint WAL before moving the old database aside.
        with closing(sqlite3.connect(old)) as conn:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        old.rename(old.with_name(old.name + '.' + stamp() + '.bak'))
    ledger.init_db(old, exclude_rules=[exclude_rule] if exclude_rule else [])
    for as_of in (date(2025, 1, 8), date(2024, 12, 12)):
        session = ingest.from_reenactment(paths.KERA_DIR / 'reenactment_script.md', 'kera', as_of)
        ledger.add_session(session)
        runner.process_session(session.session_id, 'live')
    from api.sessions import ensure_clip_session
    for sid in ensure_clip_session():
        session = ledger.get_session(sid)
        print(f'{sid} atoms={len(session.atoms)} LLM_OFF={sum(row.reason_code == "LLM_OFF" for row in session.verdicts)}')
    spec = safety.read(paths.REPO_ROOT / 'tests' / 'demo' / 'pr1_sentences.json')
    prepared = []
    for text in spec['sentences']:
        session = ingest.from_text(spec['sku_id'], spec['as_of'], text, spec['source'])
        ledger.add_session(session)
        runner.process_session(session.session_id, 'live')
        prepared.append((text, ledger.get_session(session.session_id)))
    targets = select_targets(prepared)
    if len(targets) != 3:
        raise ValueError('PR1 requires three sentences')
    for target in targets[:2]:
        labels.record_label(target, 'red', 'R04', options['labeler'], auto_trigger=False)
    if ledger.list_jobs():
        raise ValueError('prepare unexpectedly created jobs')
    if exclude_rule and exclude_rule in ledger.current_pack().active:
        raise ValueError('Excluded rule remains active')
    used_ids = set(targets)
    if paths.regressions_file().exists():
        for line in paths.regressions_file().read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if f"{row.get('session_id')}:{row.get('atom_id')}" in used_ids:
                used_ids.add(row['item_id'])
    for file in paths.clusters_dir().glob('*.json'):
        if used_ids & set(safety.read(file)['item_ids']):
            raise ValueError('Prepared items already used by a cluster')
    options['pr1_third_target'] = targets[2]
    safety.write(present, options)
    stage = snapshot_path('stage')
    if stage.exists():
        stage.rename(stage.with_name('stage-' + stamp()))
    snapshot('stage')
    record('stage')


def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def export(name, destination):
    safety.guard()
    folder = snapshot_path(name)
    destination = Path(destination).resolve()
    if destination.is_relative_to(folder.resolve()):
        raise ValueError('Export destination must be outside the snapshot')
    contents = {f'snapshot/{relative}': file for relative, file in safety.files(folder).items()}
    if (folder / 'present.json').exists():
        contents['present.json'] = folder / 'present.json'
    ids = set()
    with closing(sqlite3.connect(folder / 'ledger.db')) as conn:
        for (data,) in conn.execute("SELECT data FROM atoms WHERE session_id IN ('clip-a1','clip-a2')"):
            ids.add(json.loads(data).get('s1_call_id'))
        for (data,) in conn.execute("SELECT data FROM verdicts WHERE atom_key LIKE 'clip-a1:%' OR atom_key LIKE 'clip-a2:%'"):
            ids.update(json.loads(data)['llm_call_ids'])
        hashes = set(ids)
        for (data,) in conn.execute('SELECT data FROM llm_calls'):
            row = json.loads(data)
            if row['request_sha256'] in ids:
                hashes.add(row['request_sha256'])
    for relative, file in safety.files(paths.local_cache_dir()).items():
        if file.suffix == '.json' and re.fullmatch(r'[0-9a-f]{64}', file.stem) and safety.read(file).get('request_sha256') in hashes:
            contents['cache/' + relative] = file
    manifest = {'name': name, 'files': {relative: digest(file) for relative, file in contents.items()}}
    marker = destination / 'transfer.json'
    if marker.exists() and safety.read(marker) != manifest:
        raise ValueError('Export manifest conflicts with existing content')
    publish(contents, destination)
    safety.write(marker, manifest)


def publish(contents, destination):
    for relative, file in contents.items():
        target = safety.inside(destination, relative)
        if target.exists() and digest(target) != digest(file):
            raise ValueError('Conflicting existing file: ' + relative)
    for relative, file in contents.items():
        target = safety.inside(destination, relative)
        publish_one(file, target)


def publish_one(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open('xb') as output, source.open('rb') as input_file:
            shutil.copyfileobj(input_file, output)
    except FileExistsError:
        if digest(target) != digest(source):
            raise ValueError('Conflicting existing file: ' + target.name) from None


def import_state(source):
    safety.guard()
    source = Path(source).resolve()
    transfer = safety.read(source / 'transfer.json')
    if not {'snapshot/manifest.json', 'snapshot/ledger.db', 'snapshot/present.json'} <= transfer['files'].keys():
        raise ValueError('Incomplete handoff snapshot')
    name = transfer['name']
    destination = snapshot_path(name)
    contents = {}
    for relative, checksum in transfer['files'].items():
        file = safety.inside(source, relative)
        if digest(file) != checksum:
            raise ValueError('Transfer checksum mismatch: ' + relative)
        if relative.startswith('snapshot/'):
            target = safety.inside(destination, relative[9:])
        elif relative.startswith('cache/'):
            target = safety.inside(paths.local_cache_dir(), relative[6:])
        elif relative == 'present.json':
            continue
        else:
            raise ValueError('Unknown transfer member')
        contents[target] = file
    if destination.exists():
        actual = {relative: digest(file) for relative, file in safety.files(destination).items()}
        expected = {relative[9:]: checksum for relative, checksum in transfer['files'].items() if relative.startswith('snapshot/')}
        if actual != expected:
            raise ValueError('Snapshot differs; move it aside by hand')
    options_path = present_path()
    options = safety.read(options_path)
    new = safety.read(source / 'snapshot' / 'present.json').get('pr1_third_target')
    for target, file in contents.items():
        if target.exists() and digest(target) != digest(file):
            raise ValueError('Conflicting existing file: ' + target.name)
    for target, file in contents.items():
        publish_one(file, target)
    old = options.get('pr1_third_target')
    options['pr1_third_target'] = new
    safety.write(options_path, options)
    print(f'pr1_third_target: {old} -> {new}')


def stage_warning():
    marker = paths.snapshots_dir() / 'last.json'
    if config.settings.UI_STAGE and marker.exists():
        try:
            safety.check_config(safety.read(snapshot_path(safety.read(marker)['name']) / 'manifest.json'))
        except (ValueError, OSError) as error:
            print('\x1b[31mSTAGE CONFIGURATION MISMATCH\n' + str(error) + '\x1b[0m', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('snapshot', 'restore', 'export'):
        item = sub.add_parser(command)
        item.add_argument('--name', required=True)
        if command == 'export':
            item.add_argument('--to', required=True)
        if command == 'restore':
            item.add_argument('--port', type=int, default=8000)
    item = sub.add_parser('prepare')
    item.add_argument('--exclude-rule')
    item.add_argument('--port', type=int, default=8000)
    sub.add_parser('import').add_argument('--from', dest='source', required=True)
    args = parser.parse_args(argv)
    try:
        safety.guard()
        if args.command == 'snapshot': snapshot(args.name)
        elif args.command == 'restore': restore(args.name, args.port)
        elif args.command == 'prepare': prepare(args.exclude_rule, args.port)
        elif args.command == 'export': export(args.name, args.to)
        else: import_state(args.source)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
