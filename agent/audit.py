"""Writes audit events. Every model call, decision, human action and filing
writes one row here. See SPEC.md 6.6."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from agent.models import AuditEvent


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(
    conn: sqlite3.Connection,
    step: str,
    actor: str,
    claim_id: str | None = None,
    input_ref: str | None = None,
    output_ref: str | None = None,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    note: str | None = None,
) -> AuditEvent:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO audit_events (ts, claim_id, step, actor, input_ref, output_ref, model, "
        "tokens_in, tokens_out, cost_usd, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ts, claim_id, step, actor, input_ref, output_ref, model, tokens_in, tokens_out, cost_usd, note),
    )
    conn.commit()
    return AuditEvent(
        id=cur.lastrowid, ts=ts, claim_id=claim_id, step=step, actor=actor,
        input_ref=input_ref, output_ref=output_ref, model=model,
        tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd, note=note,
    )


def list_events(conn: sqlite3.Connection, claim_id: str | None = None) -> list[AuditEvent]:
    if claim_id:
        rows = conn.execute("SELECT * FROM audit_events WHERE claim_id = ? ORDER BY id", (claim_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
    return [AuditEvent(**dict(r)) for r in rows]


def total_cost(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM audit_events").fetchone()
    return round(row["c"], 6)
