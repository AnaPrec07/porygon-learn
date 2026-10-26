"""Print the Delta DDL so you can run it in a Databricks SQL editor or notebook.

    python scripts/print_databricks_ddl.py [catalog] [schema]

(The app also creates these tables itself on first start; this is for reviewing or
pre-creating them, e.g. under a different owner.)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.schema import ddl  # noqa: E402

catalog = sys.argv[1] if len(sys.argv) > 1 else "main"
schema = sys.argv[2] if len(sys.argv) > 2 else "porygon_learn"
print(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema};\n")
for stmt in ddl("delta", f"{catalog}.{schema}."):
    print(stmt + ";\n")
