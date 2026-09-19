"""Funds ledger per dealer and program. Balance = accrued minus reimbursed
minus approved-but-unpaid (glossary, SPEC.md section 3)."""
from __future__ import annotations

import sqlite3

from agent.audit import now_iso


def balance(conn: sqlite3.Connection, dealer_id: str, program_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS b FROM ledger_entries WHERE dealer_id=? AND program_id=?",
        (dealer_id, program_id),
    ).fetchone()
    return round(row["b"], 2)


def accrued_total(conn: sqlite3.Connection, dealer_id: str, program_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS a FROM ledger_entries WHERE dealer_id=? AND program_id=? AND kind='accrual'",
        (dealer_id, program_id),
    ).fetchone()
    return round(row["a"], 2)


def reimbursed_total(conn: sqlite3.Connection, dealer_id: str, program_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(-SUM(amount), 0) AS r FROM ledger_entries "
        "WHERE dealer_id=? AND program_id=? AND kind IN ('reimbursed','pending')",
        (dealer_id, program_id),
    ).fetchone()
    return round(row["r"], 2)


def pending_total(conn: sqlite3.Connection, dealer_id: str, program_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(-SUM(amount), 0) AS p FROM ledger_entries "
        "WHERE dealer_id=? AND program_id=? AND kind='pending'",
        (dealer_id, program_id),
    ).fetchone()
    return round(row["p"], 2)


def add_entry(
    conn: sqlite3.Connection, dealer_id: str, program_id: str, kind: str, amount: float,
    claim_id: str | None = None, note: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO ledger_entries (dealer_id, program_id, ts, kind, amount, claim_id, note) VALUES (?,?,?,?,?,?,?)",
        (dealer_id, program_id, now_iso(), kind, amount, claim_id, note),
    )
    conn.commit()


def entries(conn: sqlite3.Connection, dealer_id: str, program_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ledger_entries WHERE dealer_id=? AND program_id=? ORDER BY id",
        (dealer_id, program_id),
    ).fetchall()
