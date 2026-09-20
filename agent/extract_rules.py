"""Guide text to rules JSON with citations. See SPEC.md 8.1."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from agent.llm import call
from agent.models import Rule, RuleCheck, RuleSource, Usage

EXTRACT_PROMPT = """You are reading a manufacturer co-op advertising program guide.

Return one rule per numbered guide section that constrains what a dealer may
claim, as JSON matching this exact shape (one entry per rule):

{{
  "rules": [
    {{
      "id": "R3",
      "title": "Brand logo minimum size",
      "kind": "measured",
      "applies_to": ["direct_mail", "newspaper", "paid_social"],
      "check": {{"type": "logo_size", "min_width_in": 1.0, "min_ratio_to_dealer_logo": 0.5}},
      "source": {{"section": "3", "quote": "at least 1 inch wide and at least half the width of the dealer's logo"}},
      "on_fail": "Enlarge the brand logo to at least {{required_in}} in."
    }}
  ]
}}

Rules:
- "id" is a short id like "R1", "R2", in guide order.
- "kind" is "measured" (code can decide it from facts alone) or "judgment"
  (needs a model's subjective read, e.g. whether an offer is "restricted").
- "applies_to" is a list of media the rule applies to, or [] for all media.
- "source.section" is the guide's section number as a string.
- "source.quote" is 25 words or fewer copied VERBATIM, character-for-character,
  from the guide — citations are checked programmatically against the guide
  text, so a shortened, reworded, or trimmed quote will fail verification.
  Copy a complete clause or sentence exactly as written; when in doubt, quote
  more rather than paraphrasing to fit fewer words.
- "on_fail" is a short, specific instruction for how a dealer would fix a
  failure of this rule, or null if fixing isn't a single clear action. For a
  "logo_size" rule specifically, "on_fail" MUST contain the literal text
  "{{required_in}}" as a placeholder (e.g. "Enlarge the logo to at least
  {{required_in}} in.") — code fills that placeholder in with the actual
  number for a given ad, so it must be present verbatim, not replaced with
  an example number.

"check" must be EXACTLY one of the 9 full objects below, with "type" INSIDE
"check" every time as shown (a downstream program reads these fields by
exact name — inventing different names or nesting, even reasonable
synonyms, will break it). Use only the one(s) that fit what the guide
actually says for each rule; skip a shape if the guide has nothing like it.

- {{"type": "funds_terms", "rate": 0.5, "accrual_rate": 0.02, "excluded_categories": ["agency fee", "management fee"]}}
  (rate and accrual_rate are fractions like 0.5 for 50%, not 50; excluded_categories lists cost types the guide says are NOT reimbursed)
- {{"type": "eligible_media", "allowed": ["direct_mail", "newspaper", "radio", "paid_social", "paid_search"], "excluded": ["directories"]}}
- {{"type": "logo_size", "min_width_in": 1.0, "min_ratio_to_dealer_logo": 0.5}}
- {{"type": "required_text", "text": "Comfort you can count on"}}
- {{"type": "restricted_offer", "trigger_words": ["free"], "requires_doc": "territory_manager_approval"}}
- {{"type": "no_competing_brands"}}  (no other parameters)
- {{"type": "date_window", "activity_start": "2026-01-01", "activity_end": "2026-12-31", "claim_within_days": 60, "claim_by": "2026-12-15"}}
- {{"type": "required_documents", "docs": ["ad", "invoice", "payment", "claim_form"], "extra_by_medium": {{"radio": ["affidavit"]}}}}
  (docs uses exactly these names: "ad" (or script), "invoice", "payment", "claim_form"; extra_by_medium maps a medium name to any additional docs it needs, or {{}} if none)
- {{"type": "amounts_match", "tolerance_usd": 1.0}}

Return ONLY the JSON object. No markdown code fences, no prose before or after.

---GUIDE---
{guide_text}
"""


class ExtractedRule(BaseModel):
    id: str
    title: str
    kind: str
    applies_to: list[str] = []
    # Optional, not RuleCheck: a real guide has rules outside the 9 shapes
    # this prototype can decide (dealer tiers, spend caps, MDF vs co-op...).
    # The model is told to omit those; this also tolerates it sending null
    # instead, rather than crashing the whole extraction over one section.
    check: Optional[RuleCheck] = None
    source: RuleSource
    on_fail: Optional[str] = None


class ExtractedRules(BaseModel):
    rules: list[ExtractedRule]


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_rules(program_id: str, guide_text: str, mode: Optional[str] = None) -> tuple[list[Rule], Usage]:
    extracted, usage = call(
        task="extract_rules",
        key=program_id,
        schema=ExtractedRules,
        text=EXTRACT_PROMPT.format(guide_text=guide_text),
        mode=mode,
    )
    normalized_guide = _normalize(guide_text)

    rules = []
    for r in extracted.rules:
        if r.check is None:
            continue  # doesn't fit any check type this prototype can decide
        verified = _normalize(r.source.quote) in normalized_guide
        rules.append(
            Rule(
                id=r.id,
                program_id=program_id,
                title=r.title,
                kind=r.kind,  # type: ignore[arg-type]
                applies_to=r.applies_to,
                check=r.check,
                source=r.source,
                on_fail=r.on_fail,
                quote_verified=verified,
            )
        )
    return rules, usage
