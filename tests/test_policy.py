"""Unit tests for the pure decision function. No model calls. See SPEC.md 6.5, 8.5."""
from agent.models import CheckResult, ReimbursementBreakdown
from agent.policy import decide

THRESHOLD = 500.0


def check(rule_id, verdict, fixable=True):
    return CheckResult(rule_id=rule_id, verdict=verdict, kind="measured", source={"section": "1", "quote": "x"},
                        message="msg", fixable_after_run=fixable)


def reimb(payable, amounts_resolved=True, computed=None):
    return ReimbursementBreakdown(submitted=payable * 2, excluded=[], eligible=payable * 2, rate=0.5,
                                   computed=computed if computed is not None else payable, balance_before=8050.0,
                                   payable=payable, amounts_resolved=amounts_resolved)


def test_auto_file_when_all_pass_and_under_threshold():
    checks = [check("R3", "pass"), check("R8", "pass")]
    d = decide("A", "A", "claim", checks, reimb(380.0), THRESHOLD)
    assert d.decision == "auto_file"


def test_needs_approval_when_over_threshold():
    checks = [check("R9", "pass")]
    d = decide("E", "E", "claim", checks, reimb(825.0), THRESHOLD)
    assert d.decision == "needs_approval"


def test_not_eligible_beats_everything():
    checks = [check("R7", "fail", fixable=False), check("R8", "fail", fixable=True)]
    d = decide("C", "C", "claim", checks, reimb(0.0), THRESHOLD)
    assert d.decision == "not_eligible"


def test_blocked_missing_doc_when_only_fixable_fail():
    checks = [check("R8", "fail", fixable=True)]
    d = decide("D", "D", "claim", checks, reimb(450.0), THRESHOLD)
    assert d.decision == "blocked_missing_doc"
    assert d.payable_if_resolved == 450.0


def test_hold_question_on_unsure():
    checks = [check("R5", "unsure")]
    d = decide("F", "F", "claim", checks, reimb(300.0), THRESHOLD)
    assert d.decision == "hold_question"
    assert d.payable_if_resolved == 300.0


def test_hold_question_on_amounts_unresolved_even_if_all_checks_pass():
    checks = [check("R9", "pass")]  # engine already reflects mismatch elsewhere; policy also checks the flag directly
    d = decide("E", "E", "claim", checks, reimb(825.0, amounts_resolved=False), THRESHOLD)
    assert d.decision == "hold_question"


def test_precedence_blocked_over_hold_question():
    checks = [check("R8", "fail", fixable=True), check("R5", "unsure")]
    d = decide("X", "X", "claim", checks, reimb(300.0), THRESHOLD)
    assert d.decision == "blocked_missing_doc"


def test_precedence_hold_question_over_needs_approval():
    checks = [check("R9", "unsure")]
    d = decide("X", "X", "claim", checks, reimb(825.0), THRESHOLD)
    assert d.decision == "hold_question"


def test_precheck_fix_needed_on_fail():
    checks = [check("R3", "fail")]
    d = decide("B", "B", "precheck", checks, None, THRESHOLD)
    assert d.decision == "fix_needed"


def test_precheck_ready_to_run_when_clean():
    checks = [check("R3", "pass"), check("R4", "pass")]
    d = decide("B", "B", "precheck", checks, None, THRESHOLD)
    assert d.decision == "ready_to_run"
