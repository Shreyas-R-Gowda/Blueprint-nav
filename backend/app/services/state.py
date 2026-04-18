from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict
from uuid import uuid4

from backend.app.models.schemas import ParseResult, QueueItem, QueueState, RobotPose, SessionRecord


class SessionState:
    def __init__(self) -> None:
        self._sessions: Dict[str, SessionRecord] = {}

    def create_session(self, robot_id: str, unit: str, robot_width: float, robot_length: float, parse_result: ParseResult) -> SessionRecord:
        session_id = uuid4().hex
        session = SessionRecord(
            session_id=session_id,
            robot_id=robot_id,
            unit=unit,
            robot_width=robot_width,
            robot_length=robot_length,
            parse_result=parse_result,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> SessionRecord:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session: {session_id}")
        return self._sessions[session_id]

    def update_pose(self, session_id: str, pose: RobotPose) -> SessionRecord:
        session = self.get(session_id)
        if session.initial_pose is None:
            session.initial_pose = pose
        session.current_pose = pose
        return session

    def replace_queue(self, session_id: str, queue: list[QueueItem], commands: list[str], directions: list[str]) -> SessionRecord:
        session = self.get(session_id)
        session.queue = queue
        session.latest_commands = commands
        session.latest_directions = directions
        session.queue_state = queue[0].status if queue else QueueState.CANCELLED
        return session

    def update_path(self, session_id: str, path: list[list[int]]) -> SessionRecord:
        session = self.get(session_id)
        session.latest_path = path
        return session

    def update_robot_status(self, session_id: str, queue_state: QueueState | None, pose: RobotPose | None) -> SessionRecord:
        session = self.get(session_id)
        if queue_state is not None:
            session.queue_state = queue_state
        if pose is not None:
            session.current_pose = pose
        return session


session_state = SessionState()
