from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from backend.app.models.schemas import QueueItem, RobotStatusUpdate


def _load_local_env() -> None:
    env_files = [
        Path(__file__).resolve().parents[2] / ".env.local",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for env_file in env_files:
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


class FirebaseQueuePublisher:
    def __init__(self) -> None:
        self.enabled = False
        self.error: Optional[str] = None
        self._db = None
        self._initialize()

    def _initialize(self) -> None:
        _load_local_env()
        credentials_path = os.getenv("FIREBASE_CREDENTIALS")
        database_url = os.getenv("FIREBASE_DATABASE_URL")
        if not credentials_path or not database_url:
            self.error = "Firebase credentials are not configured."
            return

        try:
            import firebase_admin
            from firebase_admin import credentials, db

            if not firebase_admin._apps:
                firebase_admin.initialize_app(credentials.Certificate(credentials_path), {"databaseURL": database_url})

            self._db = db
            self.enabled = True
            self.error = None
        except Exception as exc:
            self.error = str(exc)
            self.enabled = False

    def publish_queue(self, robot_id: str, queue: list[QueueItem]) -> None:
        if not self.enabled or self._db is None:
            return
        payload = {
            str(item.sequence): {
                "command": item.command,
                "status": item.status.value,
                "progress": item.progress,
                "sequence": item.sequence,
            }
            for item in queue
        }
        self._db.reference(f"/robots/{robot_id}/queue").set(payload)
        self._db.reference(f"/robots/{robot_id}/meta").set({"active_sequence": 1 if queue else None})

    def cancel_pending(self, robot_id: str) -> None:
        if not self.enabled or self._db is None:
            return
        self._db.reference(f"/robots/{robot_id}/queue").set({})

    def publish_status(self, update: RobotStatusUpdate) -> None:
        if not self.enabled or self._db is None:
            return

        payload = {
            "current_command": update.current_command,
            "queue_status": update.queue_status.value if update.queue_status else None,
            "obstacle_state": update.obstacle_state,
            "progress": update.progress,
            "last_heartbeat": update.last_heartbeat,
        }
        if update.pose:
            payload["pose"] = update.pose.model_dump()
        self._db.reference(f"/robots/{update.robot_id}/status").update(payload)


firebase_queue = FirebaseQueuePublisher()
