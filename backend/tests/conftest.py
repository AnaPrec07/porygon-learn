import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, app_token="")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


NOTES = """## Lakehouse
A lakehouse is a data system that combines the benefits of data lakes and data warehouses. It supports BI and machine learning on one copy of data.

## Medallion architecture
The medallion architecture organizes tables into bronze, silver and gold layers. Bronze holds raw data, silver holds validated data and gold holds business aggregates.

## Delta Lake
Delta Lake is an open storage layer that adds ACID transactions to Parquet files. Every write creates a new table version."""
