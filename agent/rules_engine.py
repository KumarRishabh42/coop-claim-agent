"""Applies rules to observed facts. Pure code, no model calls. Returns one
CheckResult per applicable rule. See SPEC.md sections 6.1, 6.4, 8.2, 8.4."""
from __future__ import annotations

from datetime import date, datetime

from agent.config import get_config
from agent.models import AdFacts, CheckResult, ObservedFacts, PacketManifest, Rule

# Rules whose failure is fixable after the fact (paperwork, not creative that
# already ran). Everything else, when it fails in claim mode, is not_eligible.
FIXABLE_CHECK_TYPES = {"required_documents"}

# Pre-check mode only has the ad/script (SPEC.md 8.2) — no invoice, payment or
# claim form yet, so document/date/amount rules don't apply and would just
# read as false failures.
PRECHECK_CHECK_TYPES = {"eligible_media", "logo_size", "required_text", "no_competing_brands", "restricted_offer"}

COMPETITOR_BRANDS = {"heatwave", "aircore", "thermorite", "glacierline"}


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def _text_blob(ad: AdFacts | None) -> str:
    if ad is None:
        return ""
    return " ".join(ad.text_found).lower()


def evaluate_rule(
    rule: Rule,
    facts: ObservedFacts,
    manifest: PacketManifest,
    documents_present: dict[str, bool],
) -> CheckResult | None:
    """Returns None if the rule does not apply to this packet's medium."""
    if rule.applies_to and manifest.medium not in rule.applies_to:
        return None

    ctype = rule.check.type
    handler = _HANDLERS.get(ctype)
    if handler is None:
        return None
    return handler(rule, facts, manifest, documents_present)


def _base(rule: Rule, verdict: str, message: str, evidence: dict | None = None,
          fix: str | None = None, ask: str | None = None) -> CheckResult:
    return CheckResult(
        rule_id=rule.id,
        verdict=verdict,
        kind=rule.kind,
        evidence=evidence or {},
        source=rule.source,
        message=message,
        fix=fix,
        ask=ask,
        fixable_after_run=rule.check.type in FIXABLE_CHECK_TYPES,
    )


def _check_logo_size(rule, facts, manifest, documents_present) -> CheckResult:
    ad = facts.ad
    if ad is None or not ad.brand_logo_bbox_px or not ad.dealer_logo_bbox_px or not ad.image_size_px:
        return _base(rule, "unsure", "Could not measure the logos in this ad.",
                     ask="Upload a clearer copy of the ad so the logo sizes can be measured.")
    if manifest.physical_size_in is None:
        return _base(rule, "unsure", "Ad's physical size is unknown, so logo inches can't be computed.")

    img_w_px = ad.image_size_px[0]
    phys_w_in = manifest.physical_size_in.width
    brand_w_px = ad.brand_logo_bbox_px[2] - ad.brand_logo_bbox_px[0]
    dealer_w_px = ad.dealer_logo_bbox_px[2] - ad.dealer_logo_bbox_px[0]
    brand_in = round(brand_w_px / img_w_px * phys_w_in, 2)
    dealer_in = round(dealer_w_px / img_w_px * phys_w_in, 2)

    min_width_in = rule.check.min_width_in
    min_ratio = rule.check.min_ratio_to_dealer_logo
    required_in = round(max(min_width_in, min_ratio * dealer_in), 2)

    band = get_config()["policy"]["unsure_band_fraction"]
    evidence = {"brand_logo_in": brand_in, "dealer_logo_in": dealer_in, "required_in": required_in}
    diff = brand_in - required_in
    fix = rule.on_fail.format(required_in=required_in) if rule.on_fail else None

    if abs(diff) <= band * required_in:
        return _base(rule, "unsure", f"Northwind logo is {brand_in} in wide, close to the {required_in} in minimum. Double check by hand.",
                     evidence, fix=fix, ask="Confirm the measured logo width, or upload a higher-resolution ad.")
    if diff >= 0:
        return _base(rule, "pass", f"Northwind logo is {brand_in} in wide, at or above the {required_in} in minimum.", evidence)
    return _base(rule, "fail", f"Northwind logo is {brand_in} in wide. It needs at least {required_in} in.", evidence, fix=fix)


def _check_required_text(rule, facts, manifest, documents_present) -> CheckResult:
    text = rule.check.text.lower()
    blob = _text_blob(facts.ad)
    if text in blob:
        return _base(rule, "pass", f'"{rule.check.text}" appears in the ad.')
    fix = rule.on_fail.format(text=rule.check.text) if rule.on_fail else f'Add "{rule.check.text}" to the ad.'
    return _base(rule, "fail", f'"{rule.check.text}" was not found in the ad or script.', fix=fix)


def _check_eligible_media(rule, facts, manifest, documents_present) -> CheckResult:
    medium = manifest.medium
    allowed = set(rule.check.allowed)
    excluded = set(rule.check.excluded)
    if medium in excluded:
        return _base(rule, "fail", f'"{medium}" is not an eligible medium under this program.')
    if medium in allowed:
        return _base(rule, "pass", f'"{medium}" is an eligible medium.')
    return _base(rule, "unsure", f'"{medium}" is not listed as eligible or excluded. Ask the program manager.')


def _check_no_competing_brands(rule, facts, manifest, documents_present) -> CheckResult:
    ad = facts.ad
    mentioned = {b.lower() for b in (ad.brands_mentioned if ad else [])}
    competitors = mentioned & COMPETITOR_BRANDS
    if competitors:
        names = ", ".join(sorted(competitors))
        return _base(rule, "fail", f"Competing brand(s) found in the ad: {names}.", {"competitors": sorted(competitors)})
    return _base(rule, "pass", "No competing heating or cooling brand found in the ad.")


