"""Solver interfaces and implementations."""

from __future__ import annotations

from typing import Any

from .base import BaseSolver
from .codex_solver import CodexSolver
from .mako_solver import MakoSolver
from .manual_solver import ManualSolver
from .pentagi_solver import PentagiSolver

AVAILABLE_SOLVERS: dict[str, type[BaseSolver]] = {
    "codex": CodexSolver,
    "manual": ManualSolver,
    "pentagi": PentagiSolver,
    "mako": MakoSolver,
}


def create_solver(name: str, **kwargs: Any) -> BaseSolver:
    try:
        solver_cls = AVAILABLE_SOLVERS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown solver: {name}") from exc
    return solver_cls(**kwargs)


__all__ = [
    "AVAILABLE_SOLVERS",
    "BaseSolver",
    "CodexSolver",
    "MakoSolver",
    "ManualSolver",
    "PentagiSolver",
    "create_solver",
]
