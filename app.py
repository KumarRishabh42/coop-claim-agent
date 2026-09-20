"""Web UI and endpoints. No model calls happen here directly — everything
goes through agent/pipeline.py. See SPEC.md section 11 and UI-SPEC.md."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent import audit, ledger, pipeline
from agent.config import IS_SERVERLESS, get_config, repo_path, writable_path
from agent.db import db_path, dumps, get_conn, init_db, reset_db
from agent.extract_rules import extract_rules
from agent.models import PacketFiles, PacketManifest, PhysicalSize
from agent.pdf import ensure_image_bytes, ensure_text

app = FastAPI(title="Co-op claims")
templates = Jinja2Templates(directory=str(repo_path("templates", "ui")))
app.mount("/static", StaticFiles(directory=str(repo_path("templates", "static"))), name="static")


# Serves both the bundled demo packets/guides (repo_path, read-only) and
# anything uploaded at runtime (writable_path — /tmp on a serverless host).
# A plain StaticFiles mount can only point at one directory; this checks both.
def _serve_from(*roots: Path):
    def handler(path: str):
        for root in roots:
            candidate = (root / path).resolve()
            if candidate.exists() and root.resolve() in candidate.parents:
                return FileResponse(candidate)
        raise HTTPException(404)
    return handler


app.get("/packets/{path:path}")(_serve_from(writable_path("data", "packets"), repo_path("data", "packets")))
app.get("/guides/{path:path}")(_serve_from(writable_path("data", "guides"), repo_path("data", "guides")))


@app.on_event("startup")
def _self_seed_on_cold_start():
    """Serverless hosts have no build-time `make data` step, and /tmp starts
    empty on every cold start — seed the canonical demo straight from the
    bundled replay fixtures (no network) so the app isn't blank/broken."""
    if IS_SERVERLESS and not db_path().exists():
        from agent.seed import run_all_packets, seed_bmw_program, seed_program_and_rules
        conn = reset_db()
        seed_program_and_rules(conn)
        run_all_packets(conn)
        seed_bmw_program(conn)
        conn.close()

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


def _dealer_id() -> str:
    return _cfg()["program"]["dealer_id"]


def _program_id(conn: sqlite3.Connection) -> str:
    """The active program. Hardcoded rather than read from app_state: on a
    serverless host each instance has its own isolated /tmp, so a value
    written by one request (e.g. a live /program/upload) is invisible to
    whichever instance handles the next one — DB-stored "active program"
    flaps unpredictably there. A fixed constant is identical everywhere."""
    from agent.seed import BMW_PROGRAM_ID
    return BMW_PROGRAM_ID


