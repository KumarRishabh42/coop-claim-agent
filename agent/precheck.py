"""Ad image or script to observed facts. The model returns facts only — no
verdicts. See SPEC.md 8.2 and 6.3."""
from __future__ import annotations

from typing import Optional

from agent.llm import call
from agent.models import AdFacts, Usage

PRECHECK_PROMPT = """Look at this advertisement (image or script). Return only
observed facts, not judgments: the pixel bounding boxes of the brand logo and
the dealer logo (as [x0, y0, x1, y1]), the image size in pixels, every
distinct line of text found in the ad, every brand name mentioned, and any
promotional offer phrases (e.g. "free thermostat"). If this is a radio script
instead of an image, there are no bounding boxes or image size — just text,
brands and offers."""


def precheck_ad(packet_id: str, image_bytes: Optional[bytes] = None, script_text: Optional[str] = None) -> tuple[AdFacts, Usage]:
    facts, usage = call(
        task="precheck_ad",
        key=packet_id,
        schema=AdFacts,
        text=PRECHECK_PROMPT if image_bytes else (PRECHECK_PROMPT + "\n\n---SCRIPT---\n" + (script_text or "")),
        images=[image_bytes] if image_bytes else None,
    )
    return facts, usage
