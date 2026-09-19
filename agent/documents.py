"""Invoice, payment proof, claim form (and affidavit) to fields. See SPEC.md
8.3.

# DECISION: the spec lists per-document extraction; this PoC sends every
# present document for a packet in one model call and gets back one
# DocumentFacts object. Fewer fixtures, one audit event per packet instead of
# per file, same "model perceives, code decides" contract.
"""
from __future__ import annotations

from pydantic import BaseModel

from agent.llm import call
from agent.models import AffidavitFacts, ClaimFormFacts, InvoiceFacts, PaymentFacts, Usage

DOCS_PROMPT = """Read these claim documents (invoice, proof of payment, claim
form, and station affidavit if present). Extract the fields for each as
structured data. If a field can't be read with confidence, return null for
it rather than guessing — null fields are meant to stop the claim and ask a
human, not to be estimated. If a document type is not present at all, return
null for that whole section."""


class DocumentFacts(BaseModel):
    invoice: InvoiceFacts | None = None
    payment: PaymentFacts | None = None
    claim_form: ClaimFormFacts | None = None
    affidavit: AffidavitFacts | None = None


def extract_documents(packet_id: str, doc_text_blob: str) -> tuple[DocumentFacts, Usage]:
    facts, usage = call(
        task="extract_documents",
        key=packet_id,
        schema=DocumentFacts,
        text=DOCS_PROMPT + "\n\n---DOCUMENTS---\n" + doc_text_blob,
    )
    return facts, usage
