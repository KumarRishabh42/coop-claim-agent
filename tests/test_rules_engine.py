"""Unit tests on hand-written facts. No model calls, no database."""
from agent.models import AdFacts, ClaimFormFacts, InvoiceFacts, ObservedFacts, PacketManifest, PaymentFacts, Rule
from agent.rules_engine import evaluate_all, evaluate_rule, has_missing_doc_fail, has_non_fixable_fail, has_unsure


def make_manifest(medium="direct_mail", mode="claim", width=6.0, height=4.0, submitted_on="2026-10-01"):
    return PacketManifest(
        packet_id="T", mode=mode, dealer_id="summit", program_id="northwind-2026", medium=medium,
        physical_size_in={"width": width, "height": height} if width else None,
        submitted_on=submitted_on, files={"ad": "ad.png"},
    )


def rule(id, ctype, applies_to=None, kind="measured", on_fail=None, **check_params):
    return Rule(
        id=id, program_id="northwind-2026", title=id, kind=kind, applies_to=applies_to or [],
        check={"type": ctype, **check_params}, source={"section": "1", "quote": "x"}, on_fail=on_fail,
    )


LOGO_RULE = rule("R3", "logo_size", applies_to=["direct_mail"], min_width_in=1.0, min_ratio_to_dealer_logo=0.5,
                  on_fail="Enlarge the Northwind logo to at least {required_in} in.")


def test_logo_size_clear_pass():
    facts = ObservedFacts(ad=AdFacts(image_size_px=[1200, 800], brand_logo_bbox_px=[900, 40, 1220, 92],
                                      dealer_logo_bbox_px=[40, 36, 520, 120]))
    result = evaluate_rule(LOGO_RULE, facts, make_manifest(), {})
    assert result.verdict == "pass"


def test_logo_size_clear_fail():
    # dealer logo 2.4in wide -> required = max(1.0, 0.5*2.4) = 1.2in; brand at 0.7in is a clear fail.
    facts = ObservedFacts(ad=AdFacts(image_size_px=[1200, 800], brand_logo_bbox_px=[1080, 40, 1220, 92],
                                      dealer_logo_bbox_px=[40, 36, 520, 120]))
    result = evaluate_rule(LOGO_RULE, facts, make_manifest(), {})
    assert result.verdict == "fail"
    assert "1.2" in result.fix


def test_logo_size_unsure_band():
    # required = 1.2in; a measurement within 10% (0.12in) of that is "unsure", not pass/fail.
    facts = ObservedFacts(ad=AdFacts(image_size_px=[1200, 800], brand_logo_bbox_px=[970, 40, 1220, 92],
                                      dealer_logo_bbox_px=[40, 36, 520, 120]))
    result = evaluate_rule(LOGO_RULE, facts, make_manifest(), {})
    assert result.verdict == "unsure"


def test_logo_size_not_applicable_to_other_medium():
    facts = ObservedFacts(ad=AdFacts(image_size_px=[1200, 800], brand_logo_bbox_px=[900, 40, 1220, 92],
                                      dealer_logo_bbox_px=[40, 36, 520, 120]))
    result = evaluate_rule(LOGO_RULE, facts, make_manifest(medium="radio", width=None), {})
    assert result is None


def test_required_text_pass_and_fail():
    r = rule("R4", "required_text", text="Comfort you can count on")
    passing = ObservedFacts(ad=AdFacts(text_found=["Comfort you can count on"]))
    failing = ObservedFacts(ad=AdFacts(text_found=["Some other tagline"]))
    assert evaluate_rule(r, passing, make_manifest(), {}).verdict == "pass"
    assert evaluate_rule(r, failing, make_manifest(), {}).verdict == "fail"


def test_eligible_media():
    r = rule("R2", "eligible_media", allowed=["direct_mail", "radio"], excluded=["directories"])
    facts = ObservedFacts()
    assert evaluate_rule(r, facts, make_manifest(medium="direct_mail"), {}).verdict == "pass"
    assert evaluate_rule(r, facts, make_manifest(medium="directories"), {}).verdict == "fail"
    assert evaluate_rule(r, facts, make_manifest(medium="tv"), {}).verdict == "unsure"


def test_no_competing_brands():
    r = rule("R6", "no_competing_brands")
    clean = ObservedFacts(ad=AdFacts(brands_mentioned=["Northwind"]))
    dirty = ObservedFacts(ad=AdFacts(brands_mentioned=["Northwind", "HeatWave"]))
    assert evaluate_rule(r, clean, make_manifest(), {}).verdict == "pass"
    assert evaluate_rule(r, dirty, make_manifest(), {}).verdict == "fail"


