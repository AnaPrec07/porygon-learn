def mk_plan(client):
    r = client.post("/api/plans", json={"topic": "Databricks", "points": ["Lakehouse", "Delta"]})
    assert r.status_code == 201
    return r.json()


def test_full_learning_flow(client):
    from .conftest import NOTES
    plan = mk_plan(client)
    assert [p["text"] for p in plan["points"]] == ["Lakehouse", "Delta"]

    mat = client.post(f"/api/plans/{plan['id']}/materials", json={"title": "Notes", "text": NOTES}).json()
    assert mat["chunk_count"] >= 1 and mat["status"] == "unverified"
    assert client.patch(f"/api/materials/{mat['id']}", json={"status": "verified"}).json()["status"] == "verified"

    # semantic(ish) search finds the right chunk
    hits = client.get("/api/search", params={"q": "bronze silver gold layers", "plan_id": plan["id"]}).json()
    assert hits and "bronze" in hits[0]["text"].lower() and hits[0]["kind"] == "material"

    lesson = client.post(f"/api/materials/{mat['id']}/lesson").json()
    heads = [s["heading"] for s in lesson["sections"]]
    assert heads == ["Lakehouse", "Medallion architecture", "Delta Lake"]

    # nothing marked -> error; mark one -> cards from that section only
    assert client.post(f"/api/lessons/{lesson['id']}/cards", json={"auto": False}).status_code == 422
    sec = lesson["sections"][2]
    assert client.patch(f"/api/sections/{sec['id']}", json={"marked": True}).json()["marked"] == 1
    out = client.post(f"/api/lessons/{lesson['id']}/cards", json={"auto": False}).json()
    cards = out["created"]
    assert cards and all(c["section_id"] == sec["id"] for c in cards)
    assert cards[0]["question"].startswith("What is Delta Lake")
    # card is linked back to a source chunk in the vector store
    assert cards[0]["chunk_id"].startswith(mat["id"])
    src = client.get(f"/api/cards/{cards[0]['id']}/source").json()
    assert "ACID" in src["text"]

    # regenerating doesn't duplicate
    again = client.post(f"/api/lessons/{lesson['id']}/cards", json={"auto": False}).json()
    assert again["created"] == [] and again["skipped_duplicates"] == len(cards)

    # auto-select covers the remaining sections
    auto = client.post(f"/api/lessons/{lesson['id']}/cards", json={"auto": True}).json()
    assert len(auto["created"]) >= 2

    # cards are searchable too
    card_hits = client.get("/api/search", params={"q": "ACID transactions", "kind": "card"}).json()
    assert card_hits and all(h["kind"] == "card" for h in card_hits)

    html = client.get(f"/api/lessons/{lesson['id']}/html")
    assert html.status_code == 200 and "<h1>Notes</h1>" in html.text


def test_review_updates_schedule_and_stats(client):
    plan = mk_plan(client)
    c = client.post("/api/cards", json={"plan_id": plan["id"], "question": "Q1?", "answer": "A1"}).json()
    q = client.get("/api/review/queue", params={"plan_id": plan["id"]}).json()
    assert [x["id"] for x in q] == [c["id"]]

    good = client.post("/api/review", json={"card_id": c["id"], "grade": 2}).json()
    assert good["reps"] == 1 and good["interval_days"] == 1.0 and good["due"] > c["due"]
    # reviewed card is no longer due
    assert client.get("/api/review/queue", params={"plan_id": plan["id"]}).json() == []
    # but 'all' mode still returns it
    assert len(client.get("/api/review/queue", params={"mode": "all"}).json()) == 1

    again = client.post("/api/review", json={"card_id": c["id"], "grade": 0}).json()
    assert again["reps"] == 0 and again["lapses"] == 1
    assert client.post("/api/review", json={"card_id": c["id"], "grade": 9}).status_code == 422

    s = client.get("/api/stats").json()
    assert s["cards_total"] == 1 and s["reviews_today"] == 2 and s["streak_days"] == 1
    assert s["retention_30d"] == 0.5
    plans = client.get("/api/plans").json()
    assert (plans[0]["card_count"], plans[0]["due_count"], plans[0]["new_count"]) == (1, 0, 0)  # lapsed again -> due in 10 min, not "new"


