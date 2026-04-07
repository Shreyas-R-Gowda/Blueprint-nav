from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ParseState(str, Enum):
    COMPLETE = "complete"
    PARSE_INCOMPLETE = "parse_incomplete"
    FAILED = "failed"


class QueueState(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class Point(BaseModel):
    x: float
    y: float


class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int


class RoomDimensions(BaseModel):
    north_south: float
    east_west: float
    raw: Optional[str] = None
    fixed: Optional[str] = None


class RoomRecord(BaseModel):
    name: str
    confidence: float = 0.0
    center: Point
    doorway: Optional[Point] = None
    dimensions: Optional[RoomDimensions] = None
    bounding_box: Optional[BoundingBox] = None


class OrientationRecord(BaseModel):
    direction: str
    confidence: float = 0.0
    center: Optional[Point] = None
    method: Optional[str] = None


class ParseWarning(BaseModel):
    message: str


class ParseResult(BaseModel):
    parse_state: ParseState
    unit: str
    orientation: Optional[OrientationRecord] = None
    rooms: List[RoomRecord] = Field(default_factory=list)
    occupancy_grid: List[List[int]] = Field(default_factory=list)
    inflated_grid: List[List[int]] = Field(default_factory=list)
    grid_rows: int = 0
    grid_cols: int = 0
    bounding_box: Optional[BoundingBox] = None
    warnings: List[ParseWarning] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    original_image_name: Optional[str] = None
    overlay_image_name: Optional[str] = None
    wall_mask_image_name: Optional[str] = None
    ocr_debug_image_name: Optional[str] = None
    raw_grid_image_name: Optional[str] = None
    inflated_grid_image_name: Optional[str] = None


class RobotPose(BaseModel):
    x: float
    y: float
    heading: str = "N"


class QueueItem(BaseModel):
    sequence: int
    command: str
    status: QueueState = QueueState.PENDING
    progress: float = 0.0


class NavigationRequest(BaseModel):
    session_id: str
    room: str


class NavigationResponse(BaseModel):
    session_id: str
    target_room: str
    path_cells: List[List[int]]
    directions: List[str]
    commands: List[str]
    queue: List[QueueItem]
    overlay_image_name: Optional[str] = None


class ManualCommandRequest(BaseModel):
    session_id: str
    commands: List[str] = Field(default_factory=list)


class RobotStatusUpdate(BaseModel):
    session_id: Optional[str] = None
    robot_id: str
    current_command: Optional[str] = None
    queue_status: Optional[QueueState] = None
    obstacle_state: Optional[str] = None
    progress: Optional[float] = None
    pose: Optional[RobotPose] = None
    last_heartbeat: Optional[str] = None


class SessionRecord(BaseModel):
    session_id: str
    robot_id: str
    unit: str
    parse_result: ParseResult
    current_pose: Optional[RobotPose] = None
    initial_pose: Optional[RobotPose] = None
    latest_path: List[List[int]] = Field(default_factory=list)
    latest_directions: List[str] = Field(default_factory=list)
    latest_commands: List[str] = Field(default_factory=list)
    queue: List[QueueItem] = Field(default_factory=list)
    queue_state: Optional[QueueState] = None
    created_at: str


class ParseBlueprintResponse(BaseModel):
    session: SessionRecord
    firebase_enabled: bool


class HealthResponse(BaseModel):
    status: str
    firebase_enabled: bool
    firebase_error: Optional[str] = None
