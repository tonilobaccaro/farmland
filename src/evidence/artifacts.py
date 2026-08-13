"""ArtifactWriter — the only way any phase touches disk.

Contract, do not change later: phases communicate ONLY through artifacts on
disk (never in-memory), so any phase can be re-run alone. See CLAUDE.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# The exact directory layout from Part 4 of docs/evidence-gathering-plan.md.
SITE_LAYOUT = [
    "00_meta",
    "00_meta/phases",
    "01_policy",
    "01_policy/sitemaps",
    "01_policy/archive",
    "02_rendering",
    "02_rendering/hydration",
    "03_network",
    "03_network/har",
    "03_network/api_samples",
    "04_navigation",
    "05_listing_pages",
    "06_detail_pages",
    "07_fields",
    "08_dynamics",
    "99_report",
]


class ArtifactWriter:
    """Writes every artifact for one site under <root>/<site_slug>/."""

    def __init__(self, site_slug: str, root: Path) -> None:
        self.site_slug = site_slug
        self.root = Path(root)
        self.site_root = self.root / site_slug

    def scaffold(self) -> None:
        """Create the full directory tree for this site, even with nothing in it."""
        for rel in SITE_LAYOUT:
            (self.site_root / rel).mkdir(parents=True, exist_ok=True)

    def path(self, rel: str) -> Path:
        """Resolve rel (relative to the site root) to an absolute path, creating parents."""
        abs_path = self.site_root / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        return abs_path

    def json(self, rel: str, obj: Any) -> str:
        p = self.path(rel)
        p.write_text(
            json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return rel

    def text(self, rel: str, s: str) -> str:
        p = self.path(rel)
        p.write_text(s, encoding="utf-8")
        return rel

    def bytes(self, rel: str, b: bytes) -> str:
        p = self.path(rel)
        p.write_bytes(b)
        return rel

    def html(self, rel: str, s: str) -> str:
        return self.text(rel, s)

    def exists(self, rel: str) -> bool:
        return (self.site_root / rel).exists()

    def read_json(self, rel: str) -> Any:
        return json.loads((self.site_root / rel).read_text(encoding="utf-8"))
