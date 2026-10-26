import json
from datetime import datetime, timezone

import httpx
import pytest

from app.chunking import chunk_text
from app.db import DatabricksDB, DatabricksError
from app.embeddings import LocalEmbedder
from app.llm import extract_json
from app.srs import schedule
from app.vectorstore import LocalVectorStore, S3VectorStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_srs_progression():
    s = schedule(2.5, 0, 0, 0, 2, NOW)
    assert (s["reps"], s["interval_days"]) == (1, 1.0)
    s = schedule(s["ease"], s["interval_days"], s["reps"], 0, 2, NOW)
    assert s["interval_days"] == 6.0
    s = schedule(s["ease"], s["interval_days"], s["reps"], 0, 2, NOW)
    assert s["interval_days"] == 15.0  # 6 * 2.5
    lapse = schedule(s["ease"], s["interval_days"], s["reps"], 0, 0, NOW)
    assert lapse["reps"] == 0 and lapse["lapses"] == 1 and lapse["ease"] == pytest.approx(2.3)
    # ease never drops below the floor
    e = 1.35
    for _ in range(5):
        e = schedule(e, 3, 3, 0, 0, NOW)["ease"]
    assert e == 1.3


def test_chunking():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 80 for i in range(6))
    chunks = chunk_text(text, 900)
    assert len(chunks) > 1 and all(len(c) <= 900 for c in chunks)
    assert chunk_text("   ") == []
    assert all(len(c) <= 300 for c in chunk_text("Sentence one is here. " * 100, 300))


def test_extract_json():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here: [{"q": "x"}] done') == [{"q": "x"}]
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_local_vectorstore_roundtrip_and_dim_guard(tmp_path):
    emb = LocalEmbedder(64)
    vs = LocalVectorStore(tmp_path / "v.json", 64)
    docs = {"a": "delta lake transactions", "b": "unity catalog governance", "c": "spark structured streaming"}
    vs.put([{"key": k, "vector": v, "metadata": {"plan_id": "p" if k != "c" else "q", "text": t}}
            for (k, t), v in zip(docs.items(), emb.embed(list(docs.values())))])
    top = vs.query(emb.embed(["governance of unity catalog"])[0], 2)
    assert top[0]["key"] == "b"
    assert [r["key"] for r in vs.query(emb.embed(["streaming"])[0], 5, {"plan_id": "q"})] == ["c"]
    vs.delete(["b"])
    assert LocalVectorStore(tmp_path / "v.json", 64).get(["b", "a"])[0]["key"] == "a"  # persisted
    with pytest.raises(RuntimeError):
        LocalVectorStore(tmp_path / "v.json", 128)


class FakeS3Vectors:
    """Mimics the boto3 `s3vectors` client surface the app uses."""
    class exceptions:
        class NotFoundException(Exception):
            pass

    def __init__(self):
        self.calls, self.store, self.index = [], {}, None

    def get_vector_bucket(self, **kw):
        raise self.exceptions.NotFoundException()

    def create_vector_bucket(self, **kw):
        self.calls.append(("create_bucket", kw))

    def get_index(self, **kw):
        if self.index is None:
            raise self.exceptions.NotFoundException()
        return {"index": self.index}

    def create_index(self, **kw):
        self.calls.append(("create_index", kw))
        self.index = {"dimension": kw["dimension"]}

    def put_vectors(self, **kw):
        self.calls.append(("put", len(kw["vectors"])))
        for v in kw["vectors"]:
            self.store[v["key"]] = v

    def query_vectors(self, **kw):
        self.calls.append(("query", kw.get("filter")))
        q = kw["queryVector"]["float32"]
        res = [{"key": k, "distance": 1 - sum(a * b for a, b in zip(q, v["data"]["float32"])),
                "metadata": v["metadata"]} for k, v in self.store.items()]
        return {"vectors": sorted(res, key=lambda r: r["distance"])[: kw["topK"]]}

    def get_vectors(self, **kw):
        return {"vectors": [{"key": k, "metadata": self.store[k]["metadata"]} for k in kw["keys"] if k in self.store]}

    def delete_vectors(self, **kw):
        for k in kw["keys"]:
            self.store.pop(k, None)


def test_s3_vectors_adapter():
    fake = FakeS3Vectors()
    vs = S3VectorStore("my-bucket", "idx", 64, "us-east-1", client=fake)
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["create_bucket", "create_index"]
    assert fake.calls[1][1]["distanceMetric"] == "cosine"
    assert "text" in fake.calls[1][1]["metadataConfiguration"]["nonFilterableMetadataKeys"]

    emb = LocalEmbedder(64)
    vs.put([{"key": "m:0", "vector": emb.embed(["bronze silver gold"])[0], "metadata": {"plan_id": "p", "kind": "material", "text": "t"}}])
    hits = vs.query(emb.embed(["bronze silver gold"])[0], 3, {"plan_id": "p", "kind": "material"})
    assert hits[0]["key"] == "m:0" and hits[0]["score"] == pytest.approx(1.0)
    assert fake.calls[-1][1] == {"$and": [{"plan_id": "p"}, {"kind": "material"}]}
    vs.query(emb.embed(["x"])[0], 3)
    assert fake.calls[-1][1] is None
    vs.delete(["m:0"])
    assert vs.get(["m:0"]) == []
    # existing index with a different dimension is refused loudly
    with pytest.raises(RuntimeError, match="dimension"):
        S3VectorStore("my-bucket", "idx", 128, "us-east-1", client=fake)


def test_databricks_sql_adapter():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, body))
        if req.method == "POST":
            if "BOOM" in body["statement"]:
                return httpx.Response(200, json={"statement_id": "s2", "status": {"state": "FAILED", "error": {"message": "bad sql"}}})
            return httpx.Response(200, json={"statement_id": "s1", "status": {"state": "PENDING"}})
        return httpx.Response(200, json={
            "statement_id": "s1", "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": [{"name": "id", "type_name": "STRING"}, {"name": "n", "type_name": "LONG"},
                                                {"name": "ease", "type_name": "DOUBLE"}, {"name": "x", "type_name": "STRING"}]}},
            "result": {"data_array": [["a", "3", "2.5", None]]}})

    client = httpx.Client(base_url="https://dbx.example", transport=httpx.MockTransport(handler))
    import time as _t
    _t.sleep = lambda s: None
    db = DatabricksDB("https://dbx.example", "tok", "wh1", "main", "porygon_learn", client=client)
    rows = db.query("SELECT * FROM cards WHERE id = :id AND reps > :r AND note = :note", {"id": "a", "r": 2, "note": None})
    assert rows == [{"id": "a", "n": 3, "ease": 2.5, "x": None}]
    post = seen[0][2]
    assert post["warehouse_id"] == "wh1" and post["catalog"] == "main" and post["schema"] == "porygon_learn"
    assert post["parameters"] == [{"name": "id", "value": "a", "type": "STRING"},
                                  {"name": "r", "value": "2", "type": "BIGINT"}, {"name": "note"}]
    with pytest.raises(DatabricksError, match="bad sql"):
        db.execute("BOOM")
    with pytest.raises(DatabricksError):
        DatabricksDB("", "", "", "m", "s")
