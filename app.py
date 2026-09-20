"""Web UI and endpoints. No model calls happen here directly — everything
goes through agent/pipeline.py. See SPEC.md section 11 and UI-SPEC.md."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent import audit, ledger, pipeline
from agent.config import get_config, repo_path
from agent.db import get_conn, init_db

app = FastAPI(title="Co-op claims")
templates = Jinja2Templates(directory=str(repo_path("templates", "ui")))
app.mount("/static", StaticFiles(directory=str(repo_path("templates", "static"))), name="static")
app.mount("/packets", StaticFiles(directory=str(repo_path("data", "packets"))), name="packets")

# UI-SPEC.md 4.1
DECISION_LABELS = {
    "auto_file": "Filed automatically",
    "needs_approval": "Needs approval",
    "hold_question": "Stopped to ask",
    "blocked_missing_doc": "Blocked, missing document",
    "fix_needed": "Fix needed",
    "not_eligible": "Not eligible",
    "ready_to_run": "Ready to run",
}
DECISION_CHIP = {
    "auto_file": "pass",
    "needs_approval": "ask",
    "hold_question": "ask",
    "blocked_missing_doc": "ask",
    "fix_needed": "fail",
    "not_eligible": "fail",
    "ready_to_run": "pass",
}
MEDIUM_LABELS = {
    "direct_mail": "Direct mail", "newspaper": "Newspaper", "radio": "Radio",
    "paid_social": "Paid social", "paid_search": "Paid search",
}
QUEUE_DECISIONS = ("needs_approval", "hold_question", "blocked_missing_doc")


def db() -> sqlite3.Connection:
    conn = get_conn()
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def render(request: Request, name: str, context: dict, conn: sqlite3.Connection, active: str, status_code: int = 200):
    needs_you_count = conn.execute(
        f"SELECT COUNT(*) AS c FROM claims WHERE decision IN {QUEUE_DECISIONS}"
    ).fetchone()["c"]
    context = {**context, "active": active, "needs_you_count": needs_you_count}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _cfg():
    return get_config()


def _program_id() -> str:
    return _cfg()["program"]["id"]


def _dealer_id() -> str:
    return _cfg()["program"]["dealer_id"]


def _why(conn: sqlite3.Connection, claim_id: str, decision: str) -> str:
    """One sentence ending in the cited section, per UI-SPEC.md copy rules."""
    if decision in ("fix_needed", "not_eligible", "hold_question", "blocked_missing_doc"):
        row = conn.execute(
            "SELECT message, source_section FROM checks WHERE claim_id=? AND verdict IN ('fail','unsure') "
            "ORDER BY (verdict='fail') DESC LIMIT 1", (claim_id,)
        ).fetchone()
        if row:
            return f"{row['message']} Section {row['source_section']}."
    if decision in ("auto_file", "needs_approval"):
        n = conn.execute("SELECT COUNT(*) AS c FROM checks WHERE claim_id=?", (claim_id,)).fetchone()["c"]
        extra = "" if decision == "auto_file" else " Over the auto-file limit."
        return f"All {n} rules pass.{extra}"
    return ""


def _claim_row_to_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    reimb = json.loads(row["reimbursement_json"]) if row["reimbursement_json"] else None
    return {
        "id": row["id"], "medium": MEDIUM_LABELS.get(row["medium"], row["medium"]), "mode": row["mode"],
        "status": row["status"], "decision": row["decision"],
        "decision_label": DECISION_LABELS.get(row["decision"], row["decision"]),
        "tone": DECISION_CHIP.get(row["decision"], "info"),
        "payable": reimb["payable"] if reimb else None,
        "payable_if_resolved": row["payable_if_resolved"],
        "cost_usd": row["cost_usd"],
        "why": _why(conn, row["id"], row["decision"]),
    }


def _needs_you_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"SELECT * FROM claims WHERE decision IN {QUEUE_DECISIONS} ORDER BY (decision='needs_approval'), id"
    ).fetchall()
    out = []
    for r in rows:
        if r["decision"] == "needs_approval":
            reimb = json.loads(r["reimbursement_json"]) if r["reimbursement_json"] else None
            text, action = f"Ready to file for ${reimb['payable']:,.0f}, over the auto-file limit." if reimb else "Ready for approval.", "Approve"
        elif r["decision"] == "blocked_missing_doc":
            q = conn.execute("SELECT text FROM questions WHERE claim_id=? AND resolved=0 LIMIT 1", (r["id"],)).fetchone()
            text, action = (q["text"] if q else "Missing a document."), "Upload"
        else:
            q = conn.execute("SELECT text FROM questions WHERE claim_id=? AND resolved=0 LIMIT 1", (r["id"],)).fetchone()
            text, action = (q["text"] if q else "Has an open question."), "Answer"
        out.append({"id": r["id"], "text": text, "action": action, "decision": r["decision"]})
    return out


@app.get("/")
def home(request: Request, conn: sqlite3.Connection = Depends(db)):
    program_id, dealer_id = _program_id(), _dealer_id()
    dealer = conn.execute("SELECT * FROM dealers WHERE id=?", (dealer_id,)).fetchone()
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    claim_rows = conn.execute("SELECT * FROM claims ORDER BY id").fetchall()
    claims = [_claim_row_to_view(conn, r) for r in claim_rows]
    needs_you = _needs_you_rows(conn)

    total_cost = audit.total_cost(conn)
    n_claims = len(claim_rows)
    cost_per_task = round(total_cost / n_claims, 2) if n_claims else 0.0

    r7 = conn.execute("SELECT check_json FROM rules WHERE id='R7' AND program_id=?", (program_id,)).fetchone()
    claim_by = json.loads(r7["check_json"]).get("claim_by") if r7 else None

    balance = ledger.balance(conn, dealer_id, program_id) if program else 0.0
    accrued = ledger.accrued_total(conn, dealer_id, program_id) if program else 0.0
    recovered = ledger.reimbursed_total(conn, dealer_id, program_id) if program else 0.0

    filed = [c for c in claims if c["status"] == "filed"]
    filed_amount = sum(c["payable"] or 0 for c in filed)
    filed_auto = sum(1 for c in filed if c["decision"] == "auto_file")
    waiting_amount = sum(c["payable_if_resolved"] or 0 for c in claims if c["decision"] in QUEUE_DECISIONS)
    waiting_count = sum(1 for c in claims if c["decision"] in QUEUE_DECISIONS)

    return render(request, "home.html", {
        "dealer": dealer, "program": program, "claims": claims, "needs_you": needs_you,
        "balance": balance, "accrued": accrued, "recovered": recovered, "claim_by": claim_by,
        "total_cost": total_cost, "cost_per_task": cost_per_task, "n_claims": n_claims,
        "rate": program["rate"] if program else None,
        "filed_amount": filed_amount, "filed_count": len(filed), "filed_auto": filed_auto,
        "waiting_amount": waiting_amount, "waiting_count": waiting_count,
    }, conn, "home")


@app.post("/run_all")
def run_all(conn: sqlite3.Connection = Depends(db)):
    from agent.seed import run_all_packets
    run_all_packets(conn)
    return RedirectResponse("/", status_code=303)


@app.get("/claims/{claim_id}")
def claim_detail(claim_id: str, request: Request, conn: sqlite3.Connection = Depends(db)):
    row = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if row is None:
        return render(request, "not_found.html", {"claim_id": claim_id}, conn, "home", status_code=404)

    checks = conn.execute("SELECT * FROM checks WHERE claim_id=? ORDER BY (verdict='pass'), (verdict='not_applicable'), rule_id", (claim_id,)).fetchall()
    checks = [dict(c, evidence=json.loads(c["evidence_json"])) for c in checks]
    questions = conn.execute("SELECT * FROM questions WHERE claim_id=?", (claim_id,)).fetchall()
    documents = conn.execute("SELECT * FROM documents WHERE claim_id=?", (claim_id,)).fetchall()
    events = audit.list_events(conn, claim_id)
    manifest = json.loads(row["manifest_json"])
    facts = json.loads(row["facts_json"]) if row["facts_json"] else {}
    reimb = json.loads(row["reimbursement_json"]) if row["reimbursement_json"] else None

    ad_box = None
    ad = facts.get("ad")
    if ad and ad.get("image_size_px") and (ad.get("brand_logo_bbox_px") or ad.get("dealer_logo_bbox_px")):
        w, h = ad["image_size_px"]
        def pct(bbox):
            return {"left": bbox[0] / w * 100, "top": bbox[1] / h * 100,
                    "width": (bbox[2] - bbox[0]) / w * 100, "height": (bbox[3] - bbox[1]) / h * 100}
        ad_box = {
            "brand": pct(ad["brand_logo_bbox_px"]) if ad.get("brand_logo_bbox_px") else None,
            "dealer": pct(ad["dealer_logo_bbox_px"]) if ad.get("dealer_logo_bbox_px") else None,
        }
    r3 = next((c for c in checks if c["rule_id"] == "R3"), None)
    brand_in = r3["evidence"].get("brand_logo_in") if r3 else None
    dealer_in = r3["evidence"].get("dealer_logo_in") if r3 else None
    r3_fail = bool(r3 and r3["verdict"] == "fail")

    missing_docs = []
    for c in checks:
        if c["evidence"].get("missing"):
            missing_docs = c["evidence"]["missing"]

    # Progress steps, UI-SPEC.md 4.7
    steps = []
    if manifest["mode"] == "claim":
        n = len(checks)
        s1_state = "now" if row["decision"] == "hold_question" else "done"
        steps.append({"label": f"Agent checked {n} rules" if row["decision"] != "hold_question" else "Agent stopped to ask", "state": s1_state})
        answered = questions and any(True for q in questions) and row["decision"] != "hold_question"
        s2_label = "No question asked" if not questions else ("You answered" if answered else "Waiting on your answer")
        steps.append({"label": s2_label, "state": "done" if answered or not questions else ("now" if row["decision"] == "hold_question" else "next")})
        if row["decision"] == "needs_approval":
            s3_state, s3_label = "now", f"Your approval, ${reimb['payable']:,.0f}" if reimb else "Your approval"
        elif row["status"] == "filed" and row["decision"] in ("needs_approval",):
            s3_state, s3_label = "done", "Approved"
        else:
            s3_state, s3_label = ("done" if row["status"] == "filed" else "next"), "No approval needed"
        steps.append({"label": s3_label, "state": s3_state})
        if row["status"] == "filed":
            steps.append({"label": "Filed", "state": "now" if row["decision"] != "needs_approval" else "done"})
        else:
            steps.append({"label": "File and follow up", "state": "next"})

    return render(request, "claim.html", {
        "claim": dict(row, decision_label=DECISION_LABELS.get(row["decision"], row["decision"]),
                      tone=DECISION_CHIP.get(row["decision"], "info")),
        "checks": checks, "questions": questions, "documents": documents, "events": events,
        "manifest": manifest, "facts": facts, "reimb": reimb, "ad_box": ad_box,
        "ad_file": manifest["files"].get("ad"), "script_file": manifest["files"].get("script"),
        "missing_docs": missing_docs, "medium_label": MEDIUM_LABELS.get(row["medium"], row["medium"]),
        "brand_in": brand_in, "dealer_in": dealer_in, "r3_fail": r3_fail, "steps": steps,
        "cost_this_claim": sum(e.cost_usd for e in events),
    }, conn, "home")


@app.post("/claims/{claim_id}/answer")
def answer(claim_id: str, action: str = Form(...), doc_kind: str = Form(None), source: str = Form(None),
           conn: sqlite3.Connection = Depends(db)):
    kwargs = {}
    if doc_kind:
        kwargs["doc_kind"] = doc_kind
    if source:
        kwargs["source"] = source
    pipeline.resolve_claim(conn, claim_id, action, actor="human:office_manager", **kwargs)
    return RedirectResponse(f"/claims/{claim_id}", status_code=303)


@app.post("/claims/{claim_id}/approve")
def approve(claim_id: str, conn: sqlite3.Connection = Depends(db)):
    pipeline.approve_claim(conn, claim_id, actor="human:reviewer")
    return RedirectResponse(f"/claims/{claim_id}", status_code=303)


@app.post("/claims/{claim_id}/reject")
def reject(claim_id: str, reason: str = Form(""), conn: sqlite3.Connection = Depends(db)):
    pipeline.reject_claim(conn, claim_id, actor="human:reviewer", reason=reason or None)
    return RedirectResponse(f"/claims/{claim_id}", status_code=303)


@app.get("/queue")
def queue(request: Request, conn: sqlite3.Connection = Depends(db)):
    rows = conn.execute(f"SELECT * FROM claims WHERE decision IN {QUEUE_DECISIONS} ORDER BY id").fetchall()
    claims = [_claim_row_to_view(conn, r) for r in rows]
    return render(request, "queue.html", {"claims": claims}, conn, "queue")


def _highlight_quotes(guide_text: str, rules: list[dict]) -> str:
    """Wraps each verified rule's quote in <mark>, matching against the raw
    guide text even though the quote was normalized for verification (guide
    lines wrap with indentation that a one-line quote doesn't have)."""
    import html as _html
    escaped = _html.escape(guide_text)
    for r in rules:
        if not r["quote_verified"]:
            continue
        pattern = r"\s+".join(re.escape(_html.escape(w)) for w in r["source_quote"].split())
        escaped = re.sub(f"({pattern})", r"<mark>\1</mark>", escaped, count=1, flags=re.IGNORECASE)
    return escaped


@app.get("/program")
def program_page(request: Request, conn: sqlite3.Connection = Depends(db)):
    program_id = _program_id()
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    rule_rows = conn.execute("SELECT * FROM rules WHERE program_id=? ORDER BY id", (program_id,)).fetchall()
    rules = [dict(r, check=json.loads(r["check_json"]), applies_to=json.loads(r["applies_to_json"])) for r in rule_rows]
    verified_count = sum(1 for r in rules if r["quote_verified"])
    guide_text = repo_path(get_config()["paths"]["guides_dir"], "northwind-2026.md").read_text()
    marked_guide_html = _highlight_quotes(guide_text, rules)
    rules_cost = 0.0
    ev = conn.execute("SELECT cost_usd FROM audit_events WHERE step='extract_rules' ORDER BY id DESC LIMIT 1").fetchone()
    if ev:
        rules_cost = ev["cost_usd"]
    return render(request, "program.html", {
        "program": program, "rules": rules, "marked_guide_html": marked_guide_html,
        "verified_count": verified_count, "rules_cost": rules_cost,
    }, conn, "program")


@app.get("/funds")
def funds_page(request: Request, conn: sqlite3.Connection = Depends(db)):
    program_id, dealer_id = _program_id(), _dealer_id()
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    raw_entries = ledger.entries(conn, dealer_id, program_id)
    metrics = {
        "accrued": ledger.accrued_total(conn, dealer_id, program_id),
        "reimbursed": ledger.reimbursed_total(conn, dealer_id, program_id),
        "pending": ledger.pending_total(conn, dealer_id, program_id),
        "balance": ledger.balance(conn, dealer_id, program_id),
    }
    running = 0.0
    entries = []
    for e in raw_entries:
        running += e["amount"]
        entries.append({"ts": e["ts"], "kind": e["kind"], "note": e["note"], "claim_id": e["claim_id"],
                         "amount": e["amount"], "running": running})
    return render(request, "funds.html", {"program": program, "entries": entries, "metrics": metrics}, conn, "funds")


@app.get("/audit")
def audit_page(request: Request, conn: sqlite3.Connection = Depends(db)):
    events = audit.list_events(conn)
    total = audit.total_cost(conn)
    n_calls = sum(1 for e in events if e.tokens_in or e.tokens_out)
    n_claims = conn.execute("SELECT COUNT(*) AS c FROM claims").fetchone()["c"]
    return render(request, "audit.html", {
        "events": list(reversed(events)), "total_cost": total, "n_calls": n_calls, "n_claims": n_claims,
    }, conn, "audit")
