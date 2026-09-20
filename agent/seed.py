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
from agent.models import PacketManifest, Rule


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


# DECISION: five rules pulled from a real, live extraction run against
# BMW Motorrad's public 2026 co-op guidelines PDF (bmwmotorraddealerprograms.com),
# hardcoded directly rather than re-extracted or stored via a guide-text
# replay fixture — this repo doesn't keep a copy of BMW's document (see
# demo_assets/README.md), and quote verification needs that source text.
# Each quote below was verified against the real PDF at extraction time.
BMW_PROGRAM_ID = "bmw-motorrad-2026"
BMW_RULES = [
    Rule(id="R4", program_id=BMW_PROGRAM_ID, title="Claim submission deadline", kind="measured",
         applies_to=[], check={"type": "date_window", "activity_start": "2026-01-01", "activity_end": "2026-12-31",
                                "claim_within_days": 30, "claim_by": None},
         source={"section": "2", "quote": "All claims must be submitted within 30 days after the activity end date with all required documentation."},
         on_fail="Submit claim within 30 days of activity end date.", quote_verified=True),
    Rule(id="R11", program_id=BMW_PROGRAM_ID, title="Broadcast must include brand tagline", kind="judgment",
         applies_to=["radio"], check={"type": "required_text", "text": "Make Life a Ride"},
         source={"section": "4", "quote": 'Broadcast (video, radio) must mention either BMW Motorrad or BMW Motorcycles and "Make Life a Ride".'},
         on_fail='Include "Make Life a Ride" in broadcast spot.', quote_verified=True),
    Rule(id="R16", program_id=BMW_PROGRAM_ID, title="Copyright notice required in advertising", kind="judgment",
         applies_to=[], check={"type": "required_text", "text": "©2026 BMW of North America, LLC"},
         source={"section": "5", "quote": "BMW Motorrad Copyright information is required to be included in all advertising with the exception of mobile and digital banners."},
         on_fail="Include copyright notice ©2026 BMW of North America, LLC in advertising.", quote_verified=True),
    Rule(id="R17", program_id=BMW_PROGRAM_ID, title="Multi-brand ads prorated by brand coverage", kind="measured",
         applies_to=[], check={"type": "amounts_match", "tolerance_usd": 0},
         source={"section": "2", "quote": "For example, if 4 brands are displayed in an ad, only 25% of the cost will be eligible for co-op support."},
         on_fail=None, quote_verified=True),
    Rule(id="R20", program_id=BMW_PROGRAM_ID, title="Event pre-approval required one month prior", kind="measured",
         applies_to=[], check={"type": "date_window", "activity_start": "2026-01-01", "activity_end": "2026-12-31",
                                "claim_within_days": None, "claim_by": None},
         source={"section": "2", "quote": "You must submit your event for pre-approval within the co-op portal at least one month prior to your event."},
         on_fail="Submit event pre-approval at least one month before event date.", quote_verified=True),
]


def seed_bmw_program(conn: sqlite3.Connection) -> None:
    """A second, real-world program alongside the synthetic Northwind one —
    demonstrates rule extraction against an actual manufacturer guide. See
    demo_assets/README.md for the source PDF link."""
    dealer_id = get_config()["program"]["dealer_id"]

    conn.execute(
        "INSERT INTO programs (id, name, year, guide_path, rate, accrual_rate) VALUES (?,?,?,?,?,?)",
        (BMW_PROGRAM_ID, "BMW Motorrad", 2026, "demo_assets/README.md", 0.5, 0.02),
    )
    for r in BMW_RULES:
        conn.execute(
            "INSERT INTO rules (id, program_id, title, kind, applies_to_json, check_json, source_section, "
            "source_quote, on_fail, quote_verified) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.id, r.program_id, r.title, r.kind, dumps(r.applies_to), dumps(r.check), r.source.section,
             r.source.quote, r.on_fail, int(r.quote_verified)),
        )
    conn.commit()
    audit.log(conn, "extract_rules", "agent", input_ref="BMW Motorrad 2026 co-op guidelines (real PDF, see demo_assets/README.md)",
              note=f"{len(BMW_RULES)} rules hardcoded from a real extraction run, all quotes verified")

    ledger.add_entry(conn, dealer_id, BMW_PROGRAM_ID, "accrual", 50000.0, note="Demo opening balance")

    packets_dir = repo_path(get_config()["paths"]["packets_dir"])
    manifest = PacketManifest.model_validate(json.loads((packets_dir / "G" / "manifest.json").read_text()))
    pipeline.run_packet(conn, manifest, packets_dir / "G", brand_name="BMW Motorrad")

    # Default to BMW as the active program: new /claims/upload and
    # /program/upload demos check against whatever's active, and BMW is the
    # one worth showing off (real guide, hardcoded deterministic result).
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_program_id', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (BMW_PROGRAM_ID,))
    conn.commit()


# The six packets rendered by scripts/make_packets.py, per SPEC.md 7.3.
# Fixed rather than scanning data/packets/, which also holds packets created
# live through the /claims/upload demo flow (see app.py) — those have no
# replay fixture and aren't part of this canned run.
CANONICAL_PACKET_IDS = ["A", "B", "C", "D", "E", "F"]


def run_all_packets(conn: sqlite3.Connection) -> dict:
    packets_dir = repo_path(get_config()["paths"]["packets_dir"])
    decisions = {}
    for packet_id in CANONICAL_PACKET_IDS:
        manifest_path = packets_dir / packet_id / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = PacketManifest.model_validate(json.loads(manifest_path.read_text()))
        decisions[manifest.packet_id] = pipeline.run_packet(conn, manifest, packets_dir / packet_id)
    return decisions
