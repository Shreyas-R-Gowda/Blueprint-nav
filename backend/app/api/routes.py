from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.app.models.schemas import (
    HealthResponse,
    ManualCommandRequest,
    NavigationRequest,
    ParseBlueprintResponse,
    ParseState,
    QueueState,
    RobotPose,
    RobotStatusUpdate,
)
from backend.app.services.firebase_queue import firebase_queue
from backend.app.services.parser import create_runtime_dir, parse_blueprint_image
from backend.app.services.planner import (
    astar,
    build_directions_and_commands,
    build_manual_queue,
    build_navigation_response,
    nearest_open_cell,
    pose_to_grid,
    render_overlay,
    room_to_grid,
    smooth_path,
)
from backend.app.services.state import session_state


router = APIRouter()
RUNTIME_ROOT = Path(__file__).resolve().parents[3] / "runtime"
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)


def _best_goal_for_room(grid: np.ndarray, room, cell_size: float) -> tuple[int, int]:
    candidates: list[tuple[int, int]] = []
    doorway = room.doorway or room.center
    candidates.append(room_to_grid(room, cell_size))
    candidates.append((max(0, int(room.center.y // cell_size)), max(0, int(room.center.x // cell_size))))

    if room.bounding_box is not None:
        left = room.bounding_box.x // cell_size
        right = (room.bounding_box.x + room.bounding_box.width) // cell_size
        top = room.bounding_box.y // cell_size
        bottom = (room.bounding_box.y + room.bounding_box.height) // cell_size
        candidates.extend(
            [
                (int(doorway.y // cell_size), max(0, int(left) - 1)),
                (int(doorway.y // cell_size), min(grid.shape[1] - 1, int(right) + 1)),
                (max(0, int(top) - 1), int(doorway.x // cell_size)),
                (min(grid.shape[0] - 1, int(bottom) + 1), int(doorway.x // cell_size)),
            ]
        )

    seen: set[tuple[int, int]] = set()
    for row, col in candidates:
        row = min(max(0, row), grid.shape[0] - 1)
        col = min(max(0, col), grid.shape[1] - 1)
        cell = (row, col)
        if cell in seen:
            continue
        seen.add(cell)
        open_cell = nearest_open_cell(grid, cell)
        if grid[open_cell[0], open_cell[1]] == 0:
            return open_cell

    return nearest_open_cell(grid, candidates[0] if candidates else (0, 0))


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", firebase_enabled=firebase_queue.enabled, firebase_error=firebase_queue.error)


@router.post("/blueprints/parse", response_model=ParseBlueprintResponse)
async def parse_blueprint(
    blueprint: UploadFile = File(...),
    unit: str = Form("cm"),
    wall_thickness: float = Form(10.0),
    robot_width: float = Form(18.0),
    robot_length: float = Form(20.0),
    safety_margin: float = Form(5.0),
    distance_scale_factor: float = Form(1.0),
    robot_id: str = Form("robot-1"),
) -> ParseBlueprintResponse:
    image_bytes = await blueprint.read()
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Unable to decode uploaded image.")

    runtime_dir = create_runtime_dir(RUNTIME_ROOT)
    original_name = blueprint.filename or "blueprint.jpg"
    with (runtime_dir / original_name).open("wb") as handle:
        handle.write(image_bytes)

    parse_result = parse_blueprint_image(
        image,
        runtime_dir / original_name,
        unit,
        wall_thickness,
        robot_width,
        robot_length,
        safety_margin,
        runtime_dir,
    )
    parse_result.original_image_name = original_name
    session = session_state.create_session(
        robot_id=robot_id,
        unit=unit,
        robot_width=robot_width,
        robot_length=robot_length,
        parse_result=parse_result,
    )
    session_runtime_dir = RUNTIME_ROOT / session.session_id
    if runtime_dir != session_runtime_dir:
        if session_runtime_dir.exists():
            for child in session_runtime_dir.iterdir():
                child.unlink()
            session_runtime_dir.rmdir()
        runtime_dir.rename(session_runtime_dir)
    session.latest_commands = [f"SCALE={distance_scale_factor}"]
    return ParseBlueprintResponse(session=session, firebase_enabled=firebase_queue.enabled)


@router.post("/pose")
def set_pose(session_id: str, x: float, y: float, heading: str = "N"):
    return session_state.update_pose(session_id, RobotPose(x=x, y=y, heading=heading))


@router.post("/navigate")
def navigate(request: NavigationRequest):
    session = session_state.get(request.session_id)
    if session.parse_result.parse_state == ParseState.FAILED:
        raise HTTPException(status_code=400, detail="Blueprint parsing failed.")
    if session.current_pose is None:
        raise HTTPException(status_code=400, detail="Set the robot pose first.")

    room = next((candidate for candidate in session.parse_result.rooms if request.room.lower() in candidate.name.lower()), None)
    if room is None:
        raise HTTPException(status_code=404, detail="Target room not found.")

    inflated_grid = np.array(session.parse_result.inflated_grid or session.parse_result.occupancy_grid, dtype=np.uint8)
    raw_grid = np.array(session.parse_result.occupancy_grid, dtype=np.uint8)

    start = nearest_open_cell(inflated_grid, pose_to_grid(session.current_pose, 10.0))
    goal = _best_goal_for_room(inflated_grid, room, 10.0)
    path = astar(inflated_grid, start, goal)

    # Fall back to the raw grid if the inflated map is too conservative.
    render_grid = inflated_grid
    if not path:
        start = nearest_open_cell(raw_grid, pose_to_grid(session.current_pose, 10.0))
        goal = _best_goal_for_room(raw_grid, room, 10.0)
        path = astar(raw_grid, start, goal)
        render_grid = raw_grid

    if not path:
        raise HTTPException(status_code=400, detail="No path found.")

    smoothed = smooth_path(path)
    directions, commands = build_directions_and_commands(smoothed, session.current_pose.heading, 10.0, 1.0)

    runtime_dir = RUNTIME_ROOT / session.session_id
    overlay_name = None
    image_path = runtime_dir / (session.parse_result.original_image_name or "")
    if image_path.exists():
        image = cv2.imread(str(image_path))
        if image is not None:
            cell_size_px = max(1, min(image.shape[1] // render_grid.shape[1], image.shape[0] // render_grid.shape[0]))
            overlay_name = render_overlay(
                image,
                render_grid,
                smoothed,
                runtime_dir / "overlay.png",
                cell_size=cell_size_px,
                start=start,
                goal=goal,
                pose=session.current_pose,
                robot_width=session.robot_width,
                robot_length=session.robot_length,
            )

    response = build_navigation_response(session.session_id, room, smoothed, directions, commands, overlay_name)
    session_state.replace_queue(session.session_id, response.queue, response.commands, response.directions)
    session_state.update_path(session.session_id, response.path_cells)
    if overlay_name:
        session.parse_result.overlay_image_name = overlay_name
    if firebase_queue.enabled:
        try:
            firebase_queue.publish_queue(session.robot_id, response.queue)
        except Exception:
            pass
    return response


@router.post("/manual")
def manual_override(request: ManualCommandRequest):
    session = session_state.get(request.session_id)
    queue = build_manual_queue(request.commands)
    session_state.replace_queue(session.session_id, queue, request.commands, [])
    if firebase_queue.enabled:
        try:
            firebase_queue.cancel_pending(session.robot_id)
            firebase_queue.publish_queue(session.robot_id, queue)
        except Exception:
            pass
    return session


@router.post("/robot/status")
def update_robot_status(update: RobotStatusUpdate):
    if update.session_id:
        session_state.update_robot_status(update.session_id, update.queue_status, update.pose)
    if firebase_queue.enabled:
        try:
            firebase_queue.publish_status(update)
        except Exception:
            pass
    return {"ok": True}


@router.post("/robot/simulate/advance")
def simulate_advance(session_id: str):
    session = session_state.get(session_id)
    for item in session.queue:
        if item.status in {QueueState.PENDING, QueueState.BLOCKED, QueueState.IN_PROGRESS}:
            item.status = QueueState.DONE
            item.progress = 1.0
            break
    session.queue_state = QueueState.DONE if session.queue and all(item.status == QueueState.DONE for item in session.queue) else QueueState.IN_PROGRESS
    return session


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    return session_state.get(session_id)


@router.get("/sessions/{session_id}/image/{image_name}")
def get_image(session_id: str, image_name: str):
    candidate = RUNTIME_ROOT / session_id / image_name
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(candidate)
