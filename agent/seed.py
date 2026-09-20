"""Seeds a fresh database: program, rules (via extract_rules), dealer,
opening ledger, and optionally runs the pipeline over every rendered packet.
Used by scripts/seed_db.py and by tests/test_packets.py."""
from __future__ import annotations

import json
import sqlite3

from agent import audit, ledger, pipeline
from agent.config import get_config, repo_path
from agent.db import dumps
from agent.extract_rules import extract_rules
from agent.models import PacketManifest


def seed_program_and_rules(conn: sqlite3.Connection) -> None:
    cfg = get_config()
    program_id = cfg["program"]["id"]
    dealer_id = cfg["program"]["dealer_id"]

    conn.execute("INSERT INTO dealers (id, name) VALUES (?, ?)", (dealer_id, "Summit Heating and Air"))

    guide_path = repo_path(cfg["paths"]["guides_dir"], "northwind-2026.md")
    guide_text = guide_path.read_text()
    rules, usage = extract_rules(program_id, guide_text)

    r1 = next(r for r in rules if r.check.type == "funds_terms")
    conn.execute(
        "INSERT INTO programs (id, name, year, guide_path, rate, accrual_rate) VALUES (?,?,?,?,?,?)",
        (program_id, "Northwind Comfort", 2026, str(guide_path), r1.check.rate, r1.check.accrual_rate),
    )

    for r in rules:
        conn.execute(
            "INSERT INTO rules (id, program_id, title, kind, applies_to_json, check_json, source_section, "
            "source_quote, on_fail, quote_verified) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.id, r.program_id, r.title, r.kind, dumps(r.applies_to), dumps(r.check), r.source.section,
             r.source.quote, r.on_fail, int(r.quote_verified)),
        )
    conn.commit()

    unverified = sum(1 for r in rules if not r.quote_verified)
    audit.log(conn, "extract_rules", "agent", input_ref="data/guides/northwind-2026.md",
              model=usage.model, tokens_in=usage.tokens_in, tokens_out=usage.tokens_out, cost_usd=usage.cost_usd,
              note=f"{len(rules)} rules extracted, {unverified} quote(s) unverified")

    # Opening ledger state, SPEC.md 7.2: purchases $610,000, accrued $12,200,
    # reimbursed $4,150 to date -> balance $8,050.
    ledger.add_entry(conn, dealer_id, program_id, "accrual", 12200.0, note="Opening accrual, 2026 purchases to date $610,000")
    ledger.add_entry(conn, dealer_id, program_id, "reimbursed", -4150.0, note="Opening reimbursed-to-date balance")


def run_all_packets(conn: sqlite3.Connection) -> dict:
    packets_dir = repo_path(get_config()["paths"]["packets_dir"])
    decisions = {}
    for packet_dir in sorted(packets_dir.iterdir()):
        manifest_path = packet_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = PacketManifest.model_validate(json.loads(manifest_path.read_text()))
        decisions[manifest.packet_id] = pipeline.run_packet(conn, manifest, packet_dir)
    return decisions