def _set_active_program(conn: sqlite3.Connection, program_id: str) -> None:
    conn.execute("INSERT INTO app_state (key, value) VALUES ('active_program_id', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (program_id,))
    conn.commit()


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
    program_id, dealer_id = _program_id(conn), _dealer_id()
    dealer = conn.execute("SELECT * FROM dealers WHERE id=?", (dealer_id,)).fetchone()
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    claim_rows = conn.execute("SELECT * FROM claims ORDER BY id").fetchall()
    claims = [_claim_row_to_view(conn, r) for r in claim_rows]
    needs_you = _needs_you_rows(conn)

    total_cost = audit.total_cost(conn)
    n_claims = len(claim_rows)
    cost_per_task = round(total_cost / n_claims, 2) if n_claims else 0.0

    claim_by = None
    for row in conn.execute("SELECT check_json FROM rules WHERE program_id=?", (program_id,)):
        check = json.loads(row["check_json"])
        if check.get("type") == "date_window" and check.get("claim_by"):
            claim_by = check["claim_by"]
            break

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


@app.get("/claims/upload")
def claim_upload_form(request: Request, conn: sqlite3.Connection = Depends(db)):
    program_id = _program_id(conn)
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    import datetime
    # DECISION: hardcoded rather than today's real date — a real date drifts
    # day to day and can wander outside a guide's claim window during a demo.
    today = "2026-10-01"
    return render(request, "claim_upload.html", {"program": program, "today": today}, conn, "claims")


@app.post("/claims/upload")
async def claim_upload(
    request: Request,
    mode: str = Form(...),
    medium: str = Form(...),
    submitted_on: str = Form(...),
    width_in: str = Form(""),
    height_in: str = Form(""),
    ad_file: UploadFile = File(...),
    invoice_file: UploadFile | None = File(None),
    payment_file: UploadFile | None = File(None),
    claim_form_file: UploadFile | None = File(None),
    affidavit_file: UploadFile | None = File(None),
    conn: sqlite3.Connection = Depends(db),
):
    program_id = _program_id(conn)
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    dealer_id = _dealer_id()

    packet_id = f"U{int(time.time() * 1000) % 100000}"
    packet_dir = writable_path("data", "packets", packet_id)
    packet_dir.mkdir(parents=True, exist_ok=True)

    files = {}
    async def save(upload: UploadFile | None, key: str):
        if upload is None or not upload.filename:
            return
        content = await upload.read()
        png = ensure_image_bytes(upload.filename, content)
        fname = f"{key}.png"
        (packet_dir / fname).write_bytes(png)
        files[key] = fname

    await save(ad_file, "ad")
    if mode == "claim":
        await save(invoice_file, "invoice")
        await save(payment_file, "payment")
        await save(claim_form_file, "claim_form")
        await save(affidavit_file, "affidavit")
        # DECISION: if the dealer only has an ad handy, fall back to the
        # demo's sample invoice/payment/claim form ($760, matching) instead
        # of blocking on missing documents — lets "just drop in an ad" still
        # produce a full claim result.
        demo_assets = repo_path("demo_assets")
        for key in ("invoice", "payment", "claim_form"):
            if key not in files and (demo_assets / f"{key}.png").exists():
                import shutil
                shutil.copy(demo_assets / f"{key}.png", packet_dir / f"{key}.png")
                files[key] = f"{key}.png"
        if medium == "radio" and "affidavit" not in files and (demo_assets / "affidavit.png").exists():
            import shutil
            shutil.copy(demo_assets / "affidavit.png", packet_dir / "affidavit.png")
            files["affidavit"] = "affidavit.png"

    physical_size = None
    if width_in and height_in:
        try:
            physical_size = PhysicalSize(width=float(width_in), height=float(height_in))
        except ValueError:
            physical_size = None

    manifest = PacketManifest(
        packet_id=packet_id, mode=mode, dealer_id=dealer_id, program_id=program_id, medium=medium,
        physical_size_in=physical_size, submitted_on=submitted_on, files=PacketFiles(**files),
    )
    (packet_dir / "manifest.json").write_text(dumps(manifest))

    brand_name = program["name"] if program else "the manufacturer"
    pipeline.run_packet(conn, manifest, packet_dir, mode="live", brand_name=brand_name)
    return RedirectResponse(f"/claims/{packet_id}", status_code=303)



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
    claim_program = conn.execute("SELECT name, year FROM programs WHERE id=?", (row["program_id"],)).fetchone()
    program_label = f"{claim_program['name']} {claim_program['year']}" if claim_program else row["program_id"]

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
        "program_label": program_label,
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
    program_id = _program_id(conn)
    program = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    rule_rows = conn.execute("SELECT * FROM rules WHERE program_id=? ORDER BY id", (program_id,)).fetchall()
    rules = [dict(r, check=json.loads(r["check_json"]), applies_to=json.loads(r["applies_to_json"])) for r in rule_rows]
    verified_count = sum(1 for r in rules if r["quote_verified"])
    guide_path = Path(program["guide_path"]) if program else repo_path(get_config()["paths"]["guides_dir"], "northwind-2026.md")
    # A PDF's raw bytes aren't displayable text — read the plain-text copy
    # saved alongside it (see program_upload), and offer the original PDF
    # itself as a separate "view original" link/tab.
    is_pdf = guide_path.suffix.lower() == ".pdf"
    text_path = guide_path.with_suffix(".text.txt") if is_pdf else guide_path
    guide_text = text_path.read_text(errors="replace") if text_path.exists() else guide_path.read_text(errors="replace")
    marked_guide_html = _highlight_quotes(guide_text, rules)
    rules_cost = 0.0
    ev = conn.execute("SELECT cost_usd FROM audit_events WHERE step='extract_rules' ORDER BY id DESC LIMIT 1").fetchone()
    if ev:
        rules_cost = ev["cost_usd"]
    original_url = None
    if is_pdf:
        for base in (writable_path("data", "guides"), repo_path("data", "guides")):
            try:
                original_url = f"/guides/{guide_path.relative_to(base)}"
                break
            except ValueError:
                continue
    return render(request, "program.html", {
        "program": program, "rules": rules, "marked_guide_html": marked_guide_html,
        "verified_count": verified_count, "rules_cost": rules_cost, "original_url": original_url,
    }, conn, "program")


@app.get("/funds")
def funds_page(request: Request, conn: sqlite3.Connection = Depends(db)):
    program_id, dealer_id = _program_id(conn), _dealer_id()
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


# ---------------------------------------------------------------------------
# Uploads: a real guide (policy) and a real dealer packet, processed live.
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "guide"


@app.get("/program/upload")
def program_upload_form(request: Request, conn: sqlite3.Connection = Depends(db)):
    return render(request, "program_upload.html", {}, conn, "program")


@app.post("/program/upload")
async def program_upload(request: Request, guide_file: UploadFile = File(...), brand_name: str = Form(""),
                          conn: sqlite3.Connection = Depends(db)):
    content = await guide_file.read()
    guide_text = ensure_text(guide_file.filename, content)

    program_id = f"{_slug(brand_name or guide_file.filename)}-{int(time.time())}"
    guides_dir = writable_path("data", "guides", "uploaded")
    guides_dir.mkdir(parents=True, exist_ok=True)
    ext = "pdf" if guide_file.filename.lower().endswith(".pdf") else "txt"
    guide_path = guides_dir / f"{program_id}.{ext}"
    guide_path.write_bytes(content)
    # Also keep a plain-text copy so the program page can always display it.
    (guides_dir / f"{program_id}.text.txt").write_text(guide_text)

    rules, usage = extract_rules(program_id, guide_text, mode="live")
    audit.log(conn, "extract_rules", "agent", input_ref=str(guide_path), model=usage.model,
              tokens_in=usage.tokens_in, tokens_out=usage.tokens_out, cost_usd=usage.cost_usd,
              note=f"{len(rules)} rules extracted from an uploaded guide")

    r1 = next((r for r in rules if r.check.type == "funds_terms"), None)
    rate = r1.check.rate if r1 and hasattr(r1.check, "rate") else 0.5
    accrual_rate = r1.check.accrual_rate if r1 and hasattr(r1.check, "accrual_rate") else 0.02
    program_name = brand_name or guide_file.filename.rsplit(".", 1)[0]

    conn.execute(
        "INSERT INTO programs (id, name, year, guide_path, rate, accrual_rate) VALUES (?,?,?,?,?,?)",
        (program_id, program_name, 2026, str(guide_path), rate, accrual_rate),
    )
    for r in rules:
        conn.execute(
            "INSERT INTO rules (id, program_id, title, kind, applies_to_json, check_json, source_section, "
            "source_quote, on_fail, quote_verified) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.id, r.program_id, r.title, r.kind, dumps(r.applies_to), dumps(r.check), r.source.section,
             r.source.quote, r.on_fail, int(r.quote_verified)),
        )
    conn.commit()

    dealer_id = _dealer_id()
    if not conn.execute("SELECT 1 FROM dealers WHERE id=?", (dealer_id,)).fetchone():
        conn.execute("INSERT INTO dealers (id, name) VALUES (?, ?)", (dealer_id, "Summit Heating and Air"))
        conn.commit()
    # DECISION: a demo opening balance, since we have no real purchase
    # history for a freshly uploaded program. Generous enough that a few
    # sample claims don't hit the balance cap.
    ledger.add_entry(conn, dealer_id, program_id, "accrual", 50000.0,
                      note="Demo opening balance for an uploaded program")

    _set_active_program(conn, program_id)
    return RedirectResponse("/program", status_code=303)

