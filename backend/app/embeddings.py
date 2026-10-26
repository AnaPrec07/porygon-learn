"""Text -> vector. `local` needs no network; `databricks` / `bedrock` give real semantics."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Protocol

import httpx

from .config import Settings


class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


_WORD = re.compile(r"[a-z0-9]+")


class LocalEmbedder:
    """Deterministic hashing-trick embedder (unigrams + bigrams).

    Captures keyword overlap, not meaning. It exists so the app runs offline and in
    tests; use EMBED_BACKEND=databricks or bedrock for real semantic search.
    """

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        words = _WORD.findall(text.lower())
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        v = [0.0] * self.dim
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts):
        return [self._one(t) for t in texts]


class DatabricksEmbedder:
    """Databricks Foundation Model API / any serving endpoint with the OpenAI embeddings shape."""

    def __init__(self, host: str, token: str, endpoint: str, dim: int = 1024,
                 client: httpx.Client | None = None):
        self.dim, self.endpoint = dim, endpoint
        self._client = client or httpx.Client(
            base_url=host, headers={"Authorization": f"Bearer {token}"}, timeout=60.0
        )

    def embed(self, texts):
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            r = self._client.post(f"/serving-endpoints/{self.endpoint}/invocations",
                                  json={"input": texts[i:i + 32]})
            r.raise_for_status()
            out.extend(d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d.get("index", 0)))
        return out


class BedrockEmbedder:
    def __init__(self, region: str, model_id: str, dim: int = 1024, client=None):
        import boto3
        self.dim, self.model_id = dim, model_id
        self._client = client or boto3.client("bedrock-runtime", region_name=region)

    def embed(self, texts):
        out = []
        for t in texts:
            resp = self._client.invoke_model(
                modelId=self.model_id,
                body=json.dumps({"inputText": t[:8000], "dimensions": self.dim, "normalize": True}),
            )
            out.append(json.loads(resp["body"].read())["embedding"])
        return out


def build_embedder(s: Settings) -> Embedder:
    if s.embed_backend == "databricks":
        return DatabricksEmbedder(s.databricks_host, s.databricks_token, s.embed_endpoint, s.embed_dim or 1024)
    if s.embed_backend == "bedrock":
        return BedrockEmbedder(s.aws_region, s.bedrock_embed_model, s.embed_dim or 1024)
    return LocalEmbedder(s.embed_dim or 384)
