"""Loads config.yaml once. Nothing else in the codebase should hardcode a
model name, price or threshold — read it from here instead."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


@lru_cache
def get_config() -> dict:
    with open(REPO_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def repo_path(*parts: str) -> Path:
    return REPO_ROOT.joinpath(*parts)
