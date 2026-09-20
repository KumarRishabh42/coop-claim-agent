"""Invoice, payment proof, claim form (and affidavit) to fields. See SPEC.md
8.3.

# DECISION: the spec lists per-document extraction; this PoC sends every
# present document for a packet in one model call and gets back one
# DocumentFacts object. Fewer fixtures, one audit event per packet instead of
# per file, same "model perceives, code decides" contract.
#
# # DECISION: the synthetic documents are rendered PNG cards, not PDFs, so
# there is no text layer to extract. Per SPEC.md 8.3's own fallback ("send
# the page image if the text layer is empty"), this sends the document
# images directly rather than pretending to have extracted PDF text.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from agent.llm import call
from agent.models import AffidavitFacts, ClaimFormFacts, InvoiceFacts, PaymentFacts, Usage

DOCS_PROMPT = """These images are claim documents for a co-op advertising
claim: a vendor invoice, proof of payment, a claim form, and (for radio
claims) a station affidavit — in that order, only for the documents
actually present. Extract the fields for each as JSON matching this shape:

{
  "invoice": {"vendor": "PrintPost Mailers", "number": "7711", "date": "2026-09-14", "total": 760.00,
              "line_items": [{"desc": "Printing", "amount": 460.00}, {"desc": "Postage", "amount": 300.00}]},
  "payment": {"date": "2026-09-16", "amount": 760.00, "method": "card"},
  "claim_form": {"medium": "direct_mail", "total_cost": 760.00, "requested": 380.00},
  "affidavit": {"station": "WNCR-FM", "air_dates": ["2026-08-10", "2026-08-17"]}
}

Rules:
- Dates are "YYYY-MM-DD". Dollar amounts are numbers, not strings.
- If a document type has no image among those given, set its whole section
  to null (do not invent one).
- If a field can't be read with confidence, set that field to null rather
  than guessing — null fields are meant to stop the claim and ask a human,
  not to be estimated.

Return ONLY the JSON object. No markdown code fences, no prose before or after."""


class DocumentFacts(BaseModel):
    invoice: Optional[InvoiceFacts] = None
    payment: Optional[PaymentFacts] = None
    claim_form: Optional[ClaimFormFacts] = None
    affidavit: Optional[AffidavitFacts] = None


def extract_documents(packet_id: str, images: list[bytes], mode: Optional[str] = None) -> tuple[DocumentFacts, Usage]:
    facts, usage = call(
        task="extract_documents",
        key=packet_id,
        schema=DocumentFacts,
        text=DOCS_PROMPT,
        images=images,
        mode=mode,
    )
    return facts, usage
