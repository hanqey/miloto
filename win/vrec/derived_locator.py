

"""Derive one visible target from another locator's reference rectangle."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .models import Rect
from .relative_locator import (
    RelativeLocator,
    RelativeLocatorResult,
    draw_debug_overlay,
    load_relative_locator,
)

class DerivedLocatorConfigError(ValueError):
    pass

def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DerivedLocatorConfigError(f"{label} 必须是非空字符串")
    return value

def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DerivedLocatorConfigError(f"{label} 必须是数字")
    return float(value)

def _fraction_range(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise DerivedLocatorConfigError(f"{label} 必须是 [最小比例, 最大比例]")
    result = (_number(value[0], f"{label}[0]"), _number(value[1], f"{label}[1]"))
    if not 0.0 <= result[0] <= result[1] <= 1.0:
        raise DerivedLocatorConfigError(f"{label} 必须位于 0 到 1 之间并按升序排列")
    return result

@dataclass(frozen=True)
class DerivedEdgeRule:
    reference: str
    offset: float

@dataclass(frozen=True)
class DerivedLocatorSpec:
    source: Path
    locator_id: str
    base_locator: Path
    source_bounds: str
    edges: dict[str, DerivedEdgeRule]
    click_x_range: tuple[float, float]
    click_y_range: tuple[float, float]
    min_width: int
    max_width: int
    min_height: int
    max_height: int

@dataclass(frozen=True)
class DerivedLocatorResult:
    target: Rect | None
    click_bounds: Rect | None
    base: RelativeLocatorResult
    rejection_code: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.target is not None and self.click_bounds is not None

def load_derived_locator(path: str | Path) -> DerivedLocatorSpec:
    source = Path(path).resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DerivedLocatorConfigError(f"派生定位 JSON 不存在：{source}") from exc
    except json.JSONDecodeError as exc:
        raise DerivedLocatorConfigError(
            f"派生定位 JSON 无效：{source}（第 {exc.lineno} 行，第 {exc.colno} 列）"
        ) from exc
    if not isinstance(data, dict):
        raise DerivedLocatorConfigError("派生定位 JSON 顶层必须是对象")
    if data.get("version", 1) != 1:
        raise DerivedLocatorConfigError(f"不支持的派生定位版本：{data.get('version')}")
    locator_id = _string(data.get("id", source.stem), "locator.id")
    base_locator = (source.parent / _string(data.get("base_locator"), "base_locator")).resolve()
    if not base_locator.is_file():
        raise DerivedLocatorConfigError(f"基础定位 JSON 不存在：{base_locator}")
    source_bounds = _string(data.get("source_bounds", "reference_bounds"), "source_bounds")
    if source_bounds not in {"reference_bounds", "target", "click_bounds"}:
        raise DerivedLocatorConfigError("source_bounds 只能是 reference_bounds、target 或 click_bounds")
    target = data.get("target")
    if not isinstance(target, dict):
        raise DerivedLocatorConfigError("target 必须是对象")
    edges: dict[str, DerivedEdgeRule] = {}
    valid_references = {"left", "top", "right", "bottom", "center_x", "center_y"}
    for edge in ("left", "top", "right", "bottom"):
        rule = target.get(edge)
        if not isinstance(rule, dict):
            raise DerivedLocatorConfigError(f"target.{edge} 必须是对象")
        reference = _string(rule.get("reference"), f"target.{edge}.reference")
        if reference not in valid_references:
            raise DerivedLocatorConfigError(f"target.{edge}.reference 无效：{reference}")
        edges[edge] = DerivedEdgeRule(reference, _number(rule.get("offset", 0), f"target.{edge}.offset"))
    click = data.get("click", {})
    if not isinstance(click, dict):
        raise DerivedLocatorConfigError("click 必须是对象")
    bounds = data.get("bounds", {})
    if not isinstance(bounds, dict):
        raise DerivedLocatorConfigError("bounds 必须是对象")
    min_width = int(_number(bounds.get("min_width", 20), "bounds.min_width"))
    max_width = int(_number(bounds.get("max_width", 1200), "bounds.max_width"))
    min_height = int(_number(bounds.get("min_height", 10), "bounds.min_height"))
    max_height = int(_number(bounds.get("max_height", 300), "bounds.max_height"))
    if not (0 < min_width <= max_width and 0 < min_height <= max_height):
        raise DerivedLocatorConfigError("bounds 范围无效")
    return DerivedLocatorSpec(
        source=source,
        locator_id=locator_id,
        base_locator=base_locator,
        source_bounds=source_bounds,
        edges=edges,
        click_x_range=_fraction_range(click.get("x_fraction", [0.15, 0.85]), "click.x_fraction"),
        click_y_range=_fraction_range(click.get("y_fraction", [0.20, 0.80]), "click.y_fraction"),
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
    )

def _reference(bounds: Rect, name: str) -> float:
    if name == "left":
        return float(bounds.left)
    if name == "top":
        return float(bounds.top)
    if name == "right":
        return float(bounds.right)
    if name == "bottom":
        return float(bounds.bottom)
    if name == "center_x":
        return float(bounds.left + bounds.width // 2)
    if name == "center_y":
        return float(bounds.top + bounds.height // 2)
    raise AssertionError(name)

def _click_bounds(target: Rect, x_range: tuple[float, float], y_range: tuple[float, float]) -> Rect:
    left = target.left + int(target.width * x_range[0])
    right = target.left + max(int(target.width * x_range[1]), int(target.width * x_range[0]) + 1)
    top = target.top + int(target.height * y_range[0])
    bottom = target.top + max(int(target.height * y_range[1]), int(target.height * y_range[0]) + 1)
    return Rect(left, top, min(target.right, right), min(target.bottom, bottom))

class DerivedLocator:
    def __init__(self, spec: DerivedLocatorSpec):
        self.spec = spec
        self.base_locator = RelativeLocator(load_relative_locator(spec.base_locator))

    def set_scale_policy(
        self,
        preferred: tuple[float, ...] | list[float],
        fallback: tuple[float, ...] | list[float] | None = None,
    ) -> None:
        self.base_locator.set_scale_policy(preferred, fallback)

    def locate(self, image: Image.Image) -> DerivedLocatorResult:
        base = self.base_locator.locate(image, skip_optional_anchors=True)
        return self.locate_from_base(image, base)

    def locate_from_base(
        self,
        image: Image.Image,
        base: RelativeLocatorResult,
    ) -> DerivedLocatorResult:
        """Derive a target from a base result already verified in this flow."""

        source_bounds = getattr(base, self.spec.source_bounds, None)
        if not base.accepted:
            return DerivedLocatorResult(
                None,
                None,
                base,
                "base_locator_rejected",
                {
                    "accepted_anchors": [
                        anchor_id
                        for anchor_id, detection in base.detections.items()
                        if detection.accepted
                    ],
                    "anchor_scores": {
                        anchor_id: round(detection.score, 4)
                        for anchor_id, detection in base.detections.items()
                    },
                    "rejected_alternatives": list(base.rejected_alternatives),
                },
            )
        if source_bounds is None:
            return DerivedLocatorResult(
                None,
                None,
                base,
                "source_bounds_missing",
                {"source_bounds": self.spec.source_bounds},
            )
        values = {
            edge: round(_reference(source_bounds, rule.reference) + rule.offset)
            for edge, rule in self.spec.edges.items()
        }
        try:
            target = Rect(values["left"], values["top"], values["right"], values["bottom"])
        except ValueError:
            return DerivedLocatorResult(
                None,
                None,
                base,
                "target_rectangle_invalid",
                {"calculated_edges": values},
            )
        if not (
            self.spec.min_width <= target.width <= self.spec.max_width
            and self.spec.min_height <= target.height <= self.spec.max_height
        ):
            return DerivedLocatorResult(
                None,
                None,
                base,
                "target_size_out_of_bounds",
                {
                    "target": {
                        "left": target.left,
                        "top": target.top,
                        "right": target.right,
                        "bottom": target.bottom,
                        "width": target.width,
                        "height": target.height,
                    },
                    "expected": {
                        "min_width": self.spec.min_width,
                        "max_width": self.spec.max_width,
                        "min_height": self.spec.min_height,
                        "max_height": self.spec.max_height,
                    },
                },
            )
        if not (
            0 <= target.left < image.width
            and 0 < target.right <= image.width
            and 0 <= target.top < image.height
            and 0 < target.bottom <= image.height
        ):
            return DerivedLocatorResult(
                None,
                None,
                base,
                "target_out_of_image",
                {
                    "target": {
                        "left": target.left,
                        "top": target.top,
                        "right": target.right,
                        "bottom": target.bottom,
                    },
                    "image_size": {"width": image.width, "height": image.height},
                },
            )
        return DerivedLocatorResult(
            target,
            _click_bounds(target, self.spec.click_x_range, self.spec.click_y_range),
            base,
        )

def draw_derived_debug_overlay(image: Image.Image, result: DerivedLocatorResult) -> Image.Image:
    output = draw_debug_overlay(image, result.base)
    draw = ImageDraw.Draw(output)
    used_anchors = {"element1"}
    if result.base.alternative_id == "element1_plus_element2":
        used_anchors.add("element2")
    elif result.base.alternative_id == "element1_plus_element3":
        used_anchors.add("element3")
    for anchor_id, detection in result.base.detections.items():
        if anchor_id not in used_anchors or detection.bounds is None:
            continue
        bounds = detection.bounds
        draw.rectangle((bounds.left, bounds.top, bounds.right - 1, bounds.bottom - 1), outline="#5487c7", width=1)
    if result.base.reference_bounds is not None:
        bounds = result.base.reference_bounds
        draw.rectangle((bounds.left, bounds.top, bounds.right - 1, bounds.bottom - 1), outline="#2f9ca6", width=2)
        draw.text((bounds.left + 2, max(0, bounds.top - 12)), "ORIGINAL SEARCH", fill="#257d85")
    if result.target is not None:
        bounds = result.target
        draw.rectangle((bounds.left, bounds.top, bounds.right - 1, bounds.bottom - 1), outline="#b24aa5", width=2)
        draw.text((bounds.left + 2, max(0, bounds.top - 12)), "PRIMARY RESULT", fill="#9a338d")
    if result.click_bounds is not None:
        bounds = result.click_bounds
        draw.rectangle((bounds.left, bounds.top, bounds.right - 1, bounds.bottom - 1), outline="#d0a000", width=2)
        draw.text((bounds.left + 2, bounds.bottom + 2), "CLICK SAFE", fill="#a47d00")
    return output
