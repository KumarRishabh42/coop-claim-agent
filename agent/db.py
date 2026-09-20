"""SQLite storage. Plain stdlib sqlite3, JSON blobs in text columns where
that's simpler than a join. See SPEC.md section 6.7."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from agent.config import get_config, repo_path, writable_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    year INTEGER NOT NULL,
    guide_path TEXT NOT NULL,
    rate REAL NOT NULL,
    accrual_rate REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id TEXT NOT NULL,
    program_id TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    applies_to_json TEXT NOT NULL,
    check_json TEXT NOT NULL,
    source_section TEXT NOT NULL,
    source_quote TEXT NOT NULL,
    on_fail TEXT,
    quote_verified INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (id, program_id)
);

CREATE TABLE IF NOT EXISTS dealers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    packet_id TEXT NOT NULL,
    dealer_id TEXT NOT NULL,
    program_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    medium TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    reimbursement_json TEXT,
    payable_if_resolved REAL,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL,
    facts_json TEXT,
    documents_present_json TEXT,
    manifest_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT,
    present INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    kind TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    source_section TEXT,
    message TEXT NOT NULL,
    fix TEXT,
    ask TEXT
);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    text TEXT NOT NULL,
    answers_json TEXT NOT NULL DEFAULT '[]',
    resolved INTEGER NOT NULL DEFAULT 0,
    answer TEXT
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dealer_id TEXT NOT NULL,
    program_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount REAL NOT NULL,
    claim_id TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    claim_id TEXT,
    step TEXT NOT NULL,
    actor TEXT NOT NULL,
    input_ref TEXT,
    output_ref TEXT,
    model TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    note TEXT
);
"""


def db_path() -> Path:
    url = os.environ.get("DATABASE_URL") or get_config()["paths"]["database_url"]
    # DECISION: only sqlite:/// URLs are supported, per SPEC.md section 10.
    assert url.startswith("sqlite:///"), f"unsupported DATABASE_URL: {url}"
    rel = url.removeprefix("sqlite:///")
    return writable_path(rel)


def get_conn(path: Path | None = None) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI's async routes (needed for UploadFile)
    # run on the event loop thread while a sync Depends() dependency's
    # connection is created via a threadpool — one request, one connection,
    # never touched concurrently, so this is safe.
    conn = sqlite3.connect(path or db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def reset_db(path: Path | None = None) -> sqlite3.Connection:
    """Drops and recreates every table. Used by seed_db.py and tests."""
    p = path or db_path()
    if p.exists():
        p.unlink()
    conn = get_conn(p)
    init_db(conn)
    return conn


def dumps(obj) -> str:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    return json.dumps(obj, default=str)
