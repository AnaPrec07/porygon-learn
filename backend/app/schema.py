"""Single source of truth for table definitions.

DDL is generated per backend so SQLite (local) and Delta (Databricks) never drift.
Timestamps are ISO-8601 UTC strings and dates are YYYY-MM-DD strings in both, so
every query in repo.py runs unchanged on either engine.
"""
from __future__ import annotations

# column type tokens: S = string, I = integer, D = double
TABLES: dict[str, list[tuple[str, str]]] = {
    "plans": [
        ("id", "S"), ("topic", "S"), ("description", "S"), ("status", "S"), ("created_at", "S"),
    ],
    "plan_points": [
        ("id", "S"), ("plan_id", "S"), ("text", "S"), ("done", "I"), ("position", "I"),
    ],
    "materials": [
        ("id", "S"), ("plan_id", "S"), ("title", "S"), ("source_url", "S"), ("source_type", "S"),
        ("status", "S"),            # unverified | verified
        ("content", "S"), ("chunk_count", "I"), ("created_at", "S"),
    ],
    "lessons": [
        ("id", "S"), ("plan_id", "S"), ("material_id", "S"), ("title", "S"),
        ("created_at", "S"), ("updated_at", "S"),
    ],
    "lesson_sections": [
        ("id", "S"), ("lesson_id", "S"), ("position", "I"), ("heading", "S"),
        ("key_points", "S"),        # JSON array
        ("explanation", "S"), ("marked", "I"),
    ],
    "cards": [
        ("id", "S"), ("plan_id", "S"), ("lesson_id", "S"), ("section_id", "S"), ("topic", "S"),
        ("question", "S"), ("answer", "S"),
        ("chunk_id", "S"),          # key of the source chunk in the vector store
        ("ease", "D"), ("interval_days", "D"), ("reps", "I"), ("lapses", "I"),
        ("due", "S"), ("created_at", "S"),
    ],
    "reviews": [
        ("id", "S"), ("card_id", "S"), ("grade", "I"), ("correct", "I"), ("reviewed_at", "S"),
    ],
    "study_sessions": [
        ("id", "S"), ("date", "S"), ("minutes", "I"), ("category", "S"), ("topic", "S"),
        ("resource", "S"), ("made", "I"), ("studied", "I"), ("created_at", "S"),
    ],
}

_SQLITE = {"S": "TEXT", "I": "INTEGER", "D": "REAL"}
_DELTA = {"S": "STRING", "I": "BIGINT", "D": "DOUBLE"}


def ddl(dialect: str, qualifier: str = "") -> list[str]:
    """CREATE TABLE statements. `qualifier` is e.g. 'main.porygon_learn.' for Delta."""
    types = _SQLITE if dialect == "sqlite" else _DELTA
    out = []
    for table, cols in TABLES.items():
        body = ", ".join(f"{name} {types[t]}" for name, t in cols)
        if dialect == "sqlite":
            out.append(f"CREATE TABLE IF NOT EXISTS {qualifier}{table} ({body}, PRIMARY KEY (id))")
        else:
            out.append(f"CREATE TABLE IF NOT EXISTS {qualifier}{table} ({body}) USING DELTA")
    return out
