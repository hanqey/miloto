

"""Shared immutable values for the v3 visible-desktop backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

@dataclass(frozen=True)
class Point:
    x: int
    y: int

@dataclass(frozen=True)
class Rect:
    """A non-empty screen rectangle using right/bottom-exclusive coordinates."""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("矩形必须具有正宽度和正高度。")

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> Point:
        return Point(self.left + self.width // 2, self.top + self.height // 2)

    def contains(self, point: Point) -> bool:
        return (
            self.left <= point.x < self.right
            and self.top <= point.y < self.bottom
        )

    def inset(self, horizontal: int, vertical: int | None = None) -> "Rect":
        vertical = horizontal if vertical is None else vertical
        if horizontal < 0 or vertical < 0:
            raise ValueError("安全内边距不能为负数。")
        if horizontal * 2 >= self.width or vertical * 2 >= self.height:
            raise ValueError("安全内边距不能耗尽矩形。")
        return Rect(
            self.left + horizontal,
            self.top + vertical,
            self.right - horizontal,
            self.bottom - vertical,
        )

@dataclass(frozen=True)
class WindowSnapshot:
    handle: int
    process_id: int
    title: str
    class_name: str
    window_rect: Rect
    client_rect: Rect
    dpi: int
    is_foreground: bool

@dataclass(frozen=True)
class CapturedFrame:
    """One local screenshot and the geometry used to interpret its pixels."""

    image: Any
    screen_rect: Rect
    window: WindowSnapshot
    captured_at: float

@dataclass(frozen=True)
class VisualMatch:
    """Detector output; confidence is always normalized to the inclusive 0..1 range."""

    code: str
    confidence: float
    bounds: Rect | None = None
    text: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("视觉置信度必须位于 0 到 1 之间。")

    @property
    def matched(self) -> bool:
        return self.bounds is not None and self.confidence > 0.0
