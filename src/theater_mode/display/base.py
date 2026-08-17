"""Data structures and base interfaces for display topology and brightness."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OutputGeometry:
    """Position and dimension of a display output."""

    name: str
    x: int
    y: int
    width: int
    height: int

    @property
    def position(self) -> tuple[int, int]:
        return (self.x, self.y)

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)
