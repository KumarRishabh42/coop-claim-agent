"""Renders the six synthetic claim packets: ad images, simple document
cards, manifests, and hand-authored "ground truth" replay fixtures for the
model calls (precheck_ad, extract_documents).

# DECISION: SPEC.md calls for Playwright + HTML templates. For PoC speed
# this uses Pillow to draw the ads and document cards directly instead of
# installing a browser — same planted-violation design (logos are colored
# boxes at exact, code-controlled widths), far less setup. Revisit if the
# demo needs richer creative.
#
# The replay fixtures written here encode the exact values planted in each
# ad/document, i.e. what a model reading them accurately *should* see. That
# keeps `make test` deterministic without needing a live model. A live key
# can still record real fixtures over these (see agent/llm.py, LLM_MODE=record).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PACKETS_DIR = REPO_ROOT / "data" / "packets"
RECORDINGS_DIR = REPO_ROOT / "data" / "recordings"
PX_PER_IN = 200
DEALER_NAME = "Summit Heating and Air"
BRAND_NAME = "Northwind Comfort"

DEALER_COLOR = (211, 84, 0)
BRAND_COLOR = (30, 89, 148)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _centered_text(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int], fill: str, max_size: int = 22):
    x0, y0, x1, y1 = box
    size = max_size
    font = _font(size)
    while size > 8:
        font = _font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= (x1 - x0) - 12:
            break
        size -= 2
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x0 + (x1 - x0 - tw) / 2, y0 + (y1 - y0 - th) / 2 - bbox[1]), text, fill=fill, font=font)


def render_ad(
    path: Path, width_in: float, height_in: float, dealer_logo_in: float, brand_logo_in: float,
    headline: str, offer_line: str | None, tagline: str | None,
) -> dict:
    w, h = int(width_in * PX_PER_IN), int(height_in * PX_PER_IN)
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)

    dealer_w, dealer_h = int(dealer_logo_in * PX_PER_IN), 84
    dx0, dy0 = 40, 36
    dx1, dy1 = dx0 + dealer_w, dy0 + dealer_h
    draw.rectangle([dx0, dy0, dx1, dy1], fill=DEALER_COLOR)
    _centered_text(draw, DEALER_NAME, (dx0, dy0, dx1, dy1), "white")

    brand_w, brand_h = int(brand_logo_in * PX_PER_IN), 52
    bx1, by0 = w - 40, 40
    bx0, by1 = bx1 - brand_w, by0 + brand_h
    draw.rectangle([bx0, by0, bx1, by1], fill=BRAND_COLOR)
    _centered_text(draw, BRAND_NAME, (bx0, by0, bx1, by1), "white")

    lines = [headline]
    if offer_line:
        lines.append(offer_line)
    if tagline:
        lines.append(tagline)

    y = max(dy1, by1) + 36
    body_font = _font(28)
    for line in lines:
        draw.text((40, y), line, fill="black", font=body_font)
        y += 40

    draw.rectangle([0, 0, w - 1, h - 1], outline=(200, 200, 200), width=2)
    img.save(path)

    return {
        "image_size_px": [w, h],
        "brand_logo_bbox_px": [bx0, by0, bx1, by1],
        "dealer_logo_bbox_px": [dx0, dy0, dx1, dy1],
        "text_found": lines,
        "brands_mentioned": [BRAND_NAME],
        "offer_phrases": [offer_line] if offer_line else [],
    }


def render_document_card(path: Path, title: str, lines: list[str]) -> None:
    w, h = 900, 620
    img = Image.new("RGB", (w, h), (250, 250, 248))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w - 1, h - 1], outline=(210, 210, 205), width=2)
    draw.rectangle([0, 0, w - 1, 70], fill=(60, 60, 60))
    draw.text((28, 20), title, fill="white", font=_font(28))
    y = 110
    for line in lines:
        draw.text((28, y), line, fill=(40, 40, 40), font=_font(22))
        y += 42
    img.save(path)


def write_fixture(task: str, key: str, result: dict, tokens_in: int, tokens_out: int) -> None:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    price_in, price_out = 0.000002, 0.00001
    cost = round(tokens_in * price_in + tokens_out * price_out, 6)
    payload = {
        "result": result,
        "usage": {"model": "claude-sonnet-5", "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost},
    }
    (RECORDINGS_DIR / f"{task}__{key}.json").write_text(json.dumps(payload, indent=2))


def make_packet(spec: dict) -> None:
    pid = spec["packet_id"]
    pdir = PACKETS_DIR / pid
    if pdir.exists():
        shutil.rmtree(pdir)
    pdir.mkdir(parents=True)

    files: dict[str, str | None] = {}
    ad_facts: dict

    if spec["medium"] == "radio":
        script = spec["script_text"]
        (pdir / "script.txt").write_text(script)
        files["script"] = "script.txt"
        ad_facts = {
            "image_size_px": None, "brand_logo_bbox_px": None, "dealer_logo_bbox_px": None,
            "text_found": [script], "brands_mentioned": [BRAND_NAME], "offer_phrases": [],
        }
    else:
        ad_facts = render_ad(
            pdir / "ad.png", spec["width_in"], spec["height_in"], spec["dealer_logo_in"], spec["brand_logo_in"],
            spec["headline"], spec.get("offer_line"), spec.get("tagline"),
        )
        files["ad"] = "ad.png"

    doc_facts: dict = {"invoice": None, "payment": None, "claim_form": None, "affidavit": None}

    if spec["mode"] == "claim":
        if spec.get("has_invoice", True):
            line_item_lines = [f"  {li['desc']}: ${li['amount']:,.2f}" for li in spec["line_items"]]
            render_document_card(pdir / "invoice.png", "Vendor invoice", [
                f"Vendor: {spec['vendor']}", f"Invoice #: {spec['invoice_number']}",
                f"Date: {spec['invoice_date']}", *line_item_lines, f"Total: ${spec['invoice_total']:,.2f}",
            ])
            files["invoice"] = "invoice.png"
            doc_facts["invoice"] = {
                "vendor": spec["vendor"], "number": spec["invoice_number"], "date": spec["invoice_date"],
                "total": spec["invoice_total"], "line_items": spec["line_items"],
            }

        if spec.get("has_payment", True):
            render_document_card(pdir / "payment.png", "Proof of payment", [
                f"Date: {spec['payment_date']}", f"Amount: ${spec['payment_amount']:,.2f}", f"Method: {spec['payment_method']}",
            ])
            files["payment"] = "payment.png"
            doc_facts["payment"] = {"date": spec["payment_date"], "amount": spec["payment_amount"], "method": spec["payment_method"]}

        if spec.get("has_claim_form", True):
            render_document_card(pdir / "claim_form.png", "Co-op claim form", [
                f"Medium: {spec['medium']}", f"Total cost: ${spec['claim_form_total']:,.2f}",
                f"Requested: ${spec['claim_form_total'] * 0.5:,.2f}",
            ])
            files["claim_form"] = "claim_form.png"
            doc_facts["claim_form"] = {"medium": spec["medium"], "total_cost": spec["claim_form_total"], "requested": round(spec["claim_form_total"] * 0.5, 2)}

        if spec["medium"] == "radio":
            render_document_card(pdir / "affidavit.png", "Station affidavit", [
                f"Station: {spec.get('station', 'WNCR-FM')}", f"Air dates: {', '.join(spec.get('air_dates', []))}",
            ])
            files["affidavit"] = "affidavit.png"
            doc_facts["affidavit"] = {"station": spec.get("station", "WNCR-FM"), "air_dates": spec.get("air_dates", [])}

    manifest = {
        "packet_id": pid, "mode": spec["mode"], "dealer_id": "summit", "program_id": "northwind-2026",
        "medium": spec["medium"],
        "physical_size_in": {"width": spec["width_in"], "height": spec["height_in"]} if spec["medium"] != "radio" else None,
        "submitted_on": spec["submitted_on"], "files": files,
    }
    (pdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (pdir / "expected.json").write_text(json.dumps(spec["expected"], indent=2))

    write_fixture("precheck_ad", pid, ad_facts, tokens_in=1400, tokens_out=260)
    if spec["mode"] == "claim":
        write_fixture("extract_documents", pid, doc_facts, tokens_in=900, tokens_out=340)

    print(f"  packet {pid}: {spec['mode']:8s} {spec['medium']:12s} -> {pdir.relative_to(REPO_ROOT)}")


PACKETS = [
    dict(
        packet_id="A", mode="claim", medium="direct_mail", width_in=6, height_in=4,
        dealer_logo_in=2.4, brand_logo_in=1.6,
        headline="Fall furnace tune-up, $89", tagline="Comfort you can count on",
        submitted_on="2026-10-01",
        vendor="PrintPost Mailers", invoice_number="7711", invoice_date="2026-09-14", invoice_total=760.00,
        line_items=[{"desc": "Printing", "amount": 460.00}, {"desc": "Postage", "amount": 300.00}],
        payment_date="2026-09-16", payment_amount=760.00, payment_method="card",
        claim_form_total=760.00,
        expected={"decision": "auto_file", "payable": 380.0, "verdicts": {"R3": "pass", "R4": "pass", "R7": "pass", "R8": "pass", "R9": "pass"}},
    ),
    dict(
        packet_id="B", mode="precheck", medium="newspaper", width_in=5, height_in=4,
        dealer_logo_in=2.4, brand_logo_in=0.7,
        headline="Fall furnace tune-up, $89", tagline="Comfort you can count on",
        submitted_on="2026-10-01",
        expected={"decision": "fix_needed", "payable": None, "verdicts": {"R3": "fail"}, "fix_contains": "1.2 in"},
    ),
    dict(
        packet_id="C", mode="claim", medium="radio", width_in=None, height_in=None,
        script_text='"Comfort you can count on" — Summit Heating and Air is a proud Northwind Comfort dealer. '
                     "Call today for your fall furnace tune-up.",
        submitted_on="2026-10-20",
        vendor="WNCR-FM", invoice_number="R-4402", invoice_date="2026-08-03", invoice_total=4800.00,
        line_items=[{"desc": "Airtime", "amount": 4800.00}],
        payment_date="2026-08-05", payment_amount=4800.00, payment_method="ach",
        claim_form_total=4800.00, station="WNCR-FM", air_dates=["2026-08-10", "2026-08-17"],
        expected={"decision": "not_eligible", "payable": 0.0, "verdicts": {"R7": "fail"}},
    ),
    dict(
        packet_id="D", mode="claim", medium="paid_social", width_in=4, height_in=4,
        dealer_logo_in=2.0, brand_logo_in=1.4,
        headline="Book your fall tune-up", tagline="Comfort you can count on",
        submitted_on="2026-10-15",
        vendor="Meta Ads", invoice_number="FB-99213", invoice_date="2026-10-02", invoice_total=900.00,
        line_items=[{"desc": "Ad spend", "amount": 900.00}],
        has_payment=False,
        claim_form_total=900.00,
        expected={"decision": "blocked_missing_doc", "payable_if_resolved": 450.0, "verdicts": {"R8": "fail"}},
    ),
    dict(
        packet_id="E", mode="claim", medium="direct_mail", width_in=6, height_in=4,
        dealer_logo_in=2.4, brand_logo_in=1.6,
        headline="Fall furnace tune-up, $89", tagline="Comfort you can count on",
        submitted_on="2026-10-05",
        vendor="PrintPost Mailers", invoice_number="7733", invoice_date="2026-09-20", invoice_total=1650.00,
        line_items=[{"desc": "Printing", "amount": 950.00}, {"desc": "Postage", "amount": 700.00}],
        payment_date="2026-09-22", payment_amount=1650.00, payment_method="card",
        claim_form_total=1800.00,
        expected={"decision": "hold_question", "payable_if_resolved": 825.0, "verdicts": {"R9": "unsure"},
                  "after_resolve_amount_source": "invoice", "after_resolve_decision": "needs_approval", "after_resolve_payable": 825.0},
    ),
    dict(
        packet_id="F", mode="claim", medium="direct_mail", width_in=6, height_in=4,
        dealer_logo_in=2.4, brand_logo_in=1.6,
        headline="New furnace installs", offer_line="Free smart thermostat with any new Northwind furnace",
        tagline="Comfort you can count on",
        submitted_on="2026-10-20",
        vendor="PrintPost Mailers", invoice_number="8841", invoice_date="2026-10-06", invoice_total=600.00,
        line_items=[{"desc": "Printing", "amount": 350.00}, {"desc": "Postage", "amount": 250.00}],
        payment_date="2026-10-08", payment_amount=600.00, payment_method="card",
        claim_form_total=600.00,
        expected={"decision": "hold_question", "payable_if_resolved": 300.0, "verdicts": {"R5": "unsure"},
                  "after_resolve_decision": "auto_file", "after_resolve_payable": 300.0},
    ),
]


def main() -> None:
    PACKETS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Rendering {len(PACKETS)} packets into {PACKETS_DIR.relative_to(REPO_ROOT)} ...")
    for spec in PACKETS:
        make_packet(spec)
    print("Done.")


if __name__ == "__main__":
    main()
