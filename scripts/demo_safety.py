"""Checkout-scoped process, path and configuration checks for rehearsals."""
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

from core import config, paths

COMPARE = ('backend', 'offline', 'judge', 's1', 'judge_model', 'draft', 'public', 'video')


def guard():
    if config.settings.PUBLIC_MODE:
        raise ValueError('Demo state commands are unavailable in public mode')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def files(root):
    if not root.exists():
        return {}
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Snapshot trees must not contain links')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path
    return result


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError('Invalid snapshot member')
    return path


def differences(saved, current):
    return [key for key in COMPARE if saved[key] != current[key]]


def check_config(manifest):
    current = config.config_fields()
    changed = differences(manifest['config_fields'], current)
    if changed:
        raise ValueError('Configuration mismatch: ' + ', '.join(changed) + '\n' +
            manifest['config_line'] + '\n' + ' '.join(f'{k}={v}' for k, v in current.items()))


def port_free(port):
    with socket.socket() as sock:
        if os.name == 'nt':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(('127.0.0.1', port))
        except OSError:
            raise ValueError('Stop the app first: port is open') from None


def alive(pid):
    if pid <= 0:
        raise ValueError('Invalid job PID')
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 5:
                raise ValueError('Cannot inspect recorded job PID')
            return False
        try:
            status = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)):
                raise OSError('Cannot inspect recorded job PID')
            return status.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True, check=False).stdout.strip()
        return bool(state) and not state.startswith('Z')
    except ProcessLookupError:
        return False


def stop_command(pid):
    if os.name == 'nt':
        return f'taskkill /PID {pid} /T /F'
    if os.getpgid(pid) == pid:
        return f'kill -TERM -- -{pid}'
    return 'kill -TERM ' + ' '.join(str(child) for child in descendants(pid) + [pid])


def descendants(pid):
    # Query only children of a recorded PID, never process names or other jobs.
    result = subprocess.run(['pgrep', '-P', str(pid)], capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise ValueError('Cannot inspect recorded process tree')
    children = []
    for value in result.stdout.split():
        child = int(value)
        children.extend(descendants(child)); children.append(child)
    return children


def jobs():
    result = []
    for file in paths.jobs_dir().glob('*.pid'):
        if file.is_symlink():
            raise ValueError('Job PID file must not be a link')
        pid = int(file.read_text(encoding='utf-8').strip())
        if alive(pid):
            result.append((file, pid))
        else:
            file.unlink()
    return result


def stop_jobs():
    import ledger
    for file, pid in jobs():
        targets = [pid]
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], check=True, capture_output=True)
        else:
            targets = descendants(pid) + [pid]
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGTERM)
            else:
                for target in targets:
                    try: os.kill(target, signal.SIGTERM)
                    except ProcessLookupError: pass
        deadline = time.monotonic() + 5
        while any(alive(target) for target in targets) and time.monotonic() < deadline:
            time.sleep(.05)
        if os.name != 'nt':
            for target in targets:
                if alive(target):
                    try: os.kill(target, signal.SIGKILL)
                    except ProcessLookupError: pass
        if alive(pid):
            raise ValueError('Job did not stop: ' + stop_command(pid))
        job = ledger.get_job(file.stem) if paths.ledger_db().exists() else None
        if job is not None:
            attempts = [row.model_dump(mode='json') for row in job.attempts]
            if attempts:
                attempts[-1]['error_lines'].append('cancelled by prepare')
            else:
                attempts.append(dict(attempt_no=1, started=datetime.now(timezone.utc).isoformat(),
                    error_lines=['cancelled by prepare']))
            ledger.update_job(job.job_id, status='error', pid=None, attempts=attempts)
        file.unlink()


def git(*args, check=True):
    return subprocess.run(['git', '-C', str(paths.REPO_ROOT), *args], check=check,
        capture_output=True, text=True, encoding='utf-8')


def git_state():
    if not (paths.REPO_ROOT / '.git').exists():
        print('note: copy without .git; skipping git state and cleanup')
        return {'head': None, 'worktrees': [], 'branches': []}
    worktrees = []
    for block in git('worktree', 'list', '--porcelain', '-z').stdout.strip('\0').split('\0\0'):
        row = dict(line.split(' ', 1) for line in block.split('\0') if ' ' in line)
        worktrees.append(row)
    return {'head': git('rev-parse', 'HEAD').stdout.strip(), 'worktrees': worktrees,
        'branches': git('for-each-ref', '--format=%(refname:short)', 'refs/heads/rule/').stdout.splitlines()}
