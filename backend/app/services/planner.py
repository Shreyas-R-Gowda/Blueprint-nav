from __future__ import annotations

import heapq
from pathlib import Path

import cv2
import numpy as np

from backend.app.models.schemas import NavigationResponse, Point, QueueItem, QueueState, RobotPose, RoomRecord


def _heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def astar(grid: np.ndarray, start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
    rows, cols = grid.shape
    queue: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score = {start: 0.0}

    while queue:
        _, current = heapq.heappop(queue)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))

        for row, col in [
            (current[0] - 1, current[1]),
            (current[0] + 1, current[1]),
            (current[0], current[1] - 1),
            (current[0], current[1] + 1),
            (current[0] - 1, current[1] - 1),
            (current[0] - 1, current[1] + 1),
            (current[0] + 1, current[1] - 1),
            (current[0] + 1, current[1] + 1),
        ]:
            if row < 0 or row >= rows or col < 0 or col >= cols or grid[row, col] == 1:
                continue
            diagonal = abs(row - current[0]) + abs(col - current[1]) == 2
            candidate = g_score[current] + (1.4 if diagonal else 1.0)
            if candidate < g_score.get((row, col), float("inf")):
                came_from[(row, col)] = current
                g_score[(row, col)] = candidate
                heapq.heappush(queue, (candidate + _heuristic((row, col), goal), (row, col)))

    return []


def smooth_path(path: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if len(path) < 3:
        return path

    smoothed = [path[0]]
    last_delta = None
    for idx in range(1, len(path)):
        delta = (path[idx][0] - path[idx - 1][0], path[idx][1] - path[idx - 1][1])
        if last_delta is not None and delta != last_delta:
            smoothed.append(path[idx - 1])
        last_delta = delta
    smoothed.append(path[-1])
    return smoothed


def nearest_open_cell(grid: np.ndarray, start: tuple[int, int]) -> tuple[int, int]:
    if grid[start[0], start[1]] == 0:
        return start

    rows, cols = grid.shape
    for radius in range(1, 25):
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                row = start[0] + dr
                col = start[1] + dc
                if 0 <= row < rows and 0 <= col < cols and grid[row, col] == 0:
                    return row, col
    return start


def pose_to_grid(pose: RobotPose, cell_size: float) -> tuple[int, int]:
    return max(0, int(pose.y // cell_size)), max(0, int(pose.x // cell_size))


def room_to_grid(room: RoomRecord, cell_size: float) -> tuple[int, int]:
    doorway = room.doorway or Point(x=room.center.x, y=room.center.y)
    return max(0, int(doorway.y // cell_size)), max(0, int(doorway.x // cell_size))


def _heading_for_step(step: tuple[int, int]) -> str:
    return {
        (-1, 0): "N",
        (1, 0): "S",
        (0, 1): "E",
        (0, -1): "W",
        (-1, 1): "NE",
        (-1, -1): "NW",
        (1, 1): "SE",
        (1, -1): "SW",
    }[step]


def _heading_angle(heading: str) -> float:
    return {
        "N": 0.0,
        "NE": 45.0,
        "E": 90.0,
        "SE": 135.0,
        "S": 180.0,
        "SW": 225.0,
        "W": 270.0,
        "NW": 315.0,
    }[heading]


def build_directions_and_commands(path: list[tuple[int, int]], initial_heading: str, cell_size_cm: float, distance_scale_factor: float) -> tuple[list[str], list[str]]:
    if len(path) < 2:
        return [], []

    directions: list[str] = []
    commands: list[str] = []
    current_heading = initial_heading

    for idx in range(1, len(path)):
        delta = (path[idx][0] - path[idx - 1][0], path[idx][1] - path[idx - 1][1])
        step_heading = _heading_for_step(delta)
        angle_delta = (_heading_angle(step_heading) - _heading_angle(current_heading if current_heading in {"N", "E", "S", "W"} else "N") + 360.0) % 360.0
        if angle_delta:
            if angle_delta > 180.0:
                turn = int(360.0 - angle_delta)
                directions.append(f"Turn left {turn}°")
                commands.append(f"L{turn}")
            else:
                turn = int(angle_delta)
                directions.append(f"Turn right {turn}°")
                commands.append(f"R{turn}")

        diagonal = abs(delta[0]) + abs(delta[1]) == 2
        distance = max(1, int(round(cell_size_cm * (1.4142 if diagonal else 1.0) * distance_scale_factor)))
        directions.append(f"Move forward {distance}cm")
        commands.append(f"F{distance}cm")
        if step_heading in {"N", "E", "S", "W"}:
            current_heading = step_heading

    return directions, commands


def build_manual_queue(commands: list[str]) -> list[QueueItem]:
    return [QueueItem(sequence=index + 1, command=command, status=QueueState.PENDING) for index, command in enumerate(commands)]


def render_overlay(source_image: np.ndarray, grid: np.ndarray, path: list[tuple[int, int]], output_path: Path, cell_size: int = 10) -> str:
    overlay = source_image.copy()
    for row in range(grid.shape[0]):
        for col in range(grid.shape[1]):
            if grid[row, col] == 1:
                cv2.rectangle(overlay, (col * cell_size, row * cell_size), ((col + 1) * cell_size, (row + 1) * cell_size), (0, 0, 180), -1)

    result = cv2.addWeighted(overlay, 0.28, source_image, 0.72, 0)
    for row, col in path:
        cv2.circle(result, (int(col * cell_size + cell_size / 2), int(row * cell_size + cell_size / 2)), 3, (0, 220, 0), -1)

    cv2.imwrite(str(output_path), result)
    return output_path.name


def build_navigation_response(session_id: str, room: RoomRecord, path: list[tuple[int, int]], directions: list[str], commands: list[str], overlay_image_name: str | None) -> NavigationResponse:
    return NavigationResponse(
        session_id=session_id,
        target_room=room.name,
        path_cells=[[row, col] for row, col in path],
        directions=directions,
        commands=commands,
        queue=build_manual_queue(commands),
        overlay_image_name=overlay_image_name,
    )
