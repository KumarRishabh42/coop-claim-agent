"""Unit tests on hand-written facts. No model calls. See SPEC.md 8.4."""
from agent.models import ClaimFormFacts, InvoiceFacts, LineItem, ObservedFacts, PaymentFacts, Rule
from agent.reimbursement import amounts_resolved, compute_reimbursement

R1 = Rule(
    id="R1", program_id="northwind-2026", title="Funds", kind="measured",
    check={"type": "funds_terms", "rate": 0.5, "accrual_rate": 0.02, "excluded_categories": ["agency fee", "management fee"]},
    source={"section": "1", "quote": "x"},
)


def test_basic_reimbursement():
    facts = ObservedFacts(
        claim_form=ClaimFormFacts(total_cost=760.0), invoice=InvoiceFacts(total=760.0, line_items=[]),
        payment=PaymentFacts(amount=760.0),
    )
    r = compute_reimbursement(facts, R1, balance_before=8050.0, tolerance_usd=1.0, eligible_at_all=True)
    assert r.eligible == 760.0
    assert r.computed == 380.0
    assert r.payable == 380.0
    assert r.amounts_resolved is True


def test_excludes_agency_fee_line_items():
    facts = ObservedFacts(
        claim_form=ClaimFormFacts(total_cost=1000.0),
        invoice=InvoiceFacts(total=1000.0, line_items=[LineItem(desc="Printing", amount=800.0), LineItem(desc="Agency fee", amount=200.0)]),
        payment=PaymentFacts(amount=1000.0),
    )
    r = compute_reimbursement(facts, R1, balance_before=8050.0, tolerance_usd=1.0, eligible_at_all=True)
    assert r.eligible == 800.0
    assert r.computed == 400.0


def test_balance_cap():
    facts = ObservedFacts(claim_form=ClaimFormFacts(total_cost=20000.0), invoice=InvoiceFacts(total=20000.0), payment=PaymentFacts(amount=20000.0))
    r = compute_reimbursement(facts, R1, balance_before=500.0, tolerance_usd=1.0, eligible_at_all=True)
    assert r.computed == 10000.0
    assert r.payable == 500.0


def test_not_eligible_zeroes_payable():
    facts = ObservedFacts(claim_form=ClaimFormFacts(total_cost=4800.0), invoice=InvoiceFacts(total=4800.0), payment=PaymentFacts(amount=4800.0))
    r = compute_reimbursement(facts, R1, balance_before=8050.0, tolerance_usd=1.0, eligible_at_all=False)
    assert r.payable == 0.0
    assert r.computed == 2400.0  # still shown, informational


def test_amounts_resolved_true_within_tolerance():
    facts = ObservedFacts(claim_form=ClaimFormFacts(total_cost=760.0), invoice=InvoiceFacts(total=760.5), payment=PaymentFacts(amount=760.0))
    assert amounts_resolved(facts, tolerance_usd=1.0) is True


def test_amounts_resolved_false_beyond_tolerance():
    facts = ObservedFacts(claim_form=ClaimFormFacts(total_cost=1800.0), invoice=InvoiceFacts(total=1650.0), payment=PaymentFacts(amount=1650.0))
    assert amounts_resolved(facts, tolerance_usd=1.0) is False


def test_amounts_resolved_true_when_payment_missing():
    # Missing proof of payment is a required-documents problem (R8), not an
    # amounts-match problem (R9): compare only the values on file.
    facts = ObservedFacts(claim_form=ClaimFormFacts(total_cost=900.0), invoice=InvoiceFacts(total=900.0), payment=None)
    assert amounts_resolved(facts, tolerance_usd=1.0) is True


def test_reimbursement_prefers_invoice_total_over_claim_form():
    facts = ObservedFacts(claim_form=ClaimFormFacts(total_cost=1800.0), invoice=InvoiceFacts(total=1650.0), payment=PaymentFacts(amount=1650.0))
    r = compute_reimbursement(facts, R1, balance_before=8050.0, tolerance_usd=1.0, eligible_at_all=True)
    assert r.eligible == 1650.0
    assert r.computed == 825.0
