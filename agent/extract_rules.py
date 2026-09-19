"""Guide text to rules JSON with citations. See SPEC.md 8.1."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from agent.llm import call
from agent.models import Rule, RuleCheck, RuleSource, Usage

EXTRACT_PROMPT = """You are reading a manufacturer co-op advertising program guide.

Return one rule per numbered guide section that constrains what a dealer may
claim. For "check.type" use only one of: logo_size, required_text,
eligible_media, no_competing_brands, restricted_offer, date_window,
required_documents, amounts_match, funds_terms.

Every rule needs a source.section (the guide's section number as a string)
and a source.quote of 25 words or fewer copied VERBATIM from the guide —
citations are checked programmatically against the guide text, so do not
paraphrase.

---GUIDE---
{guide_text}
"""


class ExtractedRule(BaseModel):
    id: str
    title: str
    kind: str
    applies_to: list[str] = []
    check: RuleCheck
    source: RuleSource
    on_fail: Optional[str] = None


class ExtractedRules(BaseModel):
    rules: list[ExtractedRule]


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_rules(program_id: str, guide_text: str) -> tuple[list[Rule], Usage]:
    extracted, usage = call(
        task="extract_rules",
        key=program_id,
        schema=ExtractedRules,
        text=EXTRACT_PROMPT.format(guide_text=guide_text),
    )
    normalized_guide = _normalize(guide_text)

    rules = []
    for r in extracted.rules:
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
