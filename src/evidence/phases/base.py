"""Phase ABC and the phase registry.

A phase is a single re-runnable unit of work for one site. Phases talk to each
other ONLY through artifacts on disk (ArtifactWriter), never in-memory, so any
phase can be re-run alone without re-running the ones before it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from evidence.artifacts import ArtifactWriter
from evidence.config import RunConfig, SiteConfig
from evidence.models import PhaseResult
from evidence.politeness import RateLimiter, RequestBudget, RobotsGate


@dataclass
class PhaseContext:
    site: SiteConfig
    run_config: RunConfig
    artifacts: ArtifactWriter
    rate_limiter: RateLimiter
    budget: RequestBudget
    robots: RobotsGate


class Phase(ABC):
    name: str
    depends_on: ClassVar[list[str]] = []

    @abstractmethod
    async def run(self, ctx: PhaseContext) -> PhaseResult: ...


_REGISTRY: dict[str, type[Phase]] = {}


def register(name: str):
    """Class decorator: @register("p1_recon") registers a Phase subclass by name."""

    def _decorator(cls: type[Phase]) -> type[Phase]:
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return _decorator


def get_phase(name: str) -> type[Phase]:
    return _REGISTRY[name]


def all_phases() -> dict[str, type[Phase]]:
    return dict(_REGISTRY)


def resolve_order(names: list[str] | None = None) -> list[str]:
    """Topologically sort registered phases by depends_on.

    If names is given, resolve only that subset (plus their transitive
    dependencies); otherwise resolve every registered phase.
    """
    targets = set(names) if names is not None else set(_REGISTRY.keys())
    # pull in transitive dependencies
    seen: set[str] = set()
    stack = list(targets)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for dep in _REGISTRY[n].depends_on:
            if dep not in seen:
                stack.append(dep)
    targets = seen

    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(n: str) -> None:
        if n in ordered:
            return
        if n in visiting:
            raise ValueError(f"circular phase dependency detected at {n!r}")
        visiting.add(n)
        for dep in _REGISTRY[n].depends_on:
            visit(dep)
        visiting.discard(n)
        ordered.append(n)

    for n in sorted(targets):
        visit(n)
    return ordered
