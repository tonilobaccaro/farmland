from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from evidence.artifacts import SITE_LAYOUT, ArtifactWriter
from evidence.config import RunConfig, SiteConfig, load_targets
from evidence.models import FetchResult, FetchTier, PhaseResult
from evidence.phases.base import Phase, PhaseContext, register, resolve_order
from evidence.politeness import BudgetExhausted, RequestBudget, RobotsGate

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# ArtifactWriter round-trips
# ---------------------------------------------------------------------------


def test_artifact_writer_scaffold_creates_layout(tmp_path: Path) -> None:
    aw = ArtifactWriter("some-site", tmp_path)
    aw.scaffold()
    for rel in SITE_LAYOUT:
        assert (tmp_path / "some-site" / rel).is_dir()


def test_artifact_writer_json_roundtrip(tmp_path: Path) -> None:
    aw = ArtifactWriter("some-site", tmp_path)
    obj = {"b": 2, "a": [1, 2, 3], "c": "héllo"}
    rel = aw.json("00_meta/run.json", obj)
    assert rel == "00_meta/run.json"
    assert aw.exists(rel)
    assert aw.read_json(rel) == obj

    raw = (tmp_path / "some-site" / rel).read_text(encoding="utf-8")
    assert raw.startswith('{\n  "a"')  # sort_keys=True, indent=2
    assert "héllo" in raw  # ensure_ascii=False


def test_artifact_writer_text_bytes_html(tmp_path: Path) -> None:
    aw = ArtifactWriter("some-site", tmp_path)
    aw.text("01_policy/robots.txt", "User-agent: *\n")
    aw.bytes("06_detail_pages/sample_01/assets/brochure.pdf", b"%PDF-1.4 fake")
    aw.html("02_rendering/home_raw.html", "<html></html>")

    assert aw.exists("01_policy/robots.txt")
    assert aw.exists("06_detail_pages/sample_01/assets/brochure.pdf")
    assert aw.exists("02_rendering/home_raw.html")
    assert not aw.exists("nope.json")


def test_artifact_writer_scopes_under_root_and_slug(tmp_path: Path) -> None:
    aw = ArtifactWriter("landwatch", tmp_path)
    p = aw.path("00_meta/dns_tls.json")
    assert p == tmp_path / "landwatch" / "00_meta" / "dns_tls.json"


# ---------------------------------------------------------------------------
# targets.yaml
# ---------------------------------------------------------------------------


def test_targets_yaml_parses_and_has_enough_sites() -> None:
    sites = load_targets(REPO_ROOT / "targets.yaml")
    assert len(sites) >= 55
    assert all(isinstance(s, SiteConfig) for s in sites)


def test_targets_yaml_covers_all_four_waves() -> None:
    sites = load_targets(REPO_ROOT / "targets.yaml")
    waves = {s.wave for s in sites}
    assert waves == {1, 2, 3, 4}


def test_targets_yaml_slugs_are_unique() -> None:
    sites = load_targets(REPO_ROOT / "targets.yaml")
    slugs = [s.slug for s in sites]
    assert len(slugs) == len(set(slugs))


def test_targets_yaml_pilot_sites_present() -> None:
    sites = {s.slug: s for s in load_targets(REPO_ROOT / "targets.yaml")}
    for slug in ["landwatch", "peoplescompany", "schraderauction", "hibid", "acrevalue"]:
        assert slug in sites
        assert sites[slug].wave == 1


# ---------------------------------------------------------------------------
# Phase registry
# ---------------------------------------------------------------------------


class _FakePhaseA(Phase):
    depends_on: ClassVar[list[str]] = []

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        now = datetime.now(UTC)
        return PhaseResult(
            phase=self.name, site=ctx.site.slug, started_at=now, finished_at=now, status="ok"
        )


class _FakePhaseB(Phase):
    depends_on: ClassVar[list[str]] = ["test_phase_a"]

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        now = datetime.now(UTC)
        return PhaseResult(
            phase=self.name, site=ctx.site.slug, started_at=now, finished_at=now, status="ok"
        )


class _FakePhaseC(Phase):
    depends_on: ClassVar[list[str]] = ["test_phase_b"]

    async def run(self, ctx: PhaseContext) -> PhaseResult:
        now = datetime.now(UTC)
        return PhaseResult(
            phase=self.name, site=ctx.site.slug, started_at=now, finished_at=now, status="ok"
        )


register("test_phase_a")(_FakePhaseA)
register("test_phase_b")(_FakePhaseB)
register("test_phase_c")(_FakePhaseC)


def test_phase_registry_resolves_dependencies_in_order() -> None:
    order = resolve_order(["test_phase_c"])
    assert order.index("test_phase_a") < order.index("test_phase_b") < order.index("test_phase_c")


def test_phase_registry_pulls_in_transitive_deps_only_once() -> None:
    order = resolve_order(["test_phase_a", "test_phase_c"])
    assert order.count("test_phase_a") == 1
    assert set(order) == {"test_phase_a", "test_phase_b", "test_phase_c"}


# ---------------------------------------------------------------------------
# Politeness
# ---------------------------------------------------------------------------


def test_request_budget_raises_when_exhausted() -> None:
    budget = RequestBudget(limit=2)
    budget.consume()
    budget.consume()
    with pytest.raises(BudgetExhausted):
        budget.consume()


def test_robots_gate_disallow_is_recorded_and_blocked_by_default() -> None:
    robots_txt = "User-agent: *\nDisallow: /private/\n"
    gate = RobotsGate(robots_txt, user_agent="FarmlandEvidenceBot/0.1", respect_robots=True)
    assert gate.allowed("https://example.com/public/") is True
    assert gate.allowed("https://example.com/private/x") is False
    assert len(gate.conflicts) == 1
    assert gate.conflicts[0].url == "https://example.com/private/x"


def test_robots_gate_records_conflict_even_when_not_respecting_robots() -> None:
    robots_txt = "User-agent: *\nDisallow: /private/\n"
    gate = RobotsGate(robots_txt, user_agent="FarmlandEvidenceBot/0.1", respect_robots=False)
    assert gate.allowed("https://example.com/private/x") is True
    assert len(gate.conflicts) == 1


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_fetch_result_minimal_construction() -> None:
    fr = FetchResult(url="https://example.com", final_url="https://example.com", tier=FetchTier.L1)
    assert fr.status is None
    assert fr.tier == "L1"


def test_run_config_defaults() -> None:
    rc = RunConfig()
    assert rc.respect_robots is True
    assert rc.request_budget == 60
    assert "http" not in rc.evidence_root.as_posix()  # a local path, not a URL