def _check_restricted_offer(rule, facts, manifest, documents_present) -> CheckResult:
    ad = facts.ad
    phrases = [p.lower() for p in (ad.offer_phrases if ad else [])]
    triggers = [t.lower() for t in rule.check.trigger_words]
    hit = next((p for p in phrases for t in triggers if t in p), None)
    if hit is None:
        return _base(rule, "pass", "No restricted offer language found.")
    doc_key = rule.check.requires_doc
    if documents_present.get(doc_key):
        return _base(rule, "pass", f'Offer "{hit}" is backed by the required approval document.')
    return _base(
        rule, "unsure", f'Offer "{hit}" requires written territory manager approval, which is not in this packet.',
        ask=f'Upload the territory manager\'s written approval for the "{hit}" offer.',
    )


def _check_date_window(rule, facts, manifest, documents_present) -> CheckResult:
    invoice_date = _parse_date(facts.invoice.date) if facts.invoice else None
    submitted = _parse_date(manifest.submitted_on)
    if invoice_date is None:
        return _base(rule, "unsure", "No invoice date on file, so the claim window can't be checked.",
                     ask="Upload the vendor invoice so the claim date can be verified.")

    # A real guide doesn't always state every one of these (e.g. no hard
    # calendar deadline, just a rolling window) — a field the model omitted
    # or left null is "no constraint", not a crash.
    activity_start = _parse_date(getattr(rule.check, "activity_start", None))
    activity_end = _parse_date(getattr(rule.check, "activity_end", None))
    claim_by = _parse_date(getattr(rule.check, "claim_by", None))
    within_days = getattr(rule.check, "claim_within_days", None)

    if activity_start and activity_end and not (activity_start <= invoice_date <= activity_end):
        return _base(rule, "fail", f"Invoice date {invoice_date} is outside the {activity_start}–{activity_end} program window.",
                     {"invoice_date": str(invoice_date)})

    days_elapsed = (submitted - invoice_date).days if submitted else None
    if days_elapsed is not None and within_days is not None and days_elapsed > within_days:
        return _base(rule, "fail", f"Claim was submitted {days_elapsed} days after the invoice date. The limit is {within_days} days.",
                     {"days_elapsed": days_elapsed, "limit_days": within_days})
    if submitted and claim_by and submitted > claim_by:
        return _base(rule, "fail", f"Claim was submitted on {submitted}, after the {claim_by} program deadline.",
                     {"submitted": str(submitted), "claim_by": str(claim_by)})
    return _base(rule, "pass", "Invoice and claim dates are within the program window.",
                 {"days_elapsed": days_elapsed})


def _check_required_documents(rule, facts, manifest, documents_present) -> CheckResult:
    required = list(rule.check.docs)
    extra_by_medium = rule.check.extra_by_medium if hasattr(rule.check, "extra_by_medium") else {}
    required += extra_by_medium.get(manifest.medium, [])
    missing = [d for d in required if not documents_present.get(d)]
    if missing:
        return _base(rule, "fail", f"Missing document(s): {', '.join(missing)}.", {"missing": missing},
                     fix=f"Upload: {', '.join(missing)}.")
    return _base(rule, "pass", "All required documents are present.")


def _check_amounts_match(rule, facts, manifest, documents_present) -> CheckResult:
    tol = rule.check.tolerance_usd
    cf = facts.claim_form.total_cost if facts.claim_form else None
    inv = facts.invoice.total if facts.invoice else None
    pay = facts.payment.amount if facts.payment else None
    values = {"claim_form": cf, "invoice": inv, "payment": pay}
    present = {k: v for k, v in values.items() if v is not None}
    if len(present) < 2:
        return _base(rule, "pass", "Not enough amounts on file to compare yet.", values)

    mismatches = []
    keys = list(present.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = present[keys[i]], present[keys[j]]
            if abs(a - b) > tol:
                mismatches.append(f"{keys[i]} ${a:,.2f} vs {keys[j]} ${b:,.2f}")

    if mismatches:
        return _base(rule, "unsure", "Amounts do not agree: " + "; ".join(mismatches) + ".", values,
                     ask="Which amount is correct: the claim form, the invoice, or the payment record?")
    return _base(rule, "pass", "Claim form, invoice and payment amounts agree.", values)


_HANDLERS = {
    "logo_size": _check_logo_size,
    "required_text": _check_required_text,
    "eligible_media": _check_eligible_media,
    "no_competing_brands": _check_no_competing_brands,
    "restricted_offer": _check_restricted_offer,
    "date_window": _check_date_window,
    "required_documents": _check_required_documents,
    "amounts_match": _check_amounts_match,
}


def evaluate_all(
    rules: list[Rule], facts: ObservedFacts, manifest: PacketManifest, documents_present: dict[str, bool],
) -> list[CheckResult]:
    if manifest.mode == "precheck":
        rules = [r for r in rules if r.check.type in PRECHECK_CHECK_TYPES]
    results = []
    for rule in rules:
        result = evaluate_rule(rule, facts, manifest, documents_present)
        if result is not None:
            results.append(result)
    return results


def has_non_fixable_fail(checks: list[CheckResult]) -> bool:
    return any(c.verdict == "fail" and not c.fixable_after_run for c in checks)


def has_missing_doc_fail(checks: list[CheckResult]) -> bool:
    return any(c.verdict == "fail" and c.fixable_after_run for c in checks)


def has_unsure(checks: list[CheckResult]) -> bool:
    return any(c.verdict == "unsure" for c in checks)
