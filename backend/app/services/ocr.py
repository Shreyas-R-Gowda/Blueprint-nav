from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import cv2
import easyocr
import numpy as np


@dataclass
class OCRTextRegion:
    text: str
    confidence: float
    polygon: list[list[int]]
    center: tuple[int, int]
    bounds: tuple[int, int, int, int]


def decode_image_bytes(image_bytes: bytes) -> np.ndarray:
    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unable to decode image bytes")
    return image


def preprocess_image(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def build_wall_mask(gray: np.ndarray) -> np.ndarray:
    _, mask = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask


def _polygon_center(polygon: Sequence[Sequence[float]]) -> tuple[int, int]:
    points = np.array(polygon)
    return int(points[:, 0].mean()), int(points[:, 1].mean())


def _polygon_bounds(polygon: Sequence[Sequence[float]]) -> tuple[int, int, int, int]:
    points = np.array(polygon)
    min_x = int(points[:, 0].min())
    min_y = int(points[:, 1].min())
    max_x = int(points[:, 0].max())
    max_y = int(points[:, 1].max())
    return min_x, min_y, max_x - min_x, max_y - min_y


def run_ocr(image: np.ndarray) -> List[OCRTextRegion]:
    reader = easyocr.Reader(["en"], verbose=False)
    raw_results = reader.readtext(image)
    regions: List[OCRTextRegion] = []

    for polygon, text, confidence in raw_results:
        regions.append(
            OCRTextRegion(
                text=text.strip(),
                confidence=float(confidence),
                polygon=[[int(p[0]), int(p[1])] for p in polygon],
                center=_polygon_center(polygon),
                bounds=_polygon_bounds(polygon),
            )
        )

    if regions:
        return regions

    try:
        import pytesseract

        tess = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        for idx, text in enumerate(tess["text"]):
            text = text.strip()
            if not text:
                continue
            x = int(tess["left"][idx])
            y = int(tess["top"][idx])
            w = int(tess["width"][idx])
            h = int(tess["height"][idx])
            conf = max(0.0, float(tess["conf"][idx]) / 100.0)
            regions.append(
                OCRTextRegion(
                    text=text,
                    confidence=conf,
                    polygon=[[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                    center=(x + w // 2, y + h // 2),
                    bounds=(x, y, w, h),
                )
            )
    except Exception:
        pass

    return regions
