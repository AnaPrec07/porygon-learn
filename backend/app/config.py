"""Runtime configuration, read once from environment variables.

Every backend defaults to something that runs offline on a laptop, so the app
works with zero credentials. Switch pieces over to Databricks / AWS one at a time.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # --- app ---
    app_token: str = field(default_factory=lambda: _env("APP_TOKEN"))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))

    # --- relational store: sqlite | databricks (Delta tables via SQL warehouse) ---
    store_backend: str = field(default_factory=lambda: _env("STORE_BACKEND", "sqlite"))
    databricks_host: str = field(default_factory=lambda: _env("DATABRICKS_HOST").rstrip("/"))
    databricks_token: str = field(default_factory=lambda: _env("DATABRICKS_TOKEN"))
    databricks_warehouse_id: str = field(default_factory=lambda: _env("DATABRICKS_WAREHOUSE_ID"))
    databricks_catalog: str = field(default_factory=lambda: _env("DATABRICKS_CATALOG", "main"))
    databricks_schema: str = field(default_factory=lambda: _env("DATABRICKS_SCHEMA", "porygon_learn"))

    # --- vector store: local | s3vectors (Amazon S3 Vectors) ---
    vector_backend: str = field(default_factory=lambda: _env("VECTOR_BACKEND", "local"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "us-east-1"))
    s3_vector_bucket: str = field(default_factory=lambda: _env("S3_VECTOR_BUCKET"))
    s3_vector_index: str = field(default_factory=lambda: _env("S3_VECTOR_INDEX", "learning-chunks"))

    # --- embeddings: local | databricks | bedrock ---
    embed_backend: str = field(default_factory=lambda: _env("EMBED_BACKEND", "local"))
    embed_endpoint: str = field(default_factory=lambda: _env("EMBED_ENDPOINT", "databricks-gte-large-en"))
    bedrock_embed_model: str = field(default_factory=lambda: _env("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0"))
    embed_dim: int = field(default_factory=lambda: int(_env("EMBED_DIM", "0") or 0))

    # --- LLM for research / lessons / cards: none | databricks | bedrock ---
    llm_backend: str = field(default_factory=lambda: _env("LLM_BACKEND", "none"))
    llm_endpoint: str = field(default_factory=lambda: _env("LLM_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct"))
    bedrock_llm_model: str = field(default_factory=lambda: _env("BEDROCK_LLM_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"))

    def public(self) -> dict:
        """Safe-to-show summary of which backends are active (no secrets)."""
        return {
            "store": self.store_backend,
            "vectors": self.vector_backend,
            "embeddings": self.embed_backend,
            "llm": self.llm_backend,
            "auth_required": bool(self.app_token),
        }
