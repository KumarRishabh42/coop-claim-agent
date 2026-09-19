"""Turns verdicts and amounts into one decision. Pure code, unit-tested
without any model calls. See SPEC.md 6.5 and 8.5."""
from __future__ import annotations

from agent.models import CheckResult, ClaimDecision, Question, ReimbursementBreakdown
from agent.rules_engine import has_missing_doc_fail, has_non_fixable_fail, has_unsure


def _reasons(checks: list[CheckResult]) -> list[str]:
    return [f"{c.rule_id}: {c.verdict}" for c in checks if c.verdict in ("fail", "unsure")]


def _questions(checks: list[CheckResult]) -> list[Question]:
    out = []
    for i, c in enumerate(checks):
        if c.verdict == "unsure" and c.ask:
            out.append(Question(id=f"q{i+1}", rule_id=c.rule_id, text=c.ask, answers=["answer", "upload_document"]))
    return out


def decide_precheck(checks: list[CheckResult]) -> tuple[str, list[str]]:
    if any(c.verdict == "fail" for c in checks):
        return "fix_needed", _reasons(checks)
    return "ready_to_run", _reasons(checks)


def decide_claim(
    checks: list[CheckResult],
    reimbursement: ReimbursementBreakdown | None,
    threshold_usd: float,
) -> tuple[str, list[str]]:
    """Precedence: not_eligible, blocked_missing_doc, hold_question,
    needs_approval, auto_file. See SPEC.md 6.5."""
    reasons = _reasons(checks)

    if has_non_fixable_fail(checks):
        return "not_eligible", reasons
    if has_missing_doc_fail(checks):
        return "blocked_missing_doc", reasons
    amounts_unresolved = reimbursement is not None and not reimbursement.amounts_resolved
    if has_unsure(checks) or amounts_unresolved:
        return "hold_question", reasons
    payable = reimbursement.payable if reimbursement else 0.0
    if payable > threshold_usd:
        return "needs_approval", reasons
    return "auto_file", reasons


def decide(
    claim_id: str,
    packet_id: str,
    mode: str,
    checks: list[CheckResult],
    reimbursement: ReimbursementBreakdown | None,
    threshold_usd: float,
    cost_usd: float = 0.0,
) -> ClaimDecision:
    if mode == "precheck":
        decision, reasons = decide_precheck(checks)
        return ClaimDecision(
            claim_id=claim_id, packet_id=packet_id, decision=decision, reasons=reasons,
            reimbursement=None, payable_if_resolved=None, questions=_questions(checks), cost_usd=cost_usd,
        )

    decision, reasons = decide_claim(checks, reimbursement, threshold_usd)
    payable_if_resolved = None
    if decision in ("hold_question", "blocked_missing_doc") and reimbursement:
        payable_if_resolved = reimbursement.payable

    return ClaimDecision(
        claim_id=claim_id, packet_id=packet_id, decision=decision, reasons=reasons,
        reimbursement=reimbursement, payable_if_resolved=payable_if_resolved,
        questions=_questions(checks) if decision == "hold_question" else [],
        cost_usd=cost_usd,
    )
