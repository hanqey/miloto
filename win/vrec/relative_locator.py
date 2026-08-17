

"""Visible screenshot anchor-relative locator for v3.

The locator deliberately does not inspect WeChat's process, accessibility tree,
memory, or private protocol.  It finds small, stable visual anchors in a
captured screenshot and derives a target rectangle from explicit JSON edge
rules.  The first phase is diagnostic: callers can draw the result without
clicking anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image, ImageDraw

from .models import Point, Rect
from .template_matching import OpenCVTemplateMatcher, TemplateMatch

class RelativeLocatorConfigError(ValueError):
    """A locator JSON file is malformed or points to a missing asset."""

class RelativeLocatorError(RuntimeError):
    """A locator could not produce a safe target rectangle."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = dict(details or {})

def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RelativeLocatorConfigError(f"locator JSON 不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise RelativeLocatorConfigError(
            f"locator JSON 无效：{path}（第 {exc.lineno} 行，第 {exc.colno} 列）"
        ) from exc
    if not isinstance(value, dict):
        raise RelativeLocatorConfigError(f"locator JSON 顶层必须是对象：{path}")
    return value

def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RelativeLocatorConfigError(f"{label} 必须是非空字符串")
    return value

def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RelativeLocatorConfigError(f"{label} 必须是数字")
    return float(value)

def _normalized_roi(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise RelativeLocatorConfigError(f"{label} 必须是 [left, top, right, bottom]")
    result = tuple(_number(item, f"{label}[{index}]") for index, item in enumerate(value))
    if not all(0.0 <= item <= 1.0 for item in result):
        raise RelativeLocatorConfigError(f"{label} 必须位于 0 到 1 之间")
    if result[0] >= result[2] or result[1] >= result[3]:
        raise RelativeLocatorConfigError(f"{label} 的左/上边必须小于右/下边")
    return result

def _absolute_roi(value: Any, label: str) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise RelativeLocatorConfigError(f"{label} 必须是 [left, top, right, bottom]")
    result: list[int] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int):
            raise RelativeLocatorConfigError(f"{label}[{index}] 必须是非负整数")
        if item < 0:
            raise RelativeLocatorConfigError(f"{label}[{index}] 必须是非负整数")
        result.append(item)
    if result[0] >= result[2] or result[1] >= result[3]:
        raise RelativeLocatorConfigError(f"{label} 的左/上边必须小于右/下边")
    return result[0], result[1], result[2], result[3]

