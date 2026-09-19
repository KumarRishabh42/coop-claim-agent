"""Eligible cost, exclusions, rate, balance cap. Pure code. See SPEC.md 8.4."""
from __future__ import annotations

from agent.models import LineItem, ObservedFacts, ReimbursementBreakdown, Rule


def _excluded_line_items(invoice_line_items: list[LineItem], excluded_terms: list[str]) -> list[LineItem]:
    terms = [t.lower() for t in excluded_terms]
    return [li for li in invoice_line_items if any(t in li.desc.lower() for t in terms)]


def amounts_resolved(facts: ObservedFacts, tolerance_usd: float) -> bool:
    cf = facts.claim_form.total_cost if facts.claim_form else None
    inv = facts.invoice.total if facts.invoice else None
    pay = facts.payment.amount if facts.payment else None
    present = {k: v for k, v in {"claim_form": cf, "invoice": inv, "payment": pay}.items() if v is not None}
    if len(present) < 2:
        return True
    keys = list(present.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if abs(present[keys[i]] - present[keys[j]]) > tolerance_usd:
                return False
    return True


def compute_reimbursement(
    facts: ObservedFacts,
    r1_rule: Rule,
    balance_before: float,
    tolerance_usd: float,
    eligible_at_all: bool,
) -> ReimbursementBreakdown:
    cf_total = facts.claim_form.total_cost if facts.claim_form and facts.claim_form.total_cost is not None else 0.0
    invoice = facts.invoice
    basis = invoice.total if invoice and invoice.total is not None else cf_total

    excluded_terms = r1_rule.check.excluded_categories if hasattr(r1_rule.check, "excluded_categories") else []
    excluded_items = _excluded_line_items(invoice.line_items, excluded_terms) if invoice else []
    excluded_amount = sum(li.amount for li in excluded_items)

    eligible = max(round(basis - excluded_amount, 2), 0.0)
    rate = r1_rule.check.rate
    computed = round(eligible * rate, 2)

    payable = 0.0 if not eligible_at_all else round(min(computed, balance_before), 2)

    return ReimbursementBreakdown(
        submitted=cf_total,
        excluded=excluded_items,
        eligible=eligible,
        rate=rate,
        computed=computed,
        balance_before=balance_before,
        payable=payable,
        amounts_resolved=amounts_resolved(facts, tolerance_usd),
    )