def test_restricted_offer_unsure_then_pass_with_doc():
    r = rule("R5", "restricted_offer", trigger_words=["free"], requires_doc="territory_manager_approval", kind="judgment")
    facts = ObservedFacts(ad=AdFacts(offer_phrases=["Free smart thermostat"]))
    unsure = evaluate_rule(r, facts, make_manifest(), {})
    assert unsure.verdict == "unsure"
    assert unsure.ask is not None
    approved = evaluate_rule(r, facts, make_manifest(), {"territory_manager_approval": True})
    assert approved.verdict == "pass"


def test_restricted_offer_no_offer_passes():
    r = rule("R5", "restricted_offer", trigger_words=["free"], requires_doc="territory_manager_approval", kind="judgment")
    facts = ObservedFacts(ad=AdFacts(offer_phrases=[]))
    assert evaluate_rule(r, facts, make_manifest(), {}).verdict == "pass"


def test_date_window_late_claim_fails_and_is_not_fixable():
    r = rule("R7", "date_window", activity_start="2026-01-01", activity_end="2026-12-31",
             claim_within_days=60, claim_by="2026-12-15")
    facts = ObservedFacts(invoice=InvoiceFacts(date="2026-08-03"))
    result = evaluate_rule(r, facts, make_manifest(submitted_on="2026-10-20"), {})
    assert result.verdict == "fail"
    assert result.fixable_after_run is False


def test_date_window_on_time_passes():
    r = rule("R7", "date_window", activity_start="2026-01-01", activity_end="2026-12-31",
             claim_within_days=60, claim_by="2026-12-15")
    facts = ObservedFacts(invoice=InvoiceFacts(date="2026-09-14"))
    result = evaluate_rule(r, facts, make_manifest(submitted_on="2026-10-01"), {})
    assert result.verdict == "pass"


def test_required_documents_missing_is_fixable():
    r = rule("R8", "required_documents", docs=["ad", "invoice", "payment", "claim_form"], extra_by_medium={"radio": ["affidavit"]})
    result = evaluate_rule(r, ObservedFacts(), make_manifest(),
                            {"ad": True, "invoice": True, "payment": False, "claim_form": True})
    assert result.verdict == "fail"
    assert result.fixable_after_run is True
    assert "payment" in result.message


def test_required_documents_radio_needs_affidavit():
    r = rule("R8", "required_documents", docs=["ad", "invoice", "payment", "claim_form"], extra_by_medium={"radio": ["affidavit"]})
    present = {"ad": True, "invoice": True, "payment": True, "claim_form": True, "affidavit": False}
    result = evaluate_rule(r, ObservedFacts(), make_manifest(medium="radio", width=None), present)
    assert result.verdict == "fail"
    assert "affidavit" in result.message


def test_amounts_match_mismatch_is_unsure():
    r = rule("R9", "amounts_match", tolerance_usd=1.0)
    facts = ObservedFacts(claim_form=ClaimFormFacts(total_cost=1800.0), invoice=InvoiceFacts(total=1650.0),
                           payment=PaymentFacts(amount=1650.0))
    result = evaluate_rule(r, facts, make_manifest(), {})
    assert result.verdict == "unsure"


def test_amounts_match_within_tolerance_passes():
    r = rule("R9", "amounts_match", tolerance_usd=1.0)
    facts = ObservedFacts(claim_form=ClaimFormFacts(total_cost=760.0), invoice=InvoiceFacts(total=760.0),
                           payment=PaymentFacts(amount=760.5))
    result = evaluate_rule(r, facts, make_manifest(), {})
    assert result.verdict == "pass"


def test_funds_terms_is_not_evaluated():
    r = rule("R1", "funds_terms", rate=0.5, accrual_rate=0.02, excluded_categories=["agency fee"])
    assert evaluate_rule(r, ObservedFacts(), make_manifest(), {}) is None


def test_has_helpers():
    rules = [
        rule("R7", "date_window", activity_start="2026-01-01", activity_end="2026-12-31", claim_within_days=60, claim_by="2026-12-15"),
        rule("R8", "required_documents", docs=["ad", "invoice", "payment", "claim_form"], extra_by_medium={}),
    ]
    facts = ObservedFacts(invoice=InvoiceFacts(date="2026-08-03"))
    present = {"ad": True, "invoice": True, "payment": False, "claim_form": True}
    checks = evaluate_all(rules, facts, make_manifest(submitted_on="2026-10-20"), present)
    assert has_non_fixable_fail(checks) is True  # R7 late
    assert has_missing_doc_fail(checks) is True  # R8 missing payment
    assert has_unsure(checks) is False
