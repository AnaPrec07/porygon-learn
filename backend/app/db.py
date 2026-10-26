"""Tiny SQL access layer with two engines behind one interface.

Both engines use `:name` parameters, so the SQL in repo.py is shared.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import Settings
from .schema import ddl


class DB(Protocol):
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]: ...
    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None: ...
    def init_schema(self) -> None: ...


class SqliteDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def query(self, sql, params=None):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params or {}).fetchall()]

    def execute(self, sql, params=None):
        with self._lock:
            self._conn.execute(sql, params or {})
            self._conn.commit()

    def init_schema(self):
        for stmt in ddl("sqlite"):
            self.execute(stmt)


class DatabricksError(RuntimeError):
    pass


_INT_TYPES = {"BYTE", "SHORT", "INT", "LONG"}
_FLOAT_TYPES = {"FLOAT", "DOUBLE", "DECIMAL"}


def _coerce(value: str | None, type_name: str):
    if value is None:
        return None
    if type_name in _INT_TYPES:
        return int(value)
    if type_name in _FLOAT_TYPES:
        return float(value)
    if type_name == "BOOLEAN":
        return value.lower() == "true"
    return value


def _param(name: str, value: Any) -> dict:
    if value is None:
        return {"name": name}  # omitted value => SQL NULL
    if isinstance(value, bool):
        return {"name": name, "value": "1" if value else "0", "type": "BIGINT"}
    if isinstance(value, int):
        return {"name": name, "value": str(value), "type": "BIGINT"}
    if isinstance(value, float):
        return {"name": name, "value": repr(value), "type": "DOUBLE"}
    return {"name": name, "value": str(value), "type": "STRING"}


class DatabricksDB:
    """Runs SQL on a Databricks SQL warehouse via the Statement Execution API.

    Tables live in Unity Catalog as Delta tables: <catalog>.<schema>.<table>.
    """

    def __init__(self, host: str, token: str, warehouse_id: str, catalog: str, schema: str,
                 client: httpx.Client | None = None):
        if not (host and token and warehouse_id):
            raise DatabricksError("DATABRICKS_HOST, DATABRICKS_TOKEN and DATABRICKS_WAREHOUSE_ID are required")
        self.catalog, self.schema, self.warehouse_id = catalog, schema, warehouse_id
        self._client = client or httpx.Client(
            base_url=host, headers={"Authorization": f"Bearer {token}"}, timeout=60.0
        )

    def _run(self, sql: str, params: dict[str, Any] | None) -> dict:
        body = {
            "warehouse_id": self.warehouse_id,
            "catalog": self.catalog,
            "schema": self.schema,
            "statement": sql,
            "wait_timeout": "30s",
            "on_wait_timeout": "CONTINUE",
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
            "parameters": [_param(k, v) for k, v in (params or {}).items()],
        }
        r = self._client.post("/api/2.0/sql/statements", json=body)
        r.raise_for_status()
        data = r.json()
        deadline = time.time() + 300
        while data["status"]["state"] in ("PENDING", "RUNNING"):
            if time.time() > deadline:
                raise DatabricksError("statement timed out")
            time.sleep(1.0)
            r = self._client.get(f"/api/2.0/sql/statements/{data['statement_id']}")
            r.raise_for_status()
            data = r.json()
        if data["status"]["state"] != "SUCCEEDED":
            err = data["status"].get("error", {}).get("message", data["status"]["state"])
            raise DatabricksError(err)
        return data

    def query(self, sql, params=None):
        data = self._run(sql, params)
        cols = data.get("manifest", {}).get("schema", {}).get("columns", [])
        rows = data.get("result", {}).get("data_array", []) or []
        return [
            {c["name"]: _coerce(v, c["type_name"]) for c, v in zip(cols, row)}
            for row in rows
        ]

    def execute(self, sql, params=None):
        self._run(sql, params)

    def init_schema(self):
        self.execute(f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{self.schema}")
        for stmt in ddl("delta"):
            self.execute(stmt)


def build_db(s: Settings) -> DB:
    if s.store_backend == "databricks":
        return DatabricksDB(s.databricks_host, s.databricks_token, s.databricks_warehouse_id,
                            s.databricks_catalog, s.databricks_schema)
    return SqliteDB(s.data_dir / "learn.db")