def _fraction_range(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise RelativeLocatorConfigError(f"{label} 必须是 [最小比例, 最大比例]")
    result = (_number(value[0], f"{label}[0]"), _number(value[1], f"{label}[1]"))
    if not 0.0 <= result[0] <= result[1] <= 1.0:
        raise RelativeLocatorConfigError(f"{label} 必须位于 0 到 1 之间并按升序排列")
    return result

@dataclass(frozen=True)
class AnchorTemplateSpec:
    path: Path
    minimum_score: float
    minimum_margin: float
    coarse_step: int
    scale_factors: tuple[float, ...] = (1.0,)

@dataclass(frozen=True)
class AnchorSpec:
    anchor_id: str
    templates: tuple[AnchorTemplateSpec, ...]
    roi: tuple[float, float, float, float]
    pixel_roi: tuple[int, int, int, int] | None = None
    max_candidates: int = 8

@dataclass(frozen=True)
class EdgeRule:
    anchor: str
    reference: str
    offset: float

@dataclass(frozen=True)
class OptionalAdjustmentSpec:
    """Modify one target edge only when an optional anchor is visible."""

    anchor: str
    edge: str
    reference: str
    offset: float
    mode: str

@dataclass(frozen=True)
class ScalarReferenceSpec:
    """One numeric edge/centre value from an anchor or the screenshot."""

    source: str
    reference: str

@dataclass(frozen=True)
class DifferenceConstraintSpec:
    """Accept only when ``left - right`` stays inside the configured range."""

    left: ScalarReferenceSpec
    right: ScalarReferenceSpec
    min_difference: float
    max_difference: float

@dataclass(frozen=True)
class AlternativeSpec:
    alternative_id: str
    anchors: tuple[str, ...]
    reference_edges: dict[str, EdgeRule] | None
    edges: dict[str, EdgeRule]
    optional_adjustments: tuple[OptionalAdjustmentSpec, ...]
    constraints: tuple[DifferenceConstraintSpec, ...]
    min_width: int
    max_width: int
    min_height: int
    max_height: int

@dataclass(frozen=True)
class RelativeLocatorSpec:
    source: Path
    locator_id: str
    theme: str
    anchors: dict[str, AnchorSpec]
    alternatives: tuple[AlternativeSpec, ...]
    click_x_range: tuple[float, float]
    click_y_range: tuple[float, float]

@dataclass(frozen=True)
class AnchorDetection:
    anchor_id: str
    template: Path | None
    bounds: Rect | None
    score: float
    second_score: float
    accepted: bool
    scale: float = 1.0

    @property
    def center(self) -> Point | None:
        return None if self.bounds is None else self.bounds.center

@dataclass(frozen=True)
class RelativeLocatorCombination:
    """One complete anchor combination that satisfies every locator rule."""

    combination_id: str
    alternative_id: str
    used_anchor_ids: tuple[str, ...]
    detections: dict[str, AnchorDetection]
    target: Rect
    click_bounds: Rect
    reference_bounds: Rect
    score: float

@dataclass(frozen=True)
class RelativeLocatorResult:
    alternative_id: str | None
    target: Rect | None
    detections: dict[str, AnchorDetection]
    rejected_alternatives: tuple[str, ...] = ()
    click_bounds: Rect | None = None
    reference_bounds: Rect | None = None
    anchor_candidates: dict[str, tuple[AnchorDetection, ...]] | None = None
    valid_combinations: tuple[RelativeLocatorCombination, ...] = ()
    distinct_combinations: tuple[RelativeLocatorCombination, ...] = ()
    failure_code: str = ""

    @property
    def accepted(self) -> bool:
        return self.target is not None and self.alternative_id is not None

def _parse_template(source: Path, value: Any, label: str) -> AnchorTemplateSpec:
    if isinstance(value, str):
        data: dict[str, Any] = {"path": value}
    elif isinstance(value, dict):
        data = value
    else:
        raise RelativeLocatorConfigError(f"{label} 必须是路径字符串或对象")
    template_value = _non_empty_string(data.get("path"), f"{label}.path")
    path = (source.parent / template_value).resolve()
    if not path.is_file():
        raise RelativeLocatorConfigError(f"锚点模板不存在：{path}")
    minimum_score = _number(data.get("minimum_score", 0.90), f"{label}.minimum_score")
    minimum_margin = _number(data.get("minimum_margin", 0.01), f"{label}.minimum_margin")
    coarse_step_value = data.get("coarse_step", 1)
    if isinstance(coarse_step_value, bool) or not isinstance(coarse_step_value, int):
        raise RelativeLocatorConfigError(f"{label}.coarse_step 必须是正整数")
    if coarse_step_value < 1:
        raise RelativeLocatorConfigError(f"{label}.coarse_step 必须是正整数")
    if not 0.0 <= minimum_score <= 1.0 or minimum_margin < 0.0:
        raise RelativeLocatorConfigError(f"{label} 的匹配阈值无效")
    scale_values = data.get("scales", [1.0])
    if not isinstance(scale_values, list) or not scale_values:
        raise RelativeLocatorConfigError(f"{label}.scales 必须是非空数组")
    scales: list[float] = []
    for index, item in enumerate(scale_values):
        scale = _number(item, f"{label}.scales[{index}]")
        if not 0.5 <= scale <= 3.0:
            raise RelativeLocatorConfigError(
                f"{label}.scales[{index}] 必须位于 0.5 到 3.0 之间"
            )
        if scale not in scales:
            scales.append(scale)
    return AnchorTemplateSpec(
        path,
        minimum_score,
        minimum_margin,
        coarse_step_value,
        tuple(scales),
    )

def _parse_scalar_reference(
    value: Any,
    label: str,
    required_anchors: tuple[str, ...],
) -> ScalarReferenceSpec:
    if not isinstance(value, dict):
        raise RelativeLocatorConfigError(f"{label} 必须是对象")
    source = _non_empty_string(value.get("source"), f"{label}.source")
    if source != "image" and source not in required_anchors:
        raise RelativeLocatorConfigError(
            f"{label}.source 必须是 image 或当前方案声明的锚点：{source}"
        )
    reference = _non_empty_string(value.get("reference"), f"{label}.reference")
    if reference not in {"left", "top", "right", "bottom", "center_x", "center_y"}:
        raise RelativeLocatorConfigError(f"{label}.reference 无效：{reference}")
    return ScalarReferenceSpec(source, reference)

def load_relative_locator(path: str | Path) -> RelativeLocatorSpec:
    source = Path(path).resolve()
    data = _object(source)
    if data.get("version", 1) != 1:
        raise RelativeLocatorConfigError(f"不支持的 locator JSON 版本：{data.get('version')}")
    locator_id = _non_empty_string(data.get("id", source.stem), "locator.id")
    theme = _non_empty_string(data.get("theme", "light"), "locator.theme")
    if theme != "light":
        raise RelativeLocatorConfigError(
            f"当前 v3 自动化只支持 light 主题定位规则，不能加载：{theme}"
        )
    anchor_values = data.get("anchors")
    if not isinstance(anchor_values, dict) or not anchor_values:
        raise RelativeLocatorConfigError("locator.anchors 必须是非空对象")
    anchors: dict[str, AnchorSpec] = {}
    for anchor_id, value in anchor_values.items():
        anchor_id = _non_empty_string(anchor_id, "anchor id")
        if not isinstance(value, dict):
            raise RelativeLocatorConfigError(f"anchors.{anchor_id} 必须是对象")
        templates = value.get("templates")
        if not isinstance(templates, list) or not templates:
            raise RelativeLocatorConfigError(f"anchors.{anchor_id}.templates 不能为空")
        anchors[anchor_id] = AnchorSpec(
            anchor_id=anchor_id,
            templates=tuple(
                _parse_template(source, item, f"anchors.{anchor_id}.templates[{index}]")
                for index, item in enumerate(templates)
            ),
            roi=_normalized_roi(value.get("roi", [0, 0, 1, 1]), f"anchors.{anchor_id}.roi"),
            pixel_roi=_absolute_roi(
                value.get("pixel_roi"),
                f"anchors.{anchor_id}.pixel_roi",
            ),
            max_candidates=int(
                _number(
                    value.get("max_candidates", 8),
                    f"anchors.{anchor_id}.max_candidates",
                )
            ),
        )
        if not 1 <= anchors[anchor_id].max_candidates <= 32:
            raise RelativeLocatorConfigError(
                f"anchors.{anchor_id}.max_candidates 必须在 1 到 32 之间"
            )

    alternative_values = data.get("alternatives")
    if not isinstance(alternative_values, list) or not alternative_values:
        raise RelativeLocatorConfigError("locator.alternatives 必须是非空数组")
    alternatives: list[AlternativeSpec] = []
    for index, value in enumerate(alternative_values):
        label = f"alternatives[{index}]"
        if not isinstance(value, dict):
            raise RelativeLocatorConfigError(f"{label} 必须是对象")
        alternative_id = _non_empty_string(value.get("id", f"alternative_{index + 1}"), f"{label}.id")
        required = value.get("anchors")
        if not isinstance(required, list) or not required:
            raise RelativeLocatorConfigError(f"{label}.anchors 必须是非空数组")
        required_ids = tuple(_non_empty_string(item, f"{label}.anchors[{i}]") for i, item in enumerate(required))
        unknown = [item for item in required_ids if item not in anchors]
        if unknown:
            raise RelativeLocatorConfigError(f"{label} 引用了未知锚点：{unknown}")
        reference_target = value.get("reference_target")
        reference_edges: dict[str, EdgeRule] | None = None
        if reference_target is not None:
            if not isinstance(reference_target, dict):
                raise RelativeLocatorConfigError(f"{label}.reference_target 必须是对象")
            reference_edges = {}
            for edge in ("left", "top", "right", "bottom"):
                rule = reference_target.get(edge)
                if not isinstance(rule, dict):
                    raise RelativeLocatorConfigError(f"{label}.reference_target.{edge} 必须是对象")
                anchor = _non_empty_string(rule.get("anchor"), f"{label}.reference_target.{edge}.anchor")
                if anchor not in required_ids:
                    raise RelativeLocatorConfigError(
                        f"{label}.reference_target.{edge} 未声明所需锚点：{anchor}"
                    )
                reference = _non_empty_string(
                    rule.get("reference"),
                    f"{label}.reference_target.{edge}.reference",
                )
                if reference not in {"left", "top", "right", "bottom", "center_x", "center_y"}:
                    raise RelativeLocatorConfigError(
                        f"{label}.reference_target.{edge}.reference 无效：{reference}"
                    )
                reference_edges[edge] = EdgeRule(
                    anchor,
                    reference,
                    _number(rule.get("offset", 0), f"{label}.reference_target.{edge}.offset"),
                )
        target = value.get("target")
        if not isinstance(target, dict):
            raise RelativeLocatorConfigError(f"{label}.target 必须是对象")
        edges: dict[str, EdgeRule] = {}
        for edge in ("left", "top", "right", "bottom"):
            rule = target.get(edge)
            if not isinstance(rule, dict):
                raise RelativeLocatorConfigError(f"{label}.target.{edge} 必须是对象")
            anchor = _non_empty_string(rule.get("anchor"), f"{label}.target.{edge}.anchor")
            if anchor not in required_ids:
                raise RelativeLocatorConfigError(f"{label}.target.{edge} 未声明所需锚点：{anchor}")
            reference = _non_empty_string(rule.get("reference"), f"{label}.target.{edge}.reference")
            if reference not in {"left", "top", "right", "bottom", "center_x", "center_y"}:
                raise RelativeLocatorConfigError(f"{label}.target.{edge}.reference 无效：{reference}")
            edges[edge] = EdgeRule(anchor, reference, _number(rule.get("offset", 0), f"{label}.target.{edge}.offset"))
        adjustment_values = value.get("optional_adjustments", [])
        if not isinstance(adjustment_values, list):
            raise RelativeLocatorConfigError(f"{label}.optional_adjustments 必须是数组")
        optional_adjustments: list[OptionalAdjustmentSpec] = []
        for adjustment_index, adjustment in enumerate(adjustment_values):
            adjustment_label = f"{label}.optional_adjustments[{adjustment_index}]"
            if not isinstance(adjustment, dict):
                raise RelativeLocatorConfigError(f"{adjustment_label} 必须是对象")
            anchor = _non_empty_string(adjustment.get("anchor"), f"{adjustment_label}.anchor")
            if anchor not in anchors:
                raise RelativeLocatorConfigError(f"{adjustment_label} 引用了未知锚点：{anchor}")
            edge = _non_empty_string(adjustment.get("edge"), f"{adjustment_label}.edge")
            if edge not in {"left", "top", "right", "bottom"}:
                raise RelativeLocatorConfigError(f"{adjustment_label}.edge 无效：{edge}")
            reference = _non_empty_string(adjustment.get("reference"), f"{adjustment_label}.reference")
            if reference not in {"left", "top", "right", "bottom", "center_x", "center_y"}:
                raise RelativeLocatorConfigError(f"{adjustment_label}.reference 无效：{reference}")
            mode = _non_empty_string(adjustment.get("mode", "replace"), f"{adjustment_label}.mode")
            if mode not in {"min", "max", "replace"}:
                raise RelativeLocatorConfigError(f"{adjustment_label}.mode 只能是 min、max 或 replace")
            optional_adjustments.append(OptionalAdjustmentSpec(
                anchor=anchor,
                edge=edge,
                reference=reference,
                offset=_number(adjustment.get("offset", 0), f"{adjustment_label}.offset"),
                mode=mode,
            ))
        constraint_values = value.get("constraints", [])
        if not isinstance(constraint_values, list):
            raise RelativeLocatorConfigError(f"{label}.constraints 必须是数组")
        constraints: list[DifferenceConstraintSpec] = []
        for constraint_index, constraint in enumerate(constraint_values):
            constraint_label = f"{label}.constraints[{constraint_index}]"
            if not isinstance(constraint, dict):
                raise RelativeLocatorConfigError(f"{constraint_label} 必须是对象")
            min_difference = _number(
                constraint.get("min_difference"),
                f"{constraint_label}.min_difference",
            )
            max_difference = _number(
                constraint.get("max_difference"),
                f"{constraint_label}.max_difference",
            )
            if min_difference > max_difference:
                raise RelativeLocatorConfigError(
                    f"{constraint_label} 的最小差值不能大于最大差值"
                )
            constraints.append(DifferenceConstraintSpec(
                left=_parse_scalar_reference(
                    constraint.get("left"),
                    f"{constraint_label}.left",
                    required_ids,
                ),
                right=_parse_scalar_reference(
                    constraint.get("right"),
                    f"{constraint_label}.right",
                    required_ids,
                ),
                min_difference=min_difference,
                max_difference=max_difference,
            ))
        bounds = value.get("bounds", {})
        if not isinstance(bounds, dict):
            raise RelativeLocatorConfigError(f"{label}.bounds 必须是对象")
        min_width = int(_number(bounds.get("min_width", 20), f"{label}.bounds.min_width"))
        max_width = int(_number(bounds.get("max_width", 1200), f"{label}.bounds.max_width"))
        min_height = int(_number(bounds.get("min_height", 10), f"{label}.bounds.min_height"))
        max_height = int(_number(bounds.get("max_height", 300), f"{label}.bounds.max_height"))
        if not (0 < min_width <= max_width and 0 < min_height <= max_height):
            raise RelativeLocatorConfigError(f"{label}.bounds 范围无效")
        alternatives.append(AlternativeSpec(
            alternative_id,
            required_ids,
            reference_edges,
            edges,
            tuple(optional_adjustments),
            tuple(constraints),
            min_width,
            max_width,
            min_height,
            max_height,
        ))
    click = data.get("click", {})
    if not isinstance(click, dict):
        raise RelativeLocatorConfigError("locator.click 必须是对象")
    click_x_range = _fraction_range(click.get("x_fraction", [0.18, 0.82]), "locator.click.x_fraction")
    click_y_range = _fraction_range(click.get("y_fraction", [0.40, 0.60]), "locator.click.y_fraction")
    return RelativeLocatorSpec(
        source,
        locator_id,
        theme,
        anchors,
        tuple(alternatives),
        click_x_range,
        click_y_range,
    )

def _pixel_roi(
    image: Image.Image,
    roi: tuple[float, float, float, float],
    absolute: tuple[int, int, int, int] | None = None,
    scales: tuple[float, ...] = (1.0,),
) -> tuple[int, int, int, int]:
    width, height = image.size
    if absolute is not None:
        left, top, right, bottom = absolute
        minimum_scale = min(scales)
        maximum_scale = max(scales)
        left = int(left * minimum_scale)
        top = int(top * minimum_scale)
        right = round(right * maximum_scale)
        bottom = round(bottom * maximum_scale)
        return (
            min(width, left),
            min(height, top),
            min(width, max(right, left + 1)),
            min(height, max(bottom, top + 1)),
        )
    left, top, right, bottom = roi
    return (
        int(width * left),
        int(height * top),
        max(int(width * right), int(width * left) + 1),
        max(int(height * bottom), int(height * top) + 1),
    )

def _reference(bounds: Rect, reference: str) -> float:
    if reference == "left":
        return float(bounds.left)
    if reference == "top":
        return float(bounds.top)
    if reference == "right":
        return float(bounds.right)
    if reference == "bottom":
        return float(bounds.bottom)
    if reference == "center_x":
        return float(bounds.left + bounds.width // 2)
    if reference == "center_y":
        return float(bounds.top + bounds.height // 2)
    raise AssertionError(reference)

def _image_reference(image: Image.Image, reference: str) -> float:
    if reference in {"left", "top"}:
        return 0.0
    if reference == "right":
        return float(image.width)
    if reference == "bottom":
        return float(image.height)
    if reference == "center_x":
        return float(image.width // 2)
    if reference == "center_y":
        return float(image.height // 2)
    raise AssertionError(reference)

class RelativeLocator:
    """Match configured anchors and derive a target rectangle."""

    def __init__(self, spec: RelativeLocatorSpec):
        self.spec = spec
        self._preferred_scale_factors: tuple[float, ...] | None = None
        self._fallback_scale_factors: tuple[float, ...] | None = None
        self._matchers: dict[
            tuple[str, float, float, int, tuple[float, ...]],
            OpenCVTemplateMatcher,
        ] = {}

    @staticmethod
    def _normalise_scale_factors(values: tuple[float, ...] | list[float]) -> tuple[float, ...]:
        scales: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("DPI 缩放比例必须是数字。")
            scale = round(float(value), 4)
            if not 0.5 <= scale <= 3.0:
                raise ValueError("DPI 缩放比例必须位于 0.5 到 3.0 之间。")
            if scale not in scales:
                scales.append(scale)
        if not scales:
            raise ValueError("DPI 缩放比例不能为空。")
        return tuple(scales)

    def set_scale_policy(
        self,
        preferred: tuple[float, ...] | list[float],
        fallback: tuple[float, ...] | list[float] | None = None,
    ) -> None:
        """Try the reported/manual scale first, then a configured auto range."""

        primary = self._normalise_scale_factors(preferred)
        secondary = self._normalise_scale_factors(fallback or primary)
        self._preferred_scale_factors = primary
        self._fallback_scale_factors = secondary

    def _matcher_for(
        self,
        template: AnchorTemplateSpec,
        scale_factors: tuple[float, ...] | None = None,
    ) -> OpenCVTemplateMatcher:
        scales = scale_factors or template.scale_factors
        key = (
            str(template.path),
            template.minimum_score,
            template.minimum_margin,
            template.coarse_step,
            scales,
        )
        matcher = self._matchers.get(key)
        if matcher is None:
            matcher = OpenCVTemplateMatcher(
                template.path,
                coarse_step=template.coarse_step,
                minimum_score=template.minimum_score,
                minimum_margin=template.minimum_margin,
                scale_factors=scales,
            )
            self._matchers[key] = matcher
        return matcher

    def _match_template(
        self,
        image: Image.Image,
        template: AnchorTemplateSpec,
        roi: tuple[int, int, int, int],
        scale_factors: tuple[float, ...] | None = None,
    ) -> TemplateMatch:
        return self._matcher_for(template, scale_factors).match(image, roi=roi)

    @staticmethod
    def _missing_detection(anchor_id: str) -> AnchorDetection:
        return AnchorDetection(anchor_id, None, None, 0.0, 0.0, False)

    @staticmethod
    def _same_anchor_hit(left: AnchorDetection, right: AnchorDetection) -> bool:
        if left.bounds is None or right.bounds is None:
            return False
        left_center = left.bounds.center
        right_center = right.bounds.center
        return (
            abs(left_center.x - right_center.x)
            <= max(3, min(left.bounds.width, right.bounds.width) // 3)
            and abs(left_center.y - right_center.y)
            <= max(3, min(left.bounds.height, right.bounds.height) // 3)
        )

    def _candidates_for_anchor(
        self,
        image: Image.Image,
        anchor: AnchorSpec,
        roi: tuple[int, int, int, int],
        scale_factors: tuple[float, ...] | None = None,
    ) -> tuple[AnchorDetection, ...]:
        found: list[AnchorDetection] = []
        for template in anchor.templates:
            matcher = self._matcher_for(template, scale_factors)
            for candidate in matcher.find_candidates(
                image,
                roi=roi,
                max_candidates=anchor.max_candidates,
            ):
                detection = AnchorDetection(
                    anchor.anchor_id,
                    template.path,
                    candidate.bounds,
                    candidate.score,
                    0.0,
                    True,
                    candidate.scale,
                )
                duplicate = next(
                    (
                        index
                        for index, existing in enumerate(found)
                        if self._same_anchor_hit(existing, detection)
                    ),
                    None,
                )
                if duplicate is None:
                    found.append(detection)
                elif detection.score > found[duplicate].score:
                    found[duplicate] = detection
        found.sort(key=lambda item: item.score, reverse=True)
        limited = found[: anchor.max_candidates]
        return tuple(
            AnchorDetection(
                item.anchor_id,
                item.template,
                item.bounds,
                item.score,
                max(
                    (
                        other.score
                        for other in limited
                        if other is not item and not self._same_anchor_hit(item, other)
                    ),
                    default=0.0,
                ),
                True,
                item.scale,
            )
            for item in limited
        )

    def detect_anchor_candidates(
        self,
        image: Image.Image,
        *,
        skip_anchors: set[str] | None = None,
        roi_overrides: dict[str, tuple[int, int, int, int]] | None = None,
        scale_factors: tuple[float, ...] | None = None,
    ) -> dict[str, tuple[AnchorDetection, ...]]:
        skip_anchors = set(skip_anchors or ())
        roi_overrides = dict(roi_overrides or {})
        candidates: dict[str, tuple[AnchorDetection, ...]] = {}
        for anchor_id, anchor in self.spec.anchors.items():
            if anchor_id in skip_anchors:
                candidates[anchor_id] = ()
                continue
            roi = roi_overrides.get(
                anchor_id,
                _pixel_roi(
                    image,
                    anchor.roi,
                    anchor.pixel_roi,
                    tuple(
                        scale
                        for template in anchor.templates
                        for scale in (scale_factors or template.scale_factors)
                    ),
                ),
            )
            candidates[anchor_id] = self._candidates_for_anchor(
                image,
                anchor,
                roi,
                scale_factors,
            )
        return candidates

    def detect_anchors(
        self,
        image: Image.Image,
        *,
        skip_anchors: set[str] | None = None,
    ) -> dict[str, AnchorDetection]:
        candidates = self.detect_anchor_candidates(
            image,
            skip_anchors=skip_anchors,
        )
        return {
            anchor_id: values[0] if values else self._missing_detection(anchor_id)
            for anchor_id, values in candidates.items()
        }

    @staticmethod
    def _constraint_value(
        value: ScalarReferenceSpec,
        detections: dict[str, AnchorDetection],
        image: Image.Image,
    ) -> float | None:
        if value.source == "image":
            return _image_reference(image, value.reference)
        detection = detections[value.source]
        if not detection.accepted or detection.bounds is None:
            return None
        return _reference(detection.bounds, value.reference)

    @staticmethod
    def _combination_scale(
        alternative: AlternativeSpec,
        detections: dict[str, AnchorDetection],
    ) -> float | None:
        scales = [detections[name].scale for name in alternative.anchors]
        if not scales:
            return 1.0
        typical = float(median(scales))
        tolerance = max(0.08, typical * 0.08)
        if max(scales) - min(scales) > tolerance:
            return None
        return typical

    def _constraints_hold(
        self,
        alternative: AlternativeSpec,
        detections: dict[str, AnchorDetection],
        image: Image.Image,
        scale: float,
    ) -> bool:
        for constraint in alternative.constraints:
            left = self._constraint_value(constraint.left, detections, image)
            right = self._constraint_value(constraint.right, detections, image)
            if left is None or right is None:
                return False
            difference = left - right
            if not (
                constraint.min_difference * scale
                <= difference
                <= constraint.max_difference * scale
            ):
                return False
        return True

    def _derive_target(self, alternative: AlternativeSpec, detections: dict[str, AnchorDetection], image: Image.Image) -> Rect | None:
        if any(not detections[name].accepted or detections[name].bounds is None for name in alternative.anchors):
            return None
        scale = self._combination_scale(alternative, detections)
        if scale is None or not self._constraints_hold(
            alternative,
            detections,
            image,
            scale,
        ):
            return None
        values: dict[str, int] = {}
        for edge, rule in alternative.edges.items():
            bounds = detections[rule.anchor].bounds
            assert bounds is not None
            values[edge] = round(
                _reference(bounds, rule.reference) + rule.offset * scale
            )
        for adjustment in alternative.optional_adjustments:
            detection = detections[adjustment.anchor]
            if not detection.accepted or detection.bounds is None:
                continue
            candidate = round(
                _reference(detection.bounds, adjustment.reference)
                + adjustment.offset * scale
            )
            if adjustment.mode == "min":
                values[adjustment.edge] = min(values[adjustment.edge], candidate)
            elif adjustment.mode == "max":
                values[adjustment.edge] = max(values[adjustment.edge], candidate)
            else:
                values[adjustment.edge] = candidate
        try:
            target = Rect(values["left"], values["top"], values["right"], values["bottom"])
        except ValueError:
            return None
        if not (
            alternative.min_width * scale
            <= target.width
            <= alternative.max_width * scale
            and alternative.min_height * scale
            <= target.height
            <= alternative.max_height * scale
        ):
            return None
        if not (0 <= target.left < image.width and 0 < target.right <= image.width and 0 <= target.top < image.height and 0 < target.bottom <= image.height):
            return None
        return target

    @staticmethod
    def _derive_reference(
        alternative: AlternativeSpec,
        detections: dict[str, AnchorDetection],
        image: Image.Image,
    ) -> Rect | None:
        if alternative.reference_edges is None:
            return None
        if any(
            not detections[name].accepted or detections[name].bounds is None
            for name in alternative.anchors
        ):
            return None
        scale = RelativeLocator._combination_scale(alternative, detections)
        if scale is None:
            return None
        values: dict[str, int] = {}
        for edge, rule in alternative.reference_edges.items():
            bounds = detections[rule.anchor].bounds
            assert bounds is not None
            values[edge] = round(
                _reference(bounds, rule.reference) + rule.offset * scale
            )
        try:
            reference = Rect(
                values["left"],
                values["top"],
                values["right"],
                values["bottom"],
            )
        except ValueError:
            return None
        if not (
            0 <= reference.left < image.width
            and 0 < reference.right <= image.width
            and 0 <= reference.top < image.height
            and 0 < reference.bottom <= image.height
        ):
            return None
        return reference

    def _click_bounds(self, target: Rect) -> Rect:
        x_min, x_max = self.spec.click_x_range
        y_min, y_max = self.spec.click_y_range
        left = target.left + int(target.width * x_min)
        right = target.left + max(int(target.width * x_max), int(target.width * x_min) + 1)
        top = target.top + int(target.height * y_min)
        bottom = target.top + max(int(target.height * y_max), int(target.height * y_min) + 1)
        return Rect(
            max(target.left, left),
            max(target.top, top),
            min(target.right, right),
            min(target.bottom, bottom),
        )

    @staticmethod
    def _same_target(
        left: RelativeLocatorCombination,
        right: RelativeLocatorCombination,
    ) -> bool:
        return all(
            abs(a - b) <= 3
            for a, b in (
                (left.target.left, right.target.left),
                (left.target.top, right.target.top),
                (left.target.right, right.target.right),
                (left.target.bottom, right.target.bottom),
            )
        )

    def _result_from_candidates(
        self,
        image: Image.Image,
        anchor_candidates: dict[str, tuple[AnchorDetection, ...]],
        *,
        alternatives: tuple[AlternativeSpec, ...] | None = None,
    ) -> RelativeLocatorResult:
        selected_alternatives = alternatives or self.spec.alternatives
        best_detections = {
            anchor_id: values[0] if values else self._missing_detection(anchor_id)
            for anchor_id, values in anchor_candidates.items()
        }
        rejected: list[str] = []
        valid: list[RelativeLocatorCombination] = []
        alternatives_with_complete_candidates = 0
        combination_number = 0
        for alternative in selected_alternatives:
            required_lists = [anchor_candidates.get(name, ()) for name in alternative.anchors]
            if any(not values for values in required_lists):
                rejected.append(alternative.alternative_id)
                continue
            alternatives_with_complete_candidates += 1
            optional_ids = tuple(
                dict.fromkeys(item.anchor for item in alternative.optional_adjustments)
            )
            optional_lists: list[tuple[AnchorDetection, ...]] = []
            for anchor_id in optional_ids:
                values = anchor_candidates.get(anchor_id, ())
                optional_lists.append(
                    values if values else (self._missing_detection(anchor_id),)
                )
            alternative_valid = 0
            for chosen in product(*(required_lists + optional_lists)):
                detections = dict(best_detections)
                for anchor_id, detection in zip(
                    alternative.anchors + optional_ids,
                    chosen,
                ):
                    detections[anchor_id] = detection
                target = self._derive_target(alternative, detections, image)
                if target is None:
                    continue
                reference = self._derive_reference(alternative, detections, image) or target
                accepted_detections = [
                    detections[name]
                    for name in alternative.anchors + optional_ids
                    if detections[name].accepted
                ]
                combination_number += 1
                alternative_valid += 1
                valid.append(
                    RelativeLocatorCombination(
                        combination_id=f"combination_{combination_number}",
                        alternative_id=alternative.alternative_id,
                        used_anchor_ids=alternative.anchors + tuple(
                            anchor_id
                            for anchor_id in optional_ids
                            if detections[anchor_id].accepted
                        ),
                        detections=detections,
                        target=target,
                        click_bounds=self._click_bounds(target),
                        reference_bounds=reference,
                        score=(
                            sum(item.score for item in accepted_detections)
                            / len(accepted_detections)
                            if accepted_detections
                            else 0.0
                        ),
                    )
                )
            if not alternative_valid:
                rejected.append(alternative.alternative_id)

        if not valid:
            return RelativeLocatorResult(
                None,
                None,
                best_detections,
                tuple(rejected),
                anchor_candidates=anchor_candidates,
                failure_code=(
                    "anchor_candidates_missing"
                    if alternatives_with_complete_candidates == 0
                    else "no_valid_combination"
                ),
            )

        target_groups: list[list[RelativeLocatorCombination]] = []
        for combination in valid:
            group = next(
                (
                    items
                    for items in target_groups
                    if self._same_target(items[0], combination)
                ),
                None,
            )
            if group is None:
                target_groups.append([combination])
            else:
                group.append(combination)
        alternative_order = {
            item.alternative_id: index
            for index, item in enumerate(selected_alternatives)
        }
        representatives = tuple(
            max(items, key=lambda item: item.score)
            for items in target_groups
        )
        if len(target_groups) > 1:
            return RelativeLocatorResult(
                None,
                None,
                best_detections,
                tuple(rejected),
                anchor_candidates=anchor_candidates,
                valid_combinations=tuple(valid),
                distinct_combinations=representatives,
                failure_code="ambiguous_combinations",
            )

        chosen = min(
            target_groups[0],
            key=lambda item: (
                alternative_order.get(item.alternative_id, 999),
                -item.score,
            ),
        )
        return RelativeLocatorResult(
            chosen.alternative_id,
            chosen.target,
            chosen.detections,
            tuple(rejected),
            chosen.click_bounds,
            chosen.reference_bounds,
            anchor_candidates,
            tuple(valid),
            representatives,
            "",
        )

    def _result_from_detections(
        self,
        image: Image.Image,
        detections: dict[str, AnchorDetection],
        *,
        alternatives: tuple[AlternativeSpec, ...] | None = None,
    ) -> RelativeLocatorResult:
        return self._result_from_candidates(
            image,
            {
                anchor_id: (detection,) if detection.accepted else ()
                for anchor_id, detection in detections.items()
            },
            alternatives=alternatives,
        )

    def locate_near(
        self,
        image: Image.Image,
        cached: RelativeLocatorResult,
        *,
        padding: int = 10,
        skip_optional_anchors: bool = False,
    ) -> RelativeLocatorResult:
        """Revalidate cached anchors only inside small surrounding rectangles.

        A failed local check is deliberately returned as a rejected result;
        callers must then run ``locate`` on the same screenshot before using a
        click rectangle.
        """

        if padding < 0:
            raise ValueError("局部验证边距不能为负数。")
        preferred = next(
            (
                alternative
                for alternative in self.spec.alternatives
                if alternative.alternative_id == cached.alternative_id
            ),
            None,
        )
        empty = {
            anchor_id: self._missing_detection(anchor_id)
            for anchor_id in self.spec.anchors
        }
        if not cached.accepted or preferred is None:
            return RelativeLocatorResult(
                None,
                None,
                empty,
                ("cached_layout_invalid",),
                failure_code="cached_layout_invalid",
            )

        optional_anchors = {
            adjustment.anchor
            for alternative in self.spec.alternatives
            for adjustment in alternative.optional_adjustments
        }
        wanted = set(preferred.anchors)
        if not skip_optional_anchors:
            wanted.update(optional_anchors)

        scale_attempts: list[tuple[float, ...] | None] = [
            self._preferred_scale_factors
        ]
        if (
            self._fallback_scale_factors is not None
            and self._fallback_scale_factors != self._preferred_scale_factors
        ):
            scale_attempts.append(self._fallback_scale_factors)
        for attempt_index, scales in enumerate(scale_attempts):
            candidate_map: dict[str, tuple[AnchorDetection, ...]] = {
                anchor_id: () for anchor_id in self.spec.anchors
            }
            for anchor_id in wanted:
                previous = cached.detections.get(anchor_id)
                if previous is None or not previous.accepted or previous.bounds is None:

                    return RelativeLocatorResult(
                        None,
                        None,
                        empty,
                        (preferred.alternative_id,),
                        anchor_candidates=candidate_map,
                        failure_code="cached_layout_invalid",
                    )
                anchor = self.spec.anchors[anchor_id]
                max_width = max(
                    self._matcher_for(item, scales).template.width
                    for item in anchor.templates
                )
                max_height = max(
                    self._matcher_for(item, scales).template.height
                    for item in anchor.templates
                )
                roi = (
                    max(0, previous.bounds.left - padding),
                    max(0, previous.bounds.top - padding),
                    min(
                        image.width,
                        max(previous.bounds.right, previous.bounds.left + max_width)
                        + padding,
                    ),
                    min(
                        image.height,
                        max(previous.bounds.bottom, previous.bounds.top + max_height)
                        + padding,
                    ),
                )
                candidate_map[anchor_id] = self._candidates_for_anchor(
                    image,
                    anchor,
                    roi,
                    scales,
                )
            result = self._result_from_candidates(
                image,
                candidate_map,
                alternatives=(preferred,),
            )
            if (
                result.accepted
                or result.failure_code == "ambiguous_combinations"
                or attempt_index == len(scale_attempts) - 1
            ):
                return result
        raise AssertionError("scale attempts cannot be empty")

    def locate(
        self,
        image: Image.Image,
        *,
        skip_optional_anchors: bool = False,
    ) -> RelativeLocatorResult:
        optional_anchors = {
            adjustment.anchor
            for alternative in self.spec.alternatives
            for adjustment in alternative.optional_adjustments
        }
        scale_attempts: list[tuple[float, ...] | None] = [
            self._preferred_scale_factors
        ]
        if (
            self._fallback_scale_factors is not None
            and self._fallback_scale_factors != self._preferred_scale_factors
        ):
            scale_attempts.append(self._fallback_scale_factors)
        for attempt_index, scales in enumerate(scale_attempts):
            candidates = self.detect_anchor_candidates(
                image,
                skip_anchors=optional_anchors if skip_optional_anchors else None,
                scale_factors=scales,
            )
            result = self._result_from_candidates(image, candidates)
            if (
                result.accepted
                or result.failure_code == "ambiguous_combinations"
                or attempt_index == len(scale_attempts) - 1
            ):
                return result
        raise AssertionError("scale attempts cannot be empty")

def draw_debug_overlay(image: Image.Image, result: RelativeLocatorResult) -> Image.Image:
    """Draw every image candidate and every geometry-qualified target group."""

    output = image.convert("RGB").copy()
    draw = ImageDraw.Draw(output)
    colors = {
        "element1": "#d34a4a",
        "element2": "#3778c2",
        "element3": "#2a9d62",
        "element4_voice": "#8a55b5",
        "send_button": "#1b9e77",
        "emoji_button": "#3778c2",
    }
    candidate_map = result.anchor_candidates or {
        anchor_id: ((detection,) if detection.accepted else ())
        for anchor_id, detection in result.detections.items()
    }
    for anchor_id, candidates in candidate_map.items():
        color = colors.get(anchor_id, "#7a4ca3")
        for index, detection in enumerate(candidates, start=1):
            if detection.bounds is None:
                continue
            bounds = detection.bounds
            draw.rectangle(
                (bounds.left, bounds.top, bounds.right - 1, bounds.bottom - 1),
                outline=color,
                width=1,
            )
            draw.text(
                (bounds.left + 2, max(0, bounds.top - 12)),
                f"{anchor_id}#{index} {detection.score:.2f} @{detection.scale:.2f}x",
                fill=color,
            )
    combination_colors = (
        "#ef8c28",
        "#d84b76",
        "#7c5bd6",
        "#1d9bb8",
        "#bf6b20",
        "#3b7d3f",
    )
    for index, combination in enumerate(result.distinct_combinations, start=1):
        target = combination.target
        color = combination_colors[(index - 1) % len(combination_colors)]
        draw.rectangle(
            (target.left, target.top, target.right - 1, target.bottom - 1),
            outline=color,
            width=2,
        )
        draw.text(
            (target.left + 2, max(0, target.top - 24)),
            f"COMBO {index}/{combination.alternative_id}",
            fill=color,
        )
    if result.target is not None:
        target = result.target
        draw.rectangle((target.left, target.top, target.right - 1, target.bottom - 1), outline="#ef8c28", width=3)
        draw.text((target.left + 2, max(0, target.top - 24)), f"TARGET/{result.alternative_id}", fill="#ef8c28")
        center = target.center
        draw.ellipse((center.x - 2, center.y - 2, center.x + 2, center.y + 2), fill="#ef8c28")
    if result.reference_bounds is not None and result.reference_bounds != result.target:
        reference = result.reference_bounds
        draw.rectangle(
            (reference.left, reference.top, reference.right - 1, reference.bottom - 1),
            outline="#2f9ca6",
            width=1,
        )
        draw.text((reference.left + 2, reference.bottom + 1), "ORIGINAL", fill="#257d85")
    if result.click_bounds is not None:
        safe = result.click_bounds
        draw.rectangle(
            (safe.left, safe.top, safe.right - 1, safe.bottom - 1),
            outline="#d0a000",
            width=1,
        )
        draw.text((safe.left + 2, safe.bottom + 1), "CLICK SAFE", fill="#a47d00")
    return output

def draw_combination_overlay(
    image: Image.Image,
    combination: RelativeLocatorCombination,
) -> Image.Image:
    """Draw one qualified combination alone for human ambiguity review."""

    output = image.convert("RGB").copy()
    draw = ImageDraw.Draw(output)
    colors = ("#1b9e77", "#3778c2", "#8a55b5", "#d34a4a")
    accepted = [
        combination.detections[anchor_id]
        for anchor_id in combination.used_anchor_ids
        if combination.detections[anchor_id].accepted
        and combination.detections[anchor_id].bounds is not None
    ]
    for index, detection in enumerate(accepted):
        bounds = detection.bounds
        assert bounds is not None
        color = colors[index % len(colors)]
        draw.rectangle(
            (bounds.left, bounds.top, bounds.right - 1, bounds.bottom - 1),
            outline=color,
            width=2,
        )
        draw.text(
            (bounds.left + 2, max(0, bounds.top - 12)),
            f"{detection.anchor_id} {detection.score:.3f} @{detection.scale:.2f}x",
            fill=color,
        )
    target = combination.target
    draw.rectangle(
        (target.left, target.top, target.right - 1, target.bottom - 1),
        outline="#ef8c28",
        width=3,
    )
    draw.text(
        (target.left + 2, max(0, target.top - 24)),
        f"{combination.combination_id}/{combination.alternative_id}",
        fill="#ef8c28",
    )
    safe = combination.click_bounds
    draw.rectangle(
        (safe.left, safe.top, safe.right - 1, safe.bottom - 1),
        outline="#d0a000",
        width=1,
    )
    return output
