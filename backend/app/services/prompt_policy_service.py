"""Versioned, file-backed prompt policies for small control-plane agents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class PromptPolicy:
    name: str
    version: str
    content: str
    sha256: str
    source_path: str


_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"


@lru_cache(maxsize=16)
def _load_cached(name: str, mtime_ns: int) -> PromptPolicy:
    path = _PROMPTS_DIR / f"{name}.md"
    content = path.read_text(encoding="utf-8").strip()
    match = re.search(r"^version:\s*[\"']?([^\n\"']+)", content, re.MULTILINE)
    version = match.group(1).strip() if match else "unversioned"
    return PromptPolicy(
        name=name,
        version=version,
        content=content,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        source_path=str(path),
    )


def load_prompt_policy(name: str) -> PromptPolicy:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt_policy_not_found:{name}")
    return _load_cached(name, path.stat().st_mtime_ns)
