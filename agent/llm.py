"""Single wrapper for every model call. Structured JSON output, token/cost
accounting, record and replay. See SPEC.md section 10 "Model wrapper and
replay". Nothing outside this file should call a model provider directly.

# DECISION: recordings are keyed by an explicit logical `key` (e.g. a packet
# id) rather than a hash of raw input bytes. Synthetic assets are re-rendered
# by scripts/make_packets.py and can differ byte-for-byte between runs (font
# hinting, timestamps) while meaning the same thing, which would otherwise
# break replay. The caller picks a stable key.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel

from agent.config import get_config, repo_path
from agent.models import Usage

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


class LLMError(Exception):
    pass


def _recordings_dir() -> Path:
    return repo_path(get_config()["paths"]["recordings_dir"])


def _recording_path(task: str, key: str) -> Path:
    safe_key = key.replace("/", "_")
    return _recordings_dir() / f"{task}__{safe_key}.json"


def call(
    task: str,
    schema: type[BaseModel],
    key: str,
    text: Optional[str] = None,
    images: Optional[list[bytes]] = None,
    system: Optional[str] = None,
) -> tuple[BaseModel, Usage]:
    mode = os.environ.get("LLM_MODE", "replay")
    cfg = get_config()["model"]

    if mode == "replay":
        return _replay(task, key, schema)

    if mode in ("live", "record"):
        raw, usage = _call_live(text, images, system, cfg, mode)
        try:
            parsed = schema.model_validate(raw)
        except Exception:
            # Validate every model response against its schema and retry once
            # on failure, per SPEC.md section 6.
            raw, usage = _call_live(text, images, system, cfg, mode, retry_hint=True)
            parsed = schema.model_validate(raw)
        if mode == "record":
            _save_recording(task, key, raw, usage)
        return parsed, usage

    raise LLMError(f"unknown LLM_MODE: {mode!r}")


def _replay(task: str, key: str, schema: type[BaseModel]) -> tuple[BaseModel, Usage]:
    path = _recording_path(task, key)
    if not path.exists():
        raise LLMError(
            f"No recording for task={task!r} key={key!r} at {path}. "
            "Run with LLM_MODE=record and a real LLM_API_KEY once, or add a "
            "hand-authored fixture under data/recordings/."
        )
    payload = json.loads(path.read_text())
    parsed = schema.model_validate(payload["result"])
    u = payload["usage"]
    usage = Usage(model=u["model"], tokens_in=u["tokens_in"], tokens_out=u["tokens_out"],
                  cost_usd=u["cost_usd"], mode="replay")
    return parsed, usage


def _save_recording(task: str, key: str, raw: dict, usage: Usage) -> None:
    _recordings_dir().mkdir(parents=True, exist_ok=True)
    path = _recording_path(task, key)
    path.write_text(json.dumps({"result": raw, "usage": usage.model_dump(exclude={"mode"})}, indent=2, default=str))


def _call_live(text, images, system, cfg, mode, retry_hint: bool = False) -> tuple[dict, Usage]:
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError("LLM_MODE=live/record requires LLM_API_KEY (or ANTHROPIC_API_KEY) to be set.")

    content: list[dict] = []
    if text:
        content.append({"type": "text", "text": text})
    for img in images or []:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img).decode()},
        })
    if retry_hint:
        content.append({"type": "text", "text": "Your previous answer did not match the required JSON schema. Return ONLY valid JSON matching it, no prose."})

    resp = httpx.post(
        ANTHROPIC_API_URL,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={
            "model": cfg["name"],
            "max_tokens": cfg["max_tokens"],
            "system": system or "Return only valid JSON matching the requested schema. No prose, no markdown fences.",
            "messages": [{"role": "user", "content": content}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text_out = "".join(b["text"] for b in data["content"] if b["type"] == "text")
    raw = json.loads(text_out)
    usage_raw = data["usage"]
    tokens_in, tokens_out = usage_raw["input_tokens"], usage_raw["output_tokens"]
    cost = round(tokens_in * cfg["price_in_per_token"] + tokens_out * cfg["price_out_per_token"], 6)
    usage = Usage(model=cfg["name"], tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost, mode=mode)
    return raw, usage
