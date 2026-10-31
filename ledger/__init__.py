"""Transactional SQLite ledger. Each operation joins the caller's transaction."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, Field

from core.config import set_override
from core.models import Atom, Event, Job, RulePack, Session, SessionFlag, Verdict
from core.paths import ledger_db


class SessionView(Session):
    atoms: list[Atom] = Field(default_factory=list)
    verdicts: list[Verdict] = Field(default_factory=list)
    flags: list[SessionFlag] = Field(default_factory=list)


_connection = ContextVar("ledger_connection", default=None)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _data(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _json(value):
    return unicodedata.normalize("NFC", json.dumps(_data(value), ensure_ascii=False))


def _connect():
    conn = sqlite3.connect(ledger_db(), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction():
    """Serialize writers; nested scopes use savepoints and never commit early."""
    existing = _connection.get()
    if existing is not None:
        name = "nested_" + uuid4().hex
        existing.execute(f"SAVEPOINT {name}")
        try:
            yield existing
            existing.execute(f"RELEASE {name}")
        except BaseException:
            existing.execute(f"ROLLBACK TO {name}")
            existing.execute(f"RELEASE {name}")
            raise
        return
    conn = _connect()
    token = _connection.set(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        _connection.reset(token)
        conn.close()


@contextmanager
def _read():
    conn = _connection.get()
    if conn is not None:
        yield conn
    else:
        conn = _connect()
        try:
            yield conn
        finally:
            conn.close()


def init_db(path, exclude_rules=()):
    set_override("LEDGER_DB", str(path))
    ledger_db(create=True)
    with _read() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM rule_packs LIMIT 1").fetchone():
            return
        try:
            from rules.registry import all_rules
        except ModuleNotFoundError as exc:
            if exc.name not in {"rules", "rules.registry"}:
                raise
            active = []
        else:
            active = [r.rule_id for r in all_rules() if r.meta.approved_by
                      and r.meta.status == "active" and r.rule_id not in exclude_rules]
        create_pack_version(active, [], "init")


def current_pack():
    with _read() as conn:
        row = conn.execute("SELECT data FROM rule_packs ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("Ledger has no rule pack; call init_db first")
        return RulePack.model_validate_json(row[0])


def create_pack_version(active, quarantined, change, rule_id=None, approved_by=None):
    with transaction() as conn:
        version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM rule_packs").fetchone()[0]
        pack = RulePack(version=version, active=active, quarantined=quarantined,
                        change=change, rule_id=rule_id, approved_by=approved_by, ts=_now())
        conn.execute("INSERT INTO rule_packs VALUES (?,?)", (version, _json(pack)))
        return pack


def add_session(session):
    session = Session.model_validate(_data(session))
    with transaction() as conn:
        found = conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session.session_id,)).fetchone()
        if not found:
            conn.execute("INSERT INTO sessions VALUES (?,?)", (session.session_id, _json(session)))
            for turn in session.turns:
                conn.execute("INSERT INTO turns VALUES (?,?,?)", (session.session_id, turn.turn_id, _json(turn)))
                index_turn(session.session_id, turn)
        return get_session(session.session_id)


def list_sessions():
    with _read() as conn:
        ids = [r[0] for r in conn.execute("SELECT session_id FROM sessions ORDER BY session_id")]
        return [get_session(sid) for sid in ids]


def get_session(session_id):
    with _read() as conn:
        row = conn.execute("SELECT data FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        data["turns"] = [json.loads(r[0]) for r in conn.execute(
            "SELECT data FROM turns WHERE session_id=? ORDER BY rowid", (session_id,))]
        data["atoms"] = [json.loads(r[0]) for r in conn.execute(
            "SELECT data FROM atoms WHERE session_id=? ORDER BY rowid", (session_id,))]
        data["flags"] = [json.loads(r[0]) for r in conn.execute(
            "SELECT data FROM session_flags WHERE session_id=? ORDER BY rowid", (session_id,))]
        data["verdicts"] = effective_verdicts(session_id)
        return SessionView.model_validate(data)


def add_atoms(session_id, atoms):
    with transaction() as conn:
        for value in atoms:
            atom = Atom.model_validate(_data(value))
            turn = conn.execute("SELECT data FROM turns WHERE session_id=? AND turn_id=?",
                                (session_id, atom.turn_id)).fetchone()
            if turn is None or json.loads(turn[0])["text"][atom.start:atom.end] != atom.span:
                raise ValueError("Atom offsets must identify its exact source span")
            key = f"{session_id}:{atom.atom_id}"
            conn.execute("INSERT INTO atoms VALUES (?,?,?,?)", (key, session_id, atom.turn_id, _json(atom)))
            index_atom(session_id, atom)


def replace_atoms(session_id, atoms):
    if _connection.get() is None:
        raise RuntimeError("replace_atoms requires transaction()")
    conn = _connection.get()
    conn.execute("DELETE FROM search_fts WHERE session_id=? AND kind='atom'", (session_id,))
    conn.execute("DELETE FROM atoms WHERE session_id=?", (session_id,))
    add_atoms(session_id, atoms)


def append_verdicts(verdicts):
    ids = []
    with transaction() as conn:
        for value in verdicts:
            verdict = Verdict.model_validate(_data(value))
            if verdict.supersedes is None:
                previous = conn.execute("SELECT verdict_id FROM verdicts WHERE atom_key=? ORDER BY verdict_id DESC LIMIT 1",
                                        (verdict.atom_key,)).fetchone()
                verdict = verdict.model_copy(update={"supersedes": previous[0] if previous else None})
            cursor = conn.execute("INSERT INTO verdicts(atom_key,rule_pack_version,supersedes,data) VALUES (?,?,?,?)",
                                  (verdict.atom_key, verdict.rule_pack_version, verdict.supersedes, _json(verdict)))
            ids.append(cursor.lastrowid)
    return ids


def latest_verdicts(session_id, pack_version=None):
    with _read() as conn:
        sql = """SELECT v.verdict_id,v.data FROM verdicts v JOIN atoms a ON a.atom_key=v.atom_key
                 WHERE a.session_id=?"""
        args = [session_id]
        if pack_version is not None:
            sql += " AND v.rule_pack_version=?"
            args.append(pack_version)
        sql += " AND v.verdict_id=(SELECT MAX(w.verdict_id) FROM verdicts w WHERE w.atom_key=v.atom_key AND w.rule_pack_version=v.rule_pack_version) ORDER BY v.verdict_id"
        return [Verdict.model_validate({**json.loads(r["data"]), "verdict_id": r["verdict_id"]})
                for r in conn.execute(sql, args)]


def effective_verdicts(session_id, pack_version=None):
    version = current_pack().version if pack_version is None else pack_version
    with _read() as conn:
        rows = conn.execute("""SELECT v.verdict_id,v.data FROM verdicts v
            JOIN atoms a ON a.atom_key=v.atom_key WHERE a.session_id=? AND v.verdict_id=(
            SELECT MAX(w.verdict_id) FROM verdicts w WHERE w.atom_key=v.atom_key
            AND w.rule_pack_version<=?) ORDER BY v.verdict_id""", (session_id, version))
        return [Verdict.model_validate({**json.loads(r["data"]), "verdict_id": r["verdict_id"]}) for r in rows]


def append_event(type, payload):
    with transaction() as conn:
        return conn.execute("INSERT INTO events(ts,type,payload) VALUES (?,?,?)",
                            (_now(), type, _json(payload))).lastrowid


def events_since(id, limit=500):
    with _read() as conn:
        return [Event(id=r["id"], ts=r["ts"], type=r["type"], payload=json.loads(r["payload"]))
                for r in conn.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (id, limit))]


def _index(kind, session_id, turn_id, atom_key, text):
    from textnorm import fold

    text = unicodedata.normalize("NFC", text)
    with transaction() as conn:
        conn.execute("DELETE FROM search_fts WHERE kind=? AND session_id=? AND turn_id=? AND atom_key IS ?",
                     (kind, session_id, turn_id, atom_key))
        conn.execute("INSERT INTO search_fts(x,xf,kind,session_id,turn_id,atom_key) VALUES (?,?,?,?,?,?)",
                     (text.lower(), fold(text), kind, session_id, turn_id, atom_key))


def index_turn(session_id, turn):
    data = _data(turn)
    _index("turn", session_id, data["turn_id"], None, data["text"])


def index_atom(session_id, atom):
    data = _data(atom)
    _index("atom", session_id, data["turn_id"], f'{session_id}:{data["atom_id"]}', data["span"])


def search(q, session_id=None):
    from textnorm import fold

    query = unicodedata.normalize("NFC", q).strip()
    if not query or query == "*":
        return []
    prefix = query.endswith("*")
    query = query[:-1].rstrip() if prefix else query
    results, seen = [], set()
    with _read() as conn:
        for column, value in (("x", query.lower()), ("xf", fold(query))):
            phrase = '"' + value.replace('"', '""') + '"' + ("*" if prefix else "")
            sql = "SELECT rowid,kind,session_id,turn_id,atom_key,-bm25(search_fts) AS score FROM search_fts WHERE search_fts MATCH ?"
            args = [f"{column} : {phrase}"]
            if session_id is not None:
                sql += " AND session_id=?"
                args.append(session_id)
            sql += " ORDER BY score DESC,rowid ASC"
            for row in conn.execute(sql, args):
                if row["rowid"] in seen:
                    continue
                seen.add(row["rowid"])
                if row["kind"] == "turn":
                    original = conn.execute("SELECT data FROM turns WHERE session_id=? AND turn_id=?",
                                            (row["session_id"], row["turn_id"])).fetchone()
                    snippet = json.loads(original[0])["text"]
                else:
                    original = conn.execute("SELECT data FROM atoms WHERE atom_key=?", (row["atom_key"],)).fetchone()
                    snippet = json.loads(original[0])["span"]
                results.append({k: row[k] for k in ("kind", "session_id", "turn_id", "atom_key", "score")}
                               | {"snippet": snippet, "column": column})
    return results


def record_llm_call(call):
    data = _data(call)
    with transaction() as conn:
        return conn.execute("INSERT INTO llm_calls(request_sha256,data) VALUES (?,?)",
                            (data["request_sha256"], _json(data))).lastrowid


def create_job(job):
    job = Job.model_validate(_data(job))
    with transaction() as conn:
        conn.execute("INSERT INTO jobs VALUES (?,?)", (job.job_id, _json(job)))
        _write_attempts(conn, job)
    return job.job_id


def _write_attempts(conn, job):
    conn.execute("DELETE FROM job_attempts WHERE job_id=?", (job.job_id,))
    for attempt in job.attempts:
        conn.execute("INSERT INTO job_attempts VALUES (?,?,?)", (job.job_id, attempt.attempt_no, _json(attempt)))


def update_job(job_id, **changes):
    with transaction() as conn:
        row = conn.execute("SELECT data FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if "job_id" in changes:
            raise ValueError("job_id cannot change")
        job = Job.model_validate(json.loads(row[0]) | changes)
        conn.execute("UPDATE jobs SET data=? WHERE job_id=?", (_json(job), job_id))
        _write_attempts(conn, job)
        return job


def add_session_flags(session_id, flags):
    with transaction() as conn:
        for value in flags:
            flag = SessionFlag.model_validate(_data(value))
            conn.execute("INSERT OR REPLACE INTO session_flags VALUES (?,?,?)",
                         (session_id, flag.flag_code, _json(flag)))


def add_pending(pending):
    data = dict(_data(pending))
    data.setdefault("pending_id", uuid4().hex)
    data.setdefault("ts", _now())
    with transaction() as conn:
        conn.execute("INSERT INTO pending_dossier VALUES (?,?)", (data["pending_id"], _json(data)))
    return data["pending_id"]
