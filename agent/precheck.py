"""Ad image or script to observed facts. The model returns facts only — no
verdicts. See SPEC.md 8.2 and 6.3."""
from __future__ import annotations

from typing import Optional

from agent.llm import call
from agent.models import AdFacts, Usage

PRECHECK_PROMPT = """Look at this advertisement (image or script) for {brand_name},
run by one of its local dealers. Return only observed facts, not judgments,
as JSON matching this exact shape:

{{
  "image_size_px": [1200, 800],
  "brand_logo_bbox_px": [900, 40, 1160, 92],
  "dealer_logo_bbox_px": [40, 36, 548, 120],
  "text_found": ["Fall furnace tune-up, $89", "Comfort you can count on"],
  "brands_mentioned": ["{brand_name}"],
  "offer_phrases": ["Free smart thermostat"]
}}

Rules:
- "brand_logo_bbox_px" is the logo/wordmark for {brand_name} SPECIFICALLY —
  the manufacturer whose co-op program this ad is claiming under. It is
  usually the smaller, secondary logo, often near a tagline.
- "dealer_logo_bbox_px" is the LOCAL DEALER's own business logo/name (a
  different, unrelated business name) — usually the larger, primary logo.
- Bounding boxes are [x0, y0, x1, y1] in pixels, (0,0) at the top-left.
- "text_found" lists every distinct line of text in the ad, verbatim.
- "brands_mentioned" lists every brand/company name that appears, including
  {brand_name} and the dealer's own name if it reads as a brand.
- "offer_phrases" lists promotional offer phrases (e.g. "free thermostat"),
  or [] if there are none.
- If this is a script instead of an image, there is no image and no
  bounding boxes: use null for "image_size_px", "brand_logo_bbox_px" and
  "dealer_logo_bbox_px", and fill the text/brand/offer fields from the script.

Return ONLY the JSON object. No markdown code fences, no prose before or after."""


def precheck_ad(packet_id: str, image_bytes: Optional[bytes] = None, script_text: Optional[str] = None,
                 brand_name: str = "Northwind Comfort") -> tuple[AdFacts, Usage]:
    prompt = PRECHECK_PROMPT.format(brand_name=brand_name)
    facts, usage = call(
        task="precheck_ad",
        key=packet_id,
        schema=AdFacts,
        text=prompt if image_bytes else (prompt + "\n\n---SCRIPT---\n" + (script_text or "")),
        images=[image_bytes] if image_bytes else None,
    )
    return facts, usage
