"""Vector stores. `s3vectors` is Amazon S3 Vectors; `local` is a JSON file for offline use.

Both hold the chunk text in metadata, so search results are self-contained.
Keys: "<material_id>:<n>" for study material, "card:<card_id>" for flashcards.
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any, Protocol

from .config import Settings

TEXT_KEYS = ["text", "title"]  # large fields: stored but not filterable


class VectorStore(Protocol):
    def put(self, items: list[dict]) -> None: ...        # {key, vector, metadata}
    def query(self, vector: list[float], k: int, filter: dict | None = None) -> list[dict]: ...
    def get(self, keys: list[str]) -> list[dict]: ...
    def delete(self, keys: list[str]) -> None: ...


def _matches(meta: dict, filt: dict | None) -> bool:
    return all(meta.get(k) == v for k, v in (filt or {}).items())


class LocalVectorStore:
    def __init__(self, path: Path, dim: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path, self._dim = path, dim
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        if path.exists():
            blob = json.loads(path.read_text())
            if blob.get("dim") != dim:
                raise RuntimeError(
                    f"{path} holds {blob.get('dim')}-dim vectors but the embedder produces {dim}. "
                    "Use a different DATA_DIR or delete the file and re-ingest.")
            self._items = blob["items"]

    def _save(self):
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"dim": self._dim, "items": self._items}))
        tmp.replace(self._path)

    def put(self, items):
        with self._lock:
            for it in items:
                self._items[it["key"]] = {"vector": it["vector"], "metadata": it["metadata"]}
            self._save()

    def query(self, vector, k, filter=None):
        with self._lock:
            scored = []
            for key, it in self._items.items():
                if not _matches(it["metadata"], filter):
                    continue
                score = sum(a * b for a, b in zip(vector, it["vector"]))
                scored.append({"key": key, "score": score, "metadata": it["metadata"]})
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]

    def get(self, keys):
        with self._lock:
            return [{"key": k, "metadata": self._items[k]["metadata"]} for k in keys if k in self._items]

    def delete(self, keys):
        with self._lock:
            for k in keys:
                self._items.pop(k, None)
            self._save()


class S3VectorStore:
    """Amazon S3 Vectors: a vector bucket with one cosine index."""

    def __init__(self, bucket: str, index: str, dim: int, region: str, client=None):
        if not bucket:
            raise RuntimeError("S3_VECTOR_BUCKET is required when VECTOR_BACKEND=s3vectors")
        import boto3
        self.bucket, self.index, self.dim = bucket, index, dim
        self._c = client or boto3.client("s3vectors", region_name=region)
        self._ensure()

    def _ensure(self):
        try:
            self._c.get_vector_bucket(vectorBucketName=self.bucket)
        except self._c.exceptions.NotFoundException:
            self._c.create_vector_bucket(vectorBucketName=self.bucket)
        try:
            idx = self._c.get_index(vectorBucketName=self.bucket, indexName=self.index)["index"]
            if idx["dimension"] != self.dim:
                raise RuntimeError(
                    f"S3 Vectors index '{self.index}' has dimension {idx['dimension']}, embedder gives "
                    f"{self.dim}. Point S3_VECTOR_INDEX at a new name or fix EMBED_DIM.")
        except self._c.exceptions.NotFoundException:
            self._c.create_index(
                vectorBucketName=self.bucket, indexName=self.index, dataType="float32",
                dimension=self.dim, distanceMetric="cosine",
                metadataConfiguration={"nonFilterableMetadataKeys": TEXT_KEYS},
            )

    @staticmethod
    def _filter(filt: dict | None) -> dict | None:
        if not filt:
            return None
        if len(filt) == 1:
            return dict(filt)
        return {"$and": [{k: v} for k, v in filt.items()]}

    def put(self, items):
        for i in range(0, len(items), 500):  # API limit per call
            self._c.put_vectors(
                vectorBucketName=self.bucket, indexName=self.index,
                vectors=[{"key": it["key"], "data": {"float32": it["vector"]}, "metadata": it["metadata"]}
                         for it in items[i:i + 500]],
            )

    def query(self, vector, k, filter=None):
        args: dict[str, Any] = dict(
            vectorBucketName=self.bucket, indexName=self.index,
            queryVector={"float32": vector}, topK=min(k, 100),
            returnMetadata=True, returnDistance=True,
        )
        f = self._filter(filter)
        if f:
            args["filter"] = f
        res = self._c.query_vectors(**args)["vectors"]
        # cosine distance = 1 - cosine similarity
        return [{"key": r["key"], "score": 1.0 - r["distance"], "metadata": r.get("metadata", {})} for r in res]

    def get(self, keys):
        if not keys:
            return []
        res = self._c.get_vectors(vectorBucketName=self.bucket, indexName=self.index,
                                  keys=keys, returnMetadata=True)["vectors"]
        return [{"key": r["key"], "metadata": r.get("metadata", {})} for r in res]

    def delete(self, keys):
        for i in range(0, len(keys), 500):
            self._c.delete_vectors(vectorBucketName=self.bucket, indexName=self.index, keys=keys[i:i + 500])


def build_vectorstore(s: Settings, dim: int) -> VectorStore:
    if s.vector_backend == "s3vectors":
        return S3VectorStore(s.s3_vector_bucket, s.s3_vector_index, dim, s.aws_region)
    return LocalVectorStore(s.data_dir / "vectors.json", dim)
