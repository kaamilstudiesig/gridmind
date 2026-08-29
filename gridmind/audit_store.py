"""
Durable SQLite persistence for GridMind Commander AuditRecords.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union


class AuditStore:
    """
    SQLite-backed audit trail store for GridMind incidents and operator actions.
    Provides ACID-compliant persistence, concurrent read safety for the dashboard,
    and typed queries by incident ID and status.
    """

    def __init__(self, db_path: Union[str, Path] = "gridmind_audit.db") -> None:
        self.db_path = str(db_path)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes the audit_records SQLite table and indexes."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_records (
                    incident_id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    recommended_action_type TEXT,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_status ON audit_records(status);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_scenario ON audit_records(scenario_id);
                """
            )
            conn.commit()

    def save(self, record: Union[dict[str, Any], Any]) -> dict[str, Any]:
        """
        Inserts or updates an AuditRecord in the database.
        Accepts either an AuditRecord instance (with .to_dict()) or a dict.
        """
        record_dict = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        incident_id = str(record_dict.get("incident_id", ""))
        scenario_id = str(record_dict.get("scenario_id", "UNKNOWN"))
        status = str(record_dict.get("status", "PENDING_APPROVAL"))

        rec_act = record_dict.get("recommended_action")
        rec_type = rec_act.get("action_type") if isinstance(rec_act, dict) else None

        now_iso = datetime.now(timezone.utc).isoformat()
        if "created_at" not in record_dict or not record_dict["created_at"]:
            record_dict["created_at"] = now_iso
        record_dict["updated_at"] = now_iso

        created_at = record_dict["created_at"]
        updated_at = record_dict["updated_at"]
        record_json = json.dumps(record_dict, ensure_ascii=False)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_records (
                    incident_id, scenario_id, status, recommended_action_type, record_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    scenario_id = excluded.scenario_id,
                    status = excluded.status,
                    recommended_action_type = excluded.recommended_action_type,
                    record_json = excluded.record_json,
                    updated_at = excluded.updated_at;
                """,
                (incident_id, scenario_id, status, rec_type, record_json, created_at, updated_at),
            )
            conn.commit()

        return record_dict

    def get(self, incident_id: str) -> Optional[dict[str, Any]]:
        """Retrieves an AuditRecord by incident_id."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT record_json FROM audit_records WHERE incident_id = ?",
                (incident_id,),
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row["record_json"])
        return None

    def list(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        """Lists AuditRecords, optionally filtered by status, ordered by updated_at DESC."""
        with self._get_connection() as conn:
            if status:
                cursor = conn.execute(
                    "SELECT record_json FROM audit_records WHERE status = ? ORDER BY updated_at DESC",
                    (str(status),),
                )
            else:
                cursor = conn.execute(
                    "SELECT record_json FROM audit_records ORDER BY updated_at DESC"
                )
            rows = cursor.fetchall()
            return [json.loads(r["record_json"]) for r in rows]

    def claim_for_execution(self, incident_id: str) -> bool:
        """
        Atomically transitions a record from PENDING_APPROVAL to a claimed state.
        Returns True if the claim succeeded (exactly one row transitioned).
        Returns False if the record was already claimed/executed by another operator.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE audit_records
                SET status = 'EXECUTING', updated_at = ?
                WHERE incident_id = ? AND status = 'PENDING_APPROVAL';
                """,
                (datetime.now(timezone.utc).isoformat(), incident_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    def clear(self) -> None:
        """Clears all records from the audit database (used in testing)."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM audit_records;")
            conn.commit()
