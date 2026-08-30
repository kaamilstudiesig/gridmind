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
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = FULL;")
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

    def get_latest(self, scenario_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Retrieves the most recent AuditRecord, optionally scoped to a scenario_id."""
        query = "SELECT record_json FROM audit_records"
        params: list[Any] = []
        if scenario_id:
            query += " WHERE scenario_id = ?"
            params.append(str(scenario_id))
        query += " ORDER BY updated_at DESC LIMIT 1"
        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            if row:
                return json.loads(row["record_json"])
        return None

    def get_pending_for_scenario(self, scenario_id: str) -> Optional[dict[str, Any]]:
        """Retrieves the most recent PENDING_APPROVAL AuditRecord for a specific scenario."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT record_json FROM audit_records WHERE scenario_id = ? AND status = 'PENDING_APPROVAL' ORDER BY updated_at DESC LIMIT 1",
                (str(scenario_id),),
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row["record_json"])
        return None

    def count(self, scenario_id: Optional[str] = None, status: Optional[str] = None) -> int:
        """Returns total count of AuditRecords, optionally filtered by scenario_id and status."""
        query = "SELECT COUNT(*) AS total FROM audit_records"
        params: list[Any] = []
        clauses: list[str] = []
        if scenario_id:
            clauses.append("scenario_id = ?")
            params.append(str(scenario_id))
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            return int(row["total"]) if row else 0

    def list(
        self,
        status: Optional[str] = None,
        scenario_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Lists AuditRecords, optionally filtered by status and scenario_id, with optional pagination."""
        query = "SELECT record_json FROM audit_records"
        params: list[Any] = []
        clauses: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        if scenario_id:
            clauses.append("scenario_id = ?")
            params.append(str(scenario_id))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
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

    def invalidate_stale_pending_records(
        self,
        active_scenario_id: str,
        current_state_revision: str,
        exclude_incident_id: Optional[str] = None,
    ) -> int:
        """
        Atomically transitions all obsolete PENDING_APPROVAL records to STALE_STATE
        if their scenario_id != active_scenario_id OR state_revision != current_state_revision.
        Preserves all historical records while preventing execution of obsolete plans.
        Returns the number of invalidated records.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT incident_id, record_json FROM audit_records WHERE status = 'PENDING_APPROVAL'"
            )
            rows = cursor.fetchall()
            invalidated_count = 0
            for row in rows:
                inc_id = row["incident_id"]
                if exclude_incident_id and inc_id == exclude_incident_id:
                    continue
                rec_dict = json.loads(row["record_json"])
                rec_scenario = rec_dict.get("scenario_id")
                rec_revision = rec_dict.get("state_revision")
                if rec_scenario != active_scenario_id or rec_revision != current_state_revision:
                    rec_dict["status"] = "STALE_STATE"
                    rec_dict["updated_at"] = now_iso
                    new_json = json.dumps(rec_dict, ensure_ascii=False)
                    conn.execute(
                        """
                        UPDATE audit_records
                        SET status = 'STALE_STATE', record_json = ?, updated_at = ?
                        WHERE incident_id = ? AND status = 'PENDING_APPROVAL';
                        """,
                        (new_json, now_iso, inc_id),
                    )
                    invalidated_count += 1
            conn.commit()
            return invalidated_count

    def clear(self) -> None:
        """Clears all records from the audit database (used in testing)."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM audit_records;")
            conn.commit()
