"""End-to-end pipeline: run a packet through precheck/document extraction,
rules, reimbursement and policy, and persist the result. Also handles
re-running after a human answers a question or approves/rejects a claim.
See SPEC.md 8.6, 8.7 and the operating procedure in 14.3."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent import audit, ledger
from agent.config import get_config, repo_path
from agent.db import dumps
from agent.documents import extract_documents
from agent.models import ClaimDecision, ObservedFacts, PacketManifest, PaymentFacts, Rule
from agent.policy import decide
from agent.precheck import precheck_ad
from agent.reimbursement import compute_reimbursement
from agent.rules_engine import evaluate_all, has_non_fixable_fail


def load_rules(conn: sqlite3.Connection, program_id: str) -> list[Rule]:
    rows = conn.execute("SELECT * FROM rules WHERE program_id = ? ORDER BY id", (program_id,)).fetchall()
    rules = []
    for r in rows:
        rules.append(Rule(
            id=r["id"], program_id=r["program_id"], title=r["title"], kind=r["kind"],
            applies_to=json.loads(r["applies_to_json"]), check=json.loads(r["check_json"]),
            source={"section": r["source_section"], "quote": r["source_quote"]},
            on_fail=r["on_fail"], quote_verified=bool(r["quote_verified"]),
        ))
    return rules


def documents_present_map(manifest: PacketManifest, packet_dir: Path) -> dict[str, bool]:
    files = manifest.files.model_dump()
    present: dict[str, bool] = {"ad": bool(files.get("ad") or files.get("script"))}
    for key in ("invoice", "payment", "claim_form", "affidavit"):
        fname = files.get(key)
        present[key] = bool(fname) and (packet_dir / fname).exists()
    approval = files.get("approval")
    present["territory_manager_approval"] = bool(approval) and (packet_dir / approval).exists()
    return present


def run_packet(conn: sqlite3.Connection, manifest: PacketManifest, packet_dir: Path, threshold: float | None = None) -> ClaimDecision:
    cfg = get_config()
    threshold = threshold if threshold is not None else cfg["policy"]["auto_file_threshold_usd"]
    tol = cfg["policy"]["amounts_match_tolerance_usd"]

    claim_id = manifest.packet_id
    rules = load_rules(conn, manifest.program_id)
    documents_present = documents_present_map(manifest, packet_dir)

    # --- model perceives ---
    ad_facts = None
    cost_usd = 0.0
    if manifest.files.ad:
        image_bytes = (packet_dir / manifest.files.ad).read_bytes()
        ad_facts, usage1 = precheck_ad(manifest.packet_id, image_bytes=image_bytes)
        cost_usd += usage1.cost_usd
        audit.log(conn, "precheck", "agent", claim_id=claim_id, input_ref=f"packets/{manifest.packet_id}/{manifest.files.ad}",
                   model=usage1.model, tokens_in=usage1.tokens_in, tokens_out=usage1.tokens_out, cost_usd=usage1.cost_usd,
                   note="ad facts extracted")
    elif manifest.files.script:
        script_text = (packet_dir / manifest.files.script).read_text()
        ad_facts, usage1 = precheck_ad(manifest.packet_id, script_text=script_text)
        cost_usd += usage1.cost_usd
        audit.log(conn, "precheck", "agent", claim_id=claim_id, input_ref=f"packets/{manifest.packet_id}/{manifest.files.script}",
                   model=usage1.model, tokens_in=usage1.tokens_in, tokens_out=usage1.tokens_out, cost_usd=usage1.cost_usd,
                   note="script facts extracted")

    doc_facts = None
    if manifest.mode == "claim":
        present_names = [k for k in ("invoice", "payment", "claim_form", "affidavit") if documents_present.get(k)]
        blob = ("Documents present: " + ", ".join(present_names)) if present_names else "No claim documents present."
        doc_facts, usage2 = extract_documents(manifest.packet_id, blob)
        cost_usd += usage2.cost_usd
        audit.log(conn, "documents", "agent", claim_id=claim_id, input_ref=f"packets/{manifest.packet_id}/",
                   model=usage2.model, tokens_in=usage2.tokens_in, tokens_out=usage2.tokens_out, cost_usd=usage2.cost_usd,
                   note=f"{len(present_names)} document(s) extracted")

    facts = ObservedFacts(
        ad=ad_facts,
        invoice=doc_facts.invoice if doc_facts else None,
        payment=doc_facts.payment if doc_facts else None,
        claim_form=doc_facts.claim_form if doc_facts else None,
        affidavit=doc_facts.affidavit if doc_facts else None,
    )

    return finish_claim(conn, claim_id, manifest, rules, facts, documents_present, threshold, tol, cost_usd, packet_dir)


def finish_claim(
    conn: sqlite3.Connection, claim_id: str, manifest: PacketManifest, rules: list[Rule], facts: ObservedFacts,
    documents_present: dict[str, bool], threshold: float, tol: float, cost_usd: float, packet_dir: Path,
) -> ClaimDecision:
    """Rules -> reimbursement -> policy. Called on first run and again after a
    human answers a question, without re-calling the model."""
    checks = evaluate_all(rules, facts, manifest, documents_present)

    reimb = None
    if manifest.mode == "claim":
        r1 = next(r for r in rules if r.check.type == "funds_terms")
        eligible_at_all = not has_non_fixable_fail(checks)
        balance_before = ledger.balance(conn, manifest.dealer_id, manifest.program_id)
        reimb = compute_reimbursement(facts, r1, balance_before, tol, eligible_at_all)

    decision = decide(claim_id, manifest.packet_id, manifest.mode, checks, reimb, threshold, cost_usd=cost_usd)

    _persist(conn, manifest, facts, documents_present, checks, decision, packet_dir)
    audit.log(conn, "decide", "agent", claim_id=claim_id, note=f"decision={decision.decision}")

    if decision.decision == "auto_file":
        _file_claim(conn, manifest, decision)

    return decision


def _persist(conn, manifest: PacketManifest, facts: ObservedFacts, documents_present: dict, checks, decision: ClaimDecision, packet_dir: Path):
    ts = audit.now_iso()
    existing = conn.execute("SELECT created_ts, cost_usd FROM claims WHERE id = ?", (decision.claim_id,)).fetchone()
    created_ts = existing["created_ts"] if existing else ts
    prior_cost = existing["cost_usd"] if existing else 0.0
    status = "filed" if decision.decision == "auto_file" else decision.decision

    conn.execute(
        "INSERT INTO claims (id, packet_id, dealer_id, program_id, mode, medium, status, decision, reasons_json, "
        "reimbursement_json, payable_if_resolved, cost_usd, created_ts, updated_ts, facts_json, "
        "documents_present_json, manifest_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET status=excluded.status, decision=excluded.decision, "
        "reasons_json=excluded.reasons_json, reimbursement_json=excluded.reimbursement_json, "
        "payable_if_resolved=excluded.payable_if_resolved, cost_usd=excluded.cost_usd, "
        "updated_ts=excluded.updated_ts, facts_json=excluded.facts_json, "
        "documents_present_json=excluded.documents_present_json",
        (
            decision.claim_id, manifest.packet_id, manifest.dealer_id, manifest.program_id, manifest.mode,
            manifest.medium, status, decision.decision, dumps(decision.reasons),
            dumps(decision.reimbursement) if decision.reimbursement else None, decision.payable_if_resolved,
            round(prior_cost + decision.cost_usd, 6), created_ts, ts, dumps(facts), dumps(documents_present), dumps(manifest),
        ),
    )

    conn.execute("DELETE FROM checks WHERE claim_id = ?", (decision.claim_id,))
    for c in checks:
        conn.execute(
            "INSERT INTO checks (claim_id, rule_id, verdict, kind, evidence_json, source_section, message, fix, ask) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (decision.claim_id, c.rule_id, c.verdict, c.kind, dumps(c.evidence), c.source.section, c.message, c.fix, c.ask),
        )

    conn.execute("DELETE FROM questions WHERE claim_id = ?", (decision.claim_id,))
    for q in decision.questions:
        conn.execute(
            "INSERT INTO questions (id, claim_id, rule_id, text, answers_json, resolved) VALUES (?,?,?,?,?,0)",
            (f"{decision.claim_id}-{q.id}", decision.claim_id, q.rule_id, q.text, dumps(q.answers)),
        )

    conn.execute("DELETE FROM documents WHERE claim_id = ?", (decision.claim_id,))
    files = manifest.files.model_dump()
    for kind, fname in files.items():
        if fname is None:
            continue
        present = (packet_dir / fname).exists()
        conn.execute("INSERT INTO documents (claim_id, kind, path, present) VALUES (?,?,?,?)",
                     (decision.claim_id, kind, fname, int(present)))

    conn.commit()


def _file_claim(conn: sqlite3.Connection, manifest: PacketManifest, decision: ClaimDecision) -> None:
    payable = decision.reimbursement.payable if decision.reimbursement else 0.0
    if payable > 0:
        ledger.add_entry(conn, manifest.dealer_id, manifest.program_id, "pending", -payable,
                          claim_id=decision.claim_id, note=f"Filed claim {decision.claim_id}")
    audit.log(conn, "file", "agent", claim_id=decision.claim_id, note=f"Filed for ${payable:,.2f}")


def _load_claim_context(conn: sqlite3.Connection, claim_id: str):
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        raise ValueError(f"no such claim: {claim_id}")
    manifest = PacketManifest.model_validate(json.loads(row["manifest_json"]))
    facts = ObservedFacts.model_validate(json.loads(row["facts_json"]))
    documents_present = json.loads(row["documents_present_json"]) if row["documents_present_json"] else {}
    packet_dir = repo_path(get_config()["paths"]["packets_dir"], manifest.packet_id)
    rules = load_rules(conn, manifest.program_id)
    return manifest, facts, documents_present, packet_dir, rules


def resolve_claim(conn: sqlite3.Connection, claim_id: str, action: str, actor: str = "human:dealer", **payload) -> ClaimDecision:
    """Applies a human's answer to an open question, then re-runs rules and
    policy without calling the model again. See SPEC.md 8.6."""
    manifest, facts, documents_present, packet_dir, rules = _load_claim_context(conn, claim_id)
    cfg = get_config()
    threshold = cfg["policy"]["auto_file_threshold_usd"]
    tol = cfg["policy"]["amounts_match_tolerance_usd"]

    if action == "upload_document":
        doc_kind = payload["doc_kind"]
        documents_present[doc_kind] = True
        if doc_kind == "payment" and facts.payment is None:
            amount = facts.invoice.total if facts.invoice and facts.invoice.total is not None else (
                facts.claim_form.total_cost if facts.claim_form else 0.0)
            facts.payment = PaymentFacts(date=manifest.submitted_on, amount=amount, method="card")
        audit.log(conn, "answer", actor, claim_id=claim_id, note=f"Uploaded {doc_kind.replace('_', ' ')}")

    elif action == "confirm_offer_approved":
        documents_present["territory_manager_approval"] = True
        audit.log(conn, "answer", actor, claim_id=claim_id, note="Confirmed territory manager approval is on file")

    elif action == "resolve_amount":
        source = payload["source"]  # "invoice" | "claim_form" | "payment"
        candidates = {
            "invoice": facts.invoice.total if facts.invoice else None,
            "claim_form": facts.claim_form.total_cost if facts.claim_form else None,
            "payment": facts.payment.amount if facts.payment else None,
        }
        value = candidates.get(source)
        if value is not None:
            if facts.invoice:
                facts.invoice.total = value
            if facts.claim_form:
                facts.claim_form.total_cost = value
            if facts.payment:
                facts.payment.amount = value
        audit.log(conn, "answer", actor, claim_id=claim_id, note=f"Confirmed the {source.replace('_', ' ')} amount (${value:,.2f}) is correct")

    else:
        raise ValueError(f"unknown resolve action: {action}")

    return finish_claim(conn, claim_id, manifest, rules, facts, documents_present, threshold, tol, 0.0, packet_dir)


def approve_claim(conn: sqlite3.Connection, claim_id: str, actor: str = "human:reviewer") -> None:
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        raise ValueError(f"no such claim: {claim_id}")
    manifest = PacketManifest.model_validate(json.loads(row["manifest_json"]))
    reimb = json.loads(row["reimbursement_json"]) if row["reimbursement_json"] else None
    payable = reimb["payable"] if reimb else 0.0

    conn.execute("UPDATE claims SET status='filed', updated_ts=? WHERE id=?", (audit.now_iso(), claim_id))
    conn.commit()
    if payable > 0:
        ledger.add_entry(conn, manifest.dealer_id, manifest.program_id, "pending", -payable,
                          claim_id=claim_id, note=f"Approved and filed claim {claim_id}")
    audit.log(conn, "approve", actor, claim_id=claim_id, note=f"Approved for ${payable:,.2f}")


def reject_claim(conn: sqlite3.Connection, claim_id: str, actor: str = "human:reviewer", reason: str | None = None) -> None:
    conn.execute("UPDATE claims SET status='rejected', updated_ts=? WHERE id=?", (audit.now_iso(), claim_id))
    conn.commit()
    audit.log(conn, "reject", actor, claim_id=claim_id, note=reason or "Rejected by reviewer")