def test_seed_and_delete_cascade(client):
    r = client.post("/api/seed/databricks").json()
    assert r["imported"] == 136
    assert client.post("/api/seed/databricks").json()["imported"] == 0  # idempotent
    hits = client.get("/api/search", params={"q": "what is VACUUM stale files"}).json()
    assert hits and "VACUUM" in hits[0]["text"]
    assert client.delete(f"/api/plans/{r['plan_id']}").status_code == 204
    assert client.get("/api/cards").json() == []
    assert client.get("/api/search", params={"q": "VACUUM"}).json() == []


def test_log_crud(client):
    body = {"date": "2026-10-26", "minutes": 45, "category": "Data engineering", "topic": "Delta",
            "resource": "Docs", "made": 3, "studied": 10}
    row = client.post("/api/log", json=body).json()
    client.put(f"/api/log/{row['id']}", json={**body, "minutes": 60})
    assert client.get("/api/log").json()[0]["minutes"] == 60
    assert client.post("/api/log", json={**body, "date": "yesterday"}).status_code == 422
    assert client.delete(f"/api/log/{row['id']}").status_code == 204
    assert client.get("/api/log").json() == []


def test_errors(client):
    assert client.get("/api/plans/nope").status_code == 404
    plan = mk_plan(client)
    # no LLM configured -> clear 501, not a crash
    assert client.post(f"/api/plans/{plan['id']}/research", json={}).status_code == 501
    assert client.post(f"/api/plans/{plan['id']}/materials", json={"title": "x", "text": "   "}).status_code == 422


def test_auth(settings):
    from fastapi.testclient import TestClient
    from dataclasses import replace
    from app.main import create_app
    c = TestClient(create_app(replace(settings, app_token="s3cret")))
    assert c.get("/api/health").json()["auth_required"] is True
    assert c.get("/api/plans").status_code == 401
    assert c.get("/api/plans", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/plans", headers={"Authorization": "Bearer s3cret"}).status_code == 200


class FakeLLM:
    available = True

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def chat(self, system, user, max_tokens=0):
        self.prompts.append(user)
        return self.replies.pop(0)


def test_llm_paths_and_fallback(settings):
    from fastapi.testclient import TestClient
    from app.core import Learning
    from app.main import create_app
    from .conftest import NOTES

    lesson_json = ('```json\n{"title": "Lakehouse 101", "sections": [{"heading": "Why", '
                   '"key_points": ["One copy of data"], "explanation": "Lakes plus warehouses."}]}\n```')
    cards_json = '[{"q": "What does a lakehouse combine?", "a": "Data lake and data warehouse benefits"}]'
    llm = FakeLLM(["## Research\nDelta Lake adds ACID to Parquet.", lesson_json, cards_json, "not json at all"])
    c = TestClient(create_app(settings, Learning(settings, llm=llm)))

    plan = c.post("/api/plans", json={"topic": "Databricks"}).json()
    researched = c.post(f"/api/plans/{plan['id']}/research", json={"focus": "delta"}).json()
    assert researched["status"] == "unverified" and researched["source_type"] == "ai_research"

    other = c.post(f"/api/plans/{plan['id']}/materials", json={"title": "N", "text": NOTES}).json()
    lesson = c.post(f"/api/materials/{other['id']}/lesson").json()
    assert lesson["title"] == "Lakehouse 101" and lesson["sections"][0]["key_points"] == ["One copy of data"]
    assert lesson["related"], "lesson should surface related notes from other materials"
    assert "Related notes" in llm.prompts[1]

    out = c.post(f"/api/lessons/{lesson['id']}/cards", json={"auto": True}).json()
    assert [x["question"] for x in out["created"]] == ["What does a lakehouse combine?"]

    # malformed LLM output degrades to the heuristic builder instead of a 500
    llm.replies = ["garbage"]
    lesson2 = c.post(f"/api/materials/{other['id']}/lesson").json()
    assert [s["heading"] for s in lesson2["sections"]][0] == "Lakehouse"
