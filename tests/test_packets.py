"""Runs the full pipeline on each of the six packets and compares to
expected.json. Source of truth for correct behavior, per SPEC.md 13.
Agent code never reads expected.json — only this test does."""
import json

import pytest

from agent import ledger, pipeline
from agent.config import get_config, repo_path
from agent.db import reset_db
from agent.seed import run_all_packets, seed_program_and_rules


@pytest.fixture
def conn(tmp_path):
    c = reset_db(tmp_path / "test.db")
    seed_program_and_rules(c)
    yield c
    c.close()


def load_expected(packet_id: str) -> dict:
    path = repo_path(get_config()["paths"]["packets_dir"], packet_id, "expected.json")
    return json.loads(path.read_text())


def test_all_packets_match_expected(conn):
    decisions = run_all_packets(conn)
    assert set(decisions) == {"A", "B", "C", "D", "E", "F"}

    for packet_id, decision in decisions.items():
        expected = load_expected(packet_id)
        assert decision.decision == expected["decision"], f"packet {packet_id}: decision"

        for rule_id, verdict in expected.get("verdicts", {}).items():
            row = conn.execute("SELECT verdict FROM checks WHERE claim_id=? AND rule_id=?", (packet_id, rule_id)).fetchone()
            assert row is not None, f"packet {packet_id}: no check for {rule_id}"
            assert row["verdict"] == verdict, f"packet {packet_id}: {rule_id} verdict"

        if expected.get("payable") is not None:
            assert decision.reimbursement is not None, f"packet {packet_id}: expected a reimbursement breakdown"
            assert abs(decision.reimbursement.payable - expected["payable"]) < 0.01, f"packet {packet_id}: payable"

        if expected.get("payable_if_resolved") is not None:
            assert decision.payable_if_resolved is not None, f"packet {packet_id}: expected payable_if_resolved"
            assert abs(decision.payable_if_resolved - expected["payable_if_resolved"]) < 0.01, f"packet {packet_id}: payable_if_resolved"


def test_packet_B_fix_names_the_required_width(conn):
    run_all_packets(conn)
    row = conn.execute("SELECT fix FROM checks WHERE claim_id='B' AND rule_id='R3'").fetchone()
    expected = load_expected("B")
    assert expected["fix_contains"] in row["fix"]


def test_resolve_D_upload_payment_unblocks(conn):
    run_all_packets(conn)
    expected = load_expected("D")
    decision = pipeline.resolve_claim(conn, "D", "upload_document", doc_kind="payment")
    assert decision.decision == "auto_file"
    assert abs(decision.reimbursement.payable - expected["payable_if_resolved"]) < 0.01


def test_resolve_E_to_invoice_moves_to_approval_queue(conn):
    run_all_packets(conn)
    expected = load_expected("E")
    decision = pipeline.resolve_claim(conn, "E", "resolve_amount", source="invoice")
    assert decision.decision == expected["after_resolve_decision"]
    assert abs(decision.reimbursement.payable - expected["after_resolve_payable"]) < 0.01


def test_resolve_F_confirm_approval_auto_files(conn):
    run_all_packets(conn)
    expected = load_expected("F")
    decision = pipeline.resolve_claim(conn, "F", "confirm_offer_approved")
    assert decision.decision == expected["after_resolve_decision"]
    assert abs(decision.reimbursement.payable - expected["after_resolve_payable"]) < 0.01


def test_auto_filed_claim_reduces_ledger_balance(conn):
    balance_before = ledger.balance(conn, "summit", "northwind-2026")
    assert balance_before == 8050.0
    run_all_packets(conn)
    # Only packet A auto-files on the first pass (it needs no human input).
    balance_after = ledger.balance(conn, "summit", "northwind-2026")
    assert abs(balance_after - (8050.0 - 380.0)) < 0.01


def test_approving_a_needs_approval_claim_files_it_and_moves_the_ledger(conn):
    run_all_packets(conn)
    pipeline.resolve_claim(conn, "E", "resolve_amount", source="invoice")  # -> needs_approval, payable 825
    balance_before = ledger.balance(conn, "summit", "northwind-2026")
    pipeline.approve_claim(conn, "E", actor="human:office_manager")
    balance_after = ledger.balance(conn, "summit", "northwind-2026")
    assert abs((balance_before - balance_after) - 825.0) < 0.01
    status = conn.execute("SELECT status FROM claims WHERE id='E'").fetchone()["status"]
    assert status == "filed"


def test_quote_citations_are_verified_for_every_rule(conn):
    rows = conn.execute("SELECT id, quote_verified FROM rules").fetchall()
    assert len(rows) == 9
    assert all(r["quote_verified"] for r in rows), "a rule's cited quote does not appear in the guide text"


def test_audit_log_has_an_event_per_model_call_and_decision(conn):
    run_all_packets(conn)
    steps = {r["step"] for r in conn.execute("SELECT DISTINCT step FROM audit_events")}
    assert {"extract_rules", "precheck", "documents", "decide", "file"} <= steps
