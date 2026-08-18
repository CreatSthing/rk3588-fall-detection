from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional


class EventRepository:
    """Small SQLite-backed alarm store; every method returns JSON-ready dicts."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self._lock = threading.Lock()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alarm_events (
                    id TEXT PRIMARY KEY,
                    camera_id TEXT NOT NULL,
                    track_id INTEGER,
                    event_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    happened_at REAL NOT NULL,
                    ended_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    acknowledged_at REAL,
                    video_path TEXT,
                    recording_status TEXT NOT NULL,
                    recording_error TEXT,
                    details_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_alarm_events_time ON alarm_events(happened_at DESC)"
            )

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        value = dict(row)
        try:
            value["details"] = json.loads(value.pop("details_json") or "{}")
        except json.JSONDecodeError:
            value["details"] = {}
            value.pop("details_json", None)
        value["acknowledged"] = value.get("acknowledged_at") is not None
        value["video_ready"] = value.get("recording_status") == "ready" and bool(value.get("video_path"))
        return value

    def get(self, event_id: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM alarm_events WHERE id = ?", (event_id,)).fetchone()
        return self._row(row)

    def upsert(self, event: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        event_id = str(event["id"])
        state = str(event.get("state") or "confirmed")
        with self._lock, closing(self._connect()) as connection, connection:
            existing = connection.execute("SELECT id FROM alarm_events WHERE id = ?", (event_id,)).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE alarm_events
                    SET state = ?, ended_at = ?, updated_at = ?,
                        confidence = COALESCE(?, confidence), details_json = COALESCE(?, details_json)
                    WHERE id = ?
                    """,
                    (
                        state,
                        float(event.get("timestamp") or now) if state == "recovered" else None,
                        now,
                        float(event["confidence"]) if event.get("confidence") is not None else None,
                        json.dumps(event["details"], ensure_ascii=False) if event.get("details") is not None else None,
                        event_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO alarm_events (
                        id, camera_id, track_id, event_type, state, severity, confidence,
                        happened_at, created_at, updated_at, recording_status, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        str(event["camera_id"]),
                        event.get("track_id"),
                        str(event.get("event_type") or "fall"),
                        state,
                        str(event.get("severity") or "critical"),
                        float(event.get("confidence") or 0.0),
                        float(event.get("timestamp") or now),
                        now,
                        now,
                        str(event.get("recording_status") or "pending"),
                        json.dumps(event.get("details") or {}, ensure_ascii=False),
                    ),
                )
        result = self.get(event_id)
        assert result is not None
        return result

    def set_recording(
        self,
        event_id: str,
        status: str,
        video_path: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE alarm_events
                SET recording_status = ?, video_path = COALESCE(?, video_path),
                    recording_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, video_path, error, time.time(), event_id),
            )
        result = self.get(event_id)
        if result is None:
            raise KeyError(event_id)
        return result

    def acknowledge(self, event_id: str) -> Dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE alarm_events SET acknowledged_at = ?, updated_at = ? WHERE id = ?",
                (time.time(), time.time(), event_id),
            )
        result = self.get(event_id)
        if result is None:
            raise KeyError(event_id)
        return result

    def delete(self, event_id: str) -> Dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT * FROM alarm_events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                raise KeyError(event_id)
            connection.execute("DELETE FROM alarm_events WHERE id = ?", (event_id,))
        result = self._row(row)
        assert result is not None
        return result

    def list(self, limit: int = 100, camera_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM alarm_events"
        params: List[Any] = []
        if camera_id:
            query += " WHERE camera_id = ?"
            params.append(camera_id)
        query += " ORDER BY happened_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row(row) for row in rows if row is not None]

    def incomplete_recordings(self) -> List[Dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM alarm_events WHERE recording_status IN ('pending', 'recording')"
            ).fetchall()
        return [self._row(row) for row in rows if row is not None]
