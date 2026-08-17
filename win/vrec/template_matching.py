

"""OpenCV template matching for visible desktop controls.

The matcher uses normalized correlation instead of treating every background
pixel as equally useful evidence.  It still returns multiple spatially
distinct candidates so the relative locator can validate anchor pairs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .models import Rect

@dataclass(frozen=True)
class TemplateMatch:
    """Best template candidate and the evidence needed to accept it."""

    best_bounds: Rect | None
    score: float
    second_score: float
    accepted: bool
    scale: float = 1.0

    @property
    def margin(self) -> float:
        return self.score - self.second_score

@dataclass(frozen=True)
class TemplateCandidate:
    """One spatially distinct template hit above the absolute score floor."""

    bounds: Rect
    score: float
    scale: float = 1.0

class OpenCVTemplateMatcher:
    """Match one template at explicitly allowed display scales."""

    def __init__(
        self,
        template: str | Path | Image.Image,
        *,
        coarse_step: int = 4,
        minimum_score: float = 0.86,
        minimum_margin: float = 0.015,
        scale_factors: tuple[float, ...] | list[float] | None = None,
    ) -> None:
        if coarse_step < 1:
            raise ValueError("模板匹配步长必须至少为 1。")
        if not 0.0 <= minimum_score <= 1.0:
            raise ValueError("模板最低匹配分数必须位于 0 到 1 之间。")
        if minimum_margin < 0.0:
            raise ValueError("模板候选差值不能为负数。")
        requested_scales = tuple(scale_factors or (1.0,))
        if not requested_scales:
            raise ValueError("模板缩放比例不能为空。")
        normalised_scales: list[float] = []
        for value in requested_scales:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("模板缩放比例必须是数字。")
            scale = float(value)
            if not 0.5 <= scale <= 3.0:
                raise ValueError("模板缩放比例必须位于 0.5 到 3.0 之间。")
            if scale not in normalised_scales:
                normalised_scales.append(scale)
        if isinstance(template, Image.Image):
            image = template.copy()
        else:
            with Image.open(Path(template)) as opened:
                image = opened.copy()
        if image.width < 2 or image.height < 2:
            raise ValueError("模板尺寸太小，无法进行可靠匹配。")

        self.template = ImageOps.grayscale(image.convert("RGB"))
        self._template_array = np.asarray(self.template, dtype=np.uint8)

        self.coarse_step = coarse_step
        self.minimum_score = minimum_score
        self.minimum_margin = minimum_margin
        self.scale_factors = tuple(normalised_scales)

    def _template_at_scale(self, scale: float) -> np.ndarray:
        width = max(2, round(self.template.width * scale))
        height = max(2, round(self.template.height * scale))
        if width == self.template.width and height == self.template.height:
            return self._template_array
        return cv2.resize(
            self._template_array,
            (width, height),
            interpolation=cv2.INTER_LANCZOS4,
        )

    @staticmethod
    def _normalise_roi(
        image: Image.Image,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int]:
        if roi is None:
            return (0, 0, image.width, image.height)
        left, top, right, bottom = roi
        return (
            max(0, int(left)),
            max(0, int(top)),
            min(image.width, int(right)),
            min(image.height, int(bottom)),
        )

    def _search_array(
        self,
        image: Image.Image,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[np.ndarray | None, int, int]:
        source = ImageOps.grayscale(image.convert("RGB"))
        left, top, right, bottom = self._normalise_roi(source, roi)
        if right <= left or bottom <= top:
            return None, left, top
        source_array = np.asarray(source, dtype=np.uint8)
        return source_array[top:bottom, left:right], left, top

    @staticmethod
    def _response_map(
        search: np.ndarray,
        template: np.ndarray,
    ) -> np.ndarray | None:
        template_height, template_width = template.shape
        if (
            search.shape[1] < template_width
            or search.shape[0] < template_height
        ):
            return None
        if float(np.std(template)) < 1e-6:
            difference = cv2.matchTemplate(
                search,
                template,
                cv2.TM_SQDIFF_NORMED,
            )
            response = 1.0 - difference
        else:
            response = cv2.matchTemplate(
                search,
                template,
                cv2.TM_CCOEFF_NORMED,
            )
        response = np.asarray(response, dtype=np.float32)
        np.nan_to_num(response, copy=False, nan=-1.0, posinf=-1.0, neginf=-1.0)
        return response

    def _extract_peaks(
        self,
        response: np.ndarray,
        *,
        offset_x: int,
        offset_y: int,
        max_candidates: int,
        score_floor: float,
        template_width: int,
        template_height: int,
        scale: float,
    ) -> tuple[TemplateCandidate, ...]:
        """Extract non-overlapping local maxima from an OpenCV response map."""

        working = response.copy()
        found: list[TemplateCandidate] = []
        while len(found) < max_candidates and working.size:
            _minimum, maximum, _minimum_point, maximum_point = cv2.minMaxLoc(working)
            score = max(0.0, min(1.0, float(maximum)))
            if score < score_floor:
                break
            local_x, local_y = maximum_point
            left = offset_x + local_x
            top = offset_y + local_y
            found.append(
                TemplateCandidate(
                    Rect(
                        left,
                        top,
                        left + template_width,
                        top + template_height,
                    ),
                    score,
                    scale,
                )
            )

            suppress_left = max(0, local_x - template_width + 1)
            suppress_top = max(0, local_y - template_height + 1)
            suppress_right = min(working.shape[1], local_x + template_width)
            suppress_bottom = min(working.shape[0], local_y + template_height)
            working[
                suppress_top:suppress_bottom,
                suppress_left:suppress_right,
            ] = -1.0
        return tuple(found)

    @staticmethod
    def _same_spatial_hit(
        left: TemplateCandidate,
        right: TemplateCandidate,
    ) -> bool:
        left_center = left.bounds.center
        right_center = right.bounds.center
        return (
            abs(left_center.x - right_center.x)
            <= max(3, min(left.bounds.width, right.bounds.width) // 3)
            and abs(left_center.y - right_center.y)
            <= max(3, min(left.bounds.height, right.bounds.height) // 3)
        )

    def _find_spatial_candidates(
        self,
        image: Image.Image,
        *,
        roi: tuple[int, int, int, int] | None,
        max_candidates: int,
        score_floor: float,
    ) -> tuple[TemplateCandidate, ...]:
        search, offset_x, offset_y = self._search_array(image, roi)
        if search is None:
            return ()

        found: list[TemplateCandidate] = []
        per_scale_limit = max(4, max_candidates * 2)
        for scale in self.scale_factors:
            template = self._template_at_scale(scale)
            response = self._response_map(search, template)
            if response is None:
                continue
            for candidate in self._extract_peaks(
                response,
                offset_x=offset_x,
                offset_y=offset_y,
                max_candidates=per_scale_limit,
                score_floor=score_floor,
                template_width=template.shape[1],
                template_height=template.shape[0],
                scale=scale,
            ):
                duplicate = next(
                    (
                        index
                        for index, existing in enumerate(found)
                        if self._same_spatial_hit(existing, candidate)
                    ),
                    None,
                )
                if duplicate is None:
                    found.append(candidate)
                elif candidate.score > found[duplicate].score:
                    found[duplicate] = candidate
        found.sort(key=lambda item: item.score, reverse=True)
        return tuple(found[:max_candidates])

    def match(
        self,
        image: Image.Image,
        *,
        roi: tuple[int, int, int, int] | None = None,
    ) -> TemplateMatch:
        peaks = self._find_spatial_candidates(
            image,
            roi=roi,
            max_candidates=2,
            score_floor=0.0,
        )
        if not peaks:
            return TemplateMatch(None, 0.0, 0.0, False)

        best = peaks[0]
        second_score = peaks[1].score if len(peaks) > 1 else 0.0
        accepted = (
            best.score >= self.minimum_score
            and best.score - second_score >= self.minimum_margin
        )
        return TemplateMatch(
            best.bounds if accepted else None,
            best.score,
            second_score,
            accepted,
            best.scale,
        )

    def find_candidates(
        self,
        image: Image.Image,
        *,
        roi: tuple[int, int, int, int] | None = None,
        max_candidates: int = 8,
    ) -> tuple[TemplateCandidate, ...]:
        """Return NMS-filtered score-qualified hits for anchor pairing."""

        if max_candidates < 1:
            raise ValueError("候选数量上限必须至少为 1。")
        return self._find_spatial_candidates(
            image,
            roi=roi,
            max_candidates=max_candidates,
            score_floor=self.minimum_score,
        )

PillowTemplateMatcher = OpenCVTemplateMatcher
