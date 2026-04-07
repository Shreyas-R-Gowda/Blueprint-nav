from __future__ import annotations

import contextlib
import io
import uuid
from pathlib import Path

import cv2
import numpy as np
from blueprint_parser import parse_blueprint as legacy_parse_blueprint
from grid_generator import generate_grid as legacy_generate_grid
from grid_generator import inflate_obstacles as legacy_inflate_obstacles
from grid_generator import visualise_grid as legacy_visualise_grid

from backend.app.models.schemas import (
    BoundingBox,
    OrientationRecord,
    ParseResult,
    ParseState,
    ParseWarning,
    Point,
    RoomDimensions,
    RoomRecord,
)
from backend.app.services.ocr import build_wall_mask, preprocess_image, run_ocr


def _estimate_room_bbox(room_center: tuple[int, int], image_shape: tuple[int, int]) -> BoundingBox:
    h, w = image_shape
    size = max(24, min(h, w) // 12)
    return BoundingBox(
        x=max(0, room_center[0] - size // 2),
        y=max(0, room_center[1] - size // 2),
        width=size,
        height=size,
    )


def _grid_cell_from_point(point: tuple[int, int], cell_size: int, rows: int, cols: int) -> tuple[int, int]:
    row = max(0, min(rows - 1, point[1] // cell_size))
    col = max(0, min(cols - 1, point[0] // cell_size))
    return row, col


def parse_blueprint_image(
    image: np.ndarray,
    image_path: Path,
    unit: str,
    wall_thickness: float,
    robot_width: float,
    robot_length: float,
    safety_margin: float,
    runtime_dir: Path,
) -> ParseResult:
    runtime_dir.mkdir(parents=True, exist_ok=True)

    gray = preprocess_image(image)
    wall_mask = build_wall_mask(gray)
    cv2.imwrite(str(runtime_dir / "preprocessed.png"), gray)
    cv2.imwrite(str(runtime_dir / "wall_mask.png"), wall_mask)

    debug_overlay = image.copy()
    for region in run_ocr(image):
        x, y, width, height = region.bounds
        cv2.rectangle(debug_overlay, (x, y), (x + width, y + height), (0, 180, 255), 2)
        cv2.putText(debug_overlay, region.text[:24], (x, max(0, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    cv2.imwrite(str(runtime_dir / "ocr_debug.png"), debug_overlay)

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            legacy_result = legacy_parse_blueprint(str(image_path))
    except Exception as exc:
        legacy_result = {
            "parse_status": "FAILED",
            "orientation": None,
            "rooms": [],
            "missing_fields": [str(exc)],
        }

    rooms: list[RoomRecord] = []
    for legacy_room in legacy_result.get("rooms", []):
        center = legacy_room.get("center", (0, 0))
        room = RoomRecord(
            name=legacy_room.get("name", "").strip(),
            confidence=float(legacy_room.get("confidence", 0.0)),
            center=Point(x=center[0], y=center[1]),
            doorway=Point(x=center[0], y=center[1]),
            bounding_box=_estimate_room_bbox(center, gray.shape),
        )
        if legacy_room.get("dimensions"):
            dims = legacy_room["dimensions"]
            room.dimensions = RoomDimensions(
                north_south=float(dims.get("north_south", 0.0)),
                east_west=float(dims.get("east_west", 0.0)),
                raw=dims.get("raw"),
                fixed=dims.get("fixed"),
            )
        rooms.append(room)

    orientation = None
    if legacy_result.get("orientation"):
        o = legacy_result["orientation"]
        center = o.get("center")
        orientation = OrientationRecord(
            direction=o.get("direction", "N"),
            confidence=float(o.get("confidence", 0.0)),
            center=Point(x=center[0], y=center[1]) if center else None,
            method=o.get("method"),
        )

    cell_size = 10
    occupancy_grid, grid_cols, grid_rows = legacy_generate_grid(str(image_path), cell_size=cell_size)
    inflated_grid = legacy_inflate_obstacles(
        occupancy_grid,
        robot_width_cells=max(1, int(round(robot_width / cell_size))),
        safety_margin_cells=max(1, int(round(safety_margin / cell_size))),
    )

    raw_grid_path = runtime_dir / "grid_raw.jpg"
    inflated_grid_path = runtime_dir / "grid_inflated.jpg"
    legacy_visualise_grid(occupancy_grid, str(raw_grid_path), str(image_path), cell_size=cell_size)
    legacy_visualise_grid(inflated_grid, str(inflated_grid_path), str(image_path), cell_size=cell_size)

    for room in rooms:
        row, col = _grid_cell_from_point((int(room.center.x), int(room.center.y)), cell_size, grid_rows, grid_cols)
        room.doorway = Point(x=col * cell_size + cell_size / 2, y=row * cell_size + cell_size / 2)

    missing_fields = list(legacy_result.get("missing_fields", []))
    warnings: list[ParseWarning] = [ParseWarning(message=message) for message in missing_fields]

    parse_state = ParseState.COMPLETE if legacy_result.get("parse_status") == "COMPLETE" else ParseState.PARSE_INCOMPLETE

    return ParseResult(
        parse_state=parse_state,
        unit=unit,
        orientation=orientation,
        rooms=rooms,
        occupancy_grid=np.asarray(occupancy_grid, dtype=np.uint8).tolist(),
        inflated_grid=np.asarray(inflated_grid, dtype=np.uint8).tolist(),
        grid_rows=grid_rows,
        grid_cols=grid_cols,
        bounding_box=BoundingBox(x=0, y=0, width=image.shape[1], height=image.shape[0]),
        warnings=warnings,
        missing_fields=missing_fields,
        wall_mask_image_name="wall_mask.png",
        ocr_debug_image_name="ocr_debug.png",
        raw_grid_image_name="grid_raw.jpg",
        inflated_grid_image_name="grid_inflated.jpg",
    )


def create_runtime_dir(base_dir: Path) -> Path:
    runtime_dir = base_dir / uuid.uuid4().hex
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir
