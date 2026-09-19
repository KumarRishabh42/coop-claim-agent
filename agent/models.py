"""Pydantic schemas shared by every module. See SPEC.md section 6."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# 6.1 Rule
# --------------------------------------------------------------------------

CHECK_TYPES = (
    "logo_size",
    "required_text",
    "eligible_media",
    "no_competing_brands",
    "restricted_offer",
    "date_window",
    "required_documents",
    "amounts_match",
)


class RuleSource(BaseModel):
    section: str
    quote: str


class RuleCheck(BaseModel):
    """Loosely typed: params vary by `type`. See CHECK_TYPES in SPEC.md 6.1."""

    model_config = ConfigDict(extra="allow")
    type: str


class Rule(BaseModel):
    id: str
    program_id: str
    title: str
    kind: Literal["measured", "judgment"]
    applies_to: list[str] = Field(default_factory=list)
    check: RuleCheck
    source: RuleSource
    on_fail: Optional[str] = None
    quote_verified: bool = True


# --------------------------------------------------------------------------
# 6.2 Packet manifest
# --------------------------------------------------------------------------


class PhysicalSize(BaseModel):
    width: float
    height: float


class PacketFiles(BaseModel):
    model_config = ConfigDict(extra="allow")
    ad: Optional[str] = None
    script: Optional[str] = None
    claim_form: Optional[str] = None
    invoice: Optional[str] = None
    payment: Optional[str] = None
    affidavit: Optional[str] = None


class PacketManifest(BaseModel):
    packet_id: str
    mode: Literal["precheck", "claim"]
    dealer_id: str
    program_id: str
    medium: str
    physical_size_in: Optional[PhysicalSize] = None
    submitted_on: str
    files: PacketFiles


# --------------------------------------------------------------------------
# 6.3 Observed facts (model output)
# --------------------------------------------------------------------------


class AdFacts(BaseModel):
    brand_logo_bbox_px: Optional[list[float]] = None
    dealer_logo_bbox_px: Optional[list[float]] = None
    image_size_px: Optional[list[float]] = None
    text_found: list[str] = Field(default_factory=list)
    brands_mentioned: list[str] = Field(default_factory=list)
    offer_phrases: list[str] = Field(default_factory=list)


class LineItem(BaseModel):
    desc: str
    amount: float


class InvoiceFacts(BaseModel):
    vendor: Optional[str] = None
    number: Optional[str] = None
    date: Optional[str] = None
    total: Optional[float] = None
    line_items: list[LineItem] = Field(default_factory=list)


class PaymentFacts(BaseModel):
    date: Optional[str] = None
    amount: Optional[float] = None
    method: Optional[str] = None


class ClaimFormFacts(BaseModel):
    medium: Optional[str] = None
    total_cost: Optional[float] = None
    requested: Optional[float] = None


class AffidavitFacts(BaseModel):
    station: Optional[str] = None
    air_dates: list[str] = Field(default_factory=list)


class ObservedFacts(BaseModel):
    ad: Optional[AdFacts] = None
    invoice: Optional[InvoiceFacts] = None
    payment: Optional[PaymentFacts] = None
    claim_form: Optional[ClaimFormFacts] = None
    affidavit: Optional[AffidavitFacts] = None


# --------------------------------------------------------------------------
# 6.4 Check result
# --------------------------------------------------------------------------

Verdict = Literal["pass", "fail", "unsure", "not_applicable"]


class CheckResult(BaseModel):
    rule_id: str
    verdict: Verdict
    kind: Literal["measured", "judgment"]
    evidence: dict[str, Any] = Field(default_factory=dict)
    source: RuleSource
    message: str
    fix: Optional[str] = None
    ask: Optional[str] = None
    fixable_after_run: bool = True


# --------------------------------------------------------------------------
# 6.4 Reimbursement breakdown
# --------------------------------------------------------------------------


class ReimbursementBreakdown(BaseModel):
    submitted: float
    excluded: list[LineItem] = Field(default_factory=list)
    eligible: float
    rate: float
    computed: float
    balance_before: float
    payable: float
    amounts_resolved: bool = True


# --------------------------------------------------------------------------
# 6.5 Claim decision
# --------------------------------------------------------------------------

Decision = Literal[
    "auto_file",
    "needs_approval",
    "hold_question",
    "blocked_missing_doc",
    "fix_needed",
    "not_eligible",
]


class Question(BaseModel):
    id: str
    rule_id: str
    text: str
    answers: list[str] = Field(default_factory=list)


class ClaimDecision(BaseModel):
    claim_id: str
    packet_id: str
    decision: Decision
    reasons: list[str] = Field(default_factory=list)
    reimbursement: Optional[ReimbursementBreakdown] = None
    payable_if_resolved: Optional[float] = None
    questions: list[Question] = Field(default_factory=list)
    cost_usd: float = 0.0


# --------------------------------------------------------------------------
# 6.6 Audit event
# --------------------------------------------------------------------------


class AuditEvent(BaseModel):
    id: Optional[int] = None
    ts: str
    claim_id: Optional[str] = None
    step: str
    actor: str
    input_ref: Optional[str] = None
    output_ref: Optional[str] = None
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    note: Optional[str] = None


# --------------------------------------------------------------------------
# Model call usage (agent/llm.py)
# --------------------------------------------------------------------------


class Usage(BaseModel):
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    mode: Literal["live", "record", "replay"]
