"""Web UI and endpoints. No model calls happen here directly — everything
goes through agent/pipeline.py. See SPEC.md section 11."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent import audit, ledger, pipeline
from agent.config import get_config, repo_path
from agent.db import get_conn, init_db

app = FastAPI(title="Co-op claim agent")
templates = Jinja2Templates(directory=str(repo_path("templates", "ui")))
app.mount("/static", StaticFiles(directory=str(repo_path("templates", "static"))), name="static")
app.mount("/packets", StaticFiles(directory=str(repo_path("data", "packets"))), name="packets")

DECISION_LABELS = {
    "auto_file": "Filed",
    "needs_approval": "Needs approval",
    "hold_question": "Question open",
    "blocked_missing_doc": "Missing document",
    "fix_needed": "Fix needed",
    "not_eligible": "Not eligible",
    "ready_to_run": "Ready to run",
}
DECISION_TONES = {
    "auto_file": "good",
    "needs_approval": "info",
    "hold_question": "warn",
    "blocked_missing_doc": "warn",
    "fix_needed": "bad",
    "not_eligible": "bad",
    "ready_to_run": "good",
}
MEDIUM_LABELS = {
    "direct_mail": "Direct mail", "newspaper": "Newspaper", "radio": "Radio",
    "paid_social": "Paid social", "paid_search": "Paid search",
}


def db() -> sqlite3.Connection:
    conn = get_conn()
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def render(request: Request, name: str, context: dict, conn: sqlite3.Connection, active: str, status_code: int = 200):
    context = {**context, "active": active, "nav_cost": audit.total_cost(conn)}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _cfg():
    return get_config()


def _program_id() -> str:
    return _cfg()["program"]["id"]


def _dealer_id() -> str:
    return _cfg()["program"]["dealer_id"]


def _claim_row_to_view(row: sqlite3.Row) -> dict:
    reimb = json.loads(row["reimbursement_json"]) if row["reimbursement_json"] else None
    return {
        "id": row["id"], "medium": MEDIUM_LABELS.get(row["medium"], row["medium"]), "mode": row["mode"],
        "status": row["status"], "decision": row["decision"],
        "decision_label": DECISION_LABELS.get(row["decision"], row["decision"]),
        "tone": DECISION_TONES.get(row["decision"], "info"),
        "payable": reimb["payable"] if reimb else None,
        "payable_if_resolved": row["payable_if_resolved"],
        "cost_usd": row["cost_usd"],
    }


@app.get("/")
def home(request: Request, conn: sqlite3.Connection = Depends(db)):
    program_id, dealer_id = _program_id(), _dealer_id()
    dealer = conn.execute("SELECT * FROM dealers WHERE id=?", (dealer_id,)).fetchone()
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    claim_rows = conn.execute("SELECT * FROM claims ORDER BY id").fetchall()
    claims = [_claim_row_to_view(r) for r in claim_rows]

    needs_you = []
    for r in claim_rows:
        if r["decision"] not in ("hold_question", "blocked_missing_doc"):
            continue
        questions = conn.execute("SELECT * FROM questions WHERE claim_id=? AND resolved=0", (r["id"],)).fetchall()
        checks = conn.execute("SELECT * FROM checks WHERE claim_id=? AND verdict IN ('fail','unsure')", (r["id"],)).fetchall()
        text = questions[0]["text"] if questions else (checks[0]["message"] if checks else "Needs attention")
        needs_you.append({"id": r["id"], "text": text, "decision": r["decision"]})

    total_cost = audit.total_cost(conn)
    r7 = conn.execute("SELECT source_quote, check_json FROM rules WHERE id='R7' AND program_id=?", (program_id,)).fetchone()
    claim_by = None
    if r7:
        claim_by = json.loads(r7["check_json"]).get("claim_by")

    balance = ledger.balance(conn, dealer_id, program_id) if program else 0.0
    recovered = ledger.reimbursed_total(conn, dealer_id, program_id) if program else 0.0

    return render(request, "home.html", {
        "dealer": dealer, "program": program, "claims": claims, "needs_you": needs_you,
        "balance": balance, "recovered": recovered, "claim_by": claim_by, "total_cost": total_cost,
        "rate": program["rate"] if program else None,
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

    missing_docs = []
    for c in checks:
        if c["rule_id"] and c["evidence"].get("missing"):
            missing_docs = c["evidence"]["missing"]

    return render(request, "claim.html", {
        "claim": dict(row, decision_label=DECISION_LABELS.get(row["decision"], row["decision"]),
                      tone=DECISION_TONES.get(row["decision"], "info")),
        "checks": checks, "questions": questions, "documents": documents, "events": events,
        "manifest": manifest, "facts": facts, "reimb": reimb, "ad_box": ad_box,
        "ad_file": manifest["files"].get("ad"), "script_file": manifest["files"].get("script"),
        "missing_docs": missing_docs, "medium_label": MEDIUM_LABELS.get(row["medium"], row["medium"]),
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
    rows = conn.execute(
        "SELECT * FROM claims WHERE decision IN ('needs_approval','hold_question','blocked_missing_doc') ORDER BY id"
    ).fetchall()
    claims = [_claim_row_to_view(r) for r in rows]
    return render(request, "queue.html", {"claims": claims}, conn, "queue")


@app.get("/program")
def program_page(request: Request, conn: sqlite3.Connection = Depends(db)):
    program_id = _program_id()
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    rule_rows = conn.execute("SELECT * FROM rules WHERE program_id=? ORDER BY id", (program_id,)).fetchall()
    rules = [dict(r, check=json.loads(r["check_json"]), applies_to=json.loads(r["applies_to_json"])) for r in rule_rows]
    guide_text = repo_path(get_config()["paths"]["guides_dir"], "northwind-2026.md").read_text()
    return render(request, "program.html", {"program": program, "rules": rules, "guide_text": guide_text}, conn, "program")


@app.get("/funds")
def funds_page(request: Request, conn: sqlite3.Connection = Depends(db)):
    program_id, dealer_id = _program_id(), _dealer_id()
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    entries = ledger.entries(conn, dealer_id, program_id)
    metrics = {
        "accrued": ledger.accrued_total(conn, dealer_id, program_id),
        "reimbursed": ledger.reimbursed_total(conn, dealer_id, program_id),
        "pending": ledger.pending_total(conn, dealer_id, program_id),
        "balance": ledger.balance(conn, dealer_id, program_id),
    }
    return render(request, "funds.html", {"program": program, "entries": entries, "metrics": metrics}, conn, "funds")


@app.get("/audit")
def audit_page(request: Request, conn: sqlite3.Connection = Depends(db)):
    events = audit.list_events(conn)
    total = audit.total_cost(conn)
    return render(request, "audit.html", {"events": events, "total_cost": total}, conn, "audit")
