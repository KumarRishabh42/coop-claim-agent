"""Loads config.yaml once. Nothing else in the codebase should hardcode a
model name, price or threshold — read it from here instead."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Serverless hosts (Vercel, and similar) deploy the repo as a read-only
# bundle — only /tmp is writable, and it isn't guaranteed to persist between
# invocations. Detected via the VERCEL env var Vercel sets automatically.
IS_SERVERLESS = bool(os.environ.get("VERCEL"))


@lru_cache
def get_config() -> dict:
    with open(REPO_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def repo_path(*parts: str) -> Path:
    """Read-only, bundled repo content: guides, the six canonical packets,
    templates. Always resolves inside the deployed source tree."""
    return REPO_ROOT.joinpath(*parts)


def writable_path(*parts: str) -> Path:
    """Anything the app creates at runtime: the database, uploaded guides,
    uploaded claim packets. /tmp on a serverless host, the repo tree locally
    (where it's just as writable as anywhere else)."""
    base = Path("/tmp") if IS_SERVERLESS else REPO_ROOT
    return base.joinpath(*parts)


def packet_dir_for(packet_id: str) -> Path:
    """Uploaded packets live under writable_path(); the six canonical demo
    packets (A-F) are bundled read-only in the repo. Check both."""
    wp = writable_path("data", "packets", packet_id)
    if wp.exists():
        return wp
    return repo_path("data", "packets", packet_id)
