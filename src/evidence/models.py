"""Pydantic v2 data contracts shared by every phase.

These are contracts: later prompts import them and must not change field
names. If a field turns out to be wrong, that correction belongs in
docs/observations.md alongside the change (see Prompt 1c).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class FetchTier(StrEnum):
    L0 = "L0"  # httpx, default UA
    L1 = "L1"  # httpx, browser headers, http/2
    L2 = "L2"  # curl_cffi, TLS impersonation
    L3 = "L3"  # playwright headless
    L4 = "L4"  # playwright headful
    BLOCKED = "BLOCKED"


class FetchResult(BaseModel):
    url: str
    final_url: str
    status: int | None = None
    tier: FetchTier
    http_version: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    set_cookies: list[str] = Field(default_factory=list)
    body_path: str | None = None  # artifact-relative path, not inline body
    body_sha256: str | None = None
    body_bytes: int = 0
    elapsed_ms: int = 0
    redirect_chain: list[str] = Field(default_factory=list)
    error: str | None = None
    blocked_reason: str | None = None  # "waf_challenge" | "403" | "429" | "robots" | ...


class PhaseResult(BaseModel):
    phase: str
    site: str
    started_at: datetime
    finished_at: datetime
    status: Literal["ok", "partial", "failed", "skipped"]
    artifacts: list[str] = Field(default_factory=list)  # artifact-relative paths written
    requests_made: int = 0
    notes: list[str] = Field(default_factory=list)
    error: str | None = None
