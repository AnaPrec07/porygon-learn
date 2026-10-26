"""Thin CRUD helpers. Table and column names come only from code, never from requests."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .db import DB


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def insert(db: DB, table: str, row: dict) -> dict:
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    db.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", row)
    return row


def update(db: DB, table: str, id: str, fields: dict) -> None:
    if not fields:
        return
    sets = ", ".join(f"{c} = :{c}" for c in fields)
    db.execute(f"UPDATE {table} SET {sets} WHERE id = :_id", {**fields, "_id": id})


def get(db: DB, table: str, id: str) -> dict | None:
    rows = db.query(f"SELECT * FROM {table} WHERE id = :id", {"id": id})
    return rows[0] if rows else None


def delete_where(db: DB, table: str, column: str, value: str) -> None:
    db.execute(f"DELETE FROM {table} WHERE {column} = :v", {"v": value})
