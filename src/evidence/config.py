"""Site targets and run-wide configuration."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class SiteConfig(BaseModel):
    slug: str
    base_url: str
    wave: int
    archetype: str
    expected_family: str
    notes: str | None = None
    seed_search_url: str | None = None
    seed_detail_urls: list[str] = Field(default_factory=list)


class RunConfig(BaseModel):
    evidence_root: Path = Path("./evidence")
    respect_robots: bool = True
    request_budget: int = 60
    min_delay_s: float = 2.0
    max_delay_s: float = 5.0
    concurrency: int = 1
    user_agent: str = (
        "FarmlandEvidenceBot/0.1 (+https://github.com/tonilobaccaro/farmland; "
        "research/evidence-gathering, contact via repo)"
    )
    contact_url: str = "https://github.com/tonilobaccaro/farmland"
    timeout_s: float = 30.0


def load_targets(path: str | Path) -> list[SiteConfig]:
    """Parse targets.yaml into a list of SiteConfig, in file order (wave order)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    sites = data.get("sites", []) if isinstance(data, dict) else data
    return [SiteConfig(**s) for s in sites]
