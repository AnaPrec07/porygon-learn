"""Learning workflow: ingest -> search -> lesson -> flashcards -> spaced review -> stats."""
from __future__ import annotations

import html
import json
import random
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import repo
from .chunking import chunk_text, split_sentences
from .config import Settings
from .db import DB, build_db
from .embeddings import Embedder, build_embedder
from .llm import LLMUnavailable, build_llm, extract_json
from .srs import GOOD, schedule
from .vectorstore import VectorStore, build_vectorstore


class NotFound(LookupError):
    pass


class Learning:
    def __init__(self, settings: Settings, db: DB | None = None, embedder: Embedder | None = None,
                 vectors: VectorStore | None = None, llm=None):
        self.settings = settings
        self.db = db or build_db(settings)
        self.embedder = embedder or build_embedder(settings)
        self.vectors = vectors or build_vectorstore(settings, self.embedder.dim)
        self.llm = llm or build_llm(settings)
        self.db.init_schema()

    # ------------------------------------------------------------------ plans
    def create_plan(self, topic: str, description: str = "", points: list[str] | None = None) -> dict:
        plan = repo.insert(self.db, "plans", {
            "id": repo.new_id(), "topic": topic.strip(), "description": description.strip(),
            "status": "active", "created_at": repo.now_iso()})
        for i, text in enumerate(p.strip() for p in (points or []) if p.strip()):
            repo.insert(self.db, "plan_points", {
                "id": repo.new_id(), "plan_id": plan["id"], "text": text, "done": 0, "position": i})
        return self.get_plan(plan["id"])

    def list_plans(self) -> list[dict]:
        plans = self.db.query("SELECT * FROM plans ORDER BY created_at DESC")
        counts = {r["plan_id"]: r for r in self.db.query(
            "SELECT plan_id, COUNT(*) AS cards, "
            "SUM(CASE WHEN due <= :now AND (reps > 0 OR lapses > 0) THEN 1 ELSE 0 END) AS due, "
            "SUM(CASE WHEN reps = 0 AND lapses = 0 THEN 1 ELSE 0 END) AS fresh "
            "FROM cards GROUP BY plan_id", {"now": repo.now_iso()})}
        for p in plans:
            c = counts.get(p["id"], {})
            p["card_count"], p["due_count"], p["new_count"] = (c.get(k, 0) or 0 for k in ("cards", "due", "fresh"))
        return plans

    def get_plan(self, plan_id: str) -> dict:
        plan = repo.get(self.db, "plans", plan_id)
        if not plan:
            raise NotFound("plan not found")
        plan["points"] = self.db.query(
            "SELECT * FROM plan_points WHERE plan_id = :p ORDER BY position", {"p": plan_id})
        plan["materials"] = self.db.query(
            "SELECT id, plan_id, title, source_url, source_type, status, chunk_count, created_at "
            "FROM materials WHERE plan_id = :p ORDER BY created_at DESC", {"p": plan_id})
        plan["lessons"] = self.db.query(
            "SELECT * FROM lessons WHERE plan_id = :p ORDER BY created_at DESC", {"p": plan_id})
        return plan

    def update_plan(self, plan_id: str, fields: dict) -> dict:
        self.get_plan(plan_id)
        repo.update(self.db, "plans", plan_id, fields)
        return self.get_plan(plan_id)

    def add_point(self, plan_id: str, text: str) -> dict:
        self.get_plan(plan_id)
        n = self.db.query("SELECT COUNT(*) AS n FROM plan_points WHERE plan_id = :p", {"p": plan_id})[0]["n"]
        return repo.insert(self.db, "plan_points", {
            "id": repo.new_id(), "plan_id": plan_id, "text": text.strip(), "done": 0, "position": n})

    def update_point(self, point_id: str, fields: dict) -> dict:
        if not repo.get(self.db, "plan_points", point_id):
            raise NotFound("point not found")
        repo.update(self.db, "plan_points", point_id, fields)
        return repo.get(self.db, "plan_points", point_id)

    def delete_plan(self, plan_id: str) -> None:
        self.get_plan(plan_id)
        keys = [r["id"] for r in self.db.query("SELECT id FROM cards WHERE plan_id = :p", {"p": plan_id})]
        self.vectors.delete([f"card:{k}" for k in keys])
        for m in self.db.query("SELECT id FROM materials WHERE plan_id = :p", {"p": plan_id}):
            self._drop_material_vectors(m["id"])
        for lesson in self.db.query("SELECT id FROM lessons WHERE plan_id = :p", {"p": plan_id}):
            repo.delete_where(self.db, "lesson_sections", "lesson_id", lesson["id"])
        for card_id in keys:
            repo.delete_where(self.db, "reviews", "card_id", card_id)
        for table in ("cards", "lessons", "materials", "plan_points"):
            repo.delete_where(self.db, table, "plan_id", plan_id)
        repo.delete_where(self.db, "plans", "id", plan_id)

    # -------------------------------------------------------------- materials
    def add_material(self, plan_id: str, title: str, text: str, source_url: str = "",
                     source_type: str = "note", status: str = "unverified") -> dict:
        plan = self.get_plan(plan_id)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("material is empty")
        mid = repo.new_id()
        vectors = self.embedder.embed(chunks)
        self.vectors.put([{
            "key": f"{mid}:{i}", "vector": v,
            "metadata": {"kind": "material", "plan_id": plan_id, "material_id": mid,
                         "topic": plan["topic"], "title": title, "text": c}}
            for i, (c, v) in enumerate(zip(chunks, vectors))])
        repo.insert(self.db, "materials", {
            "id": mid, "plan_id": plan_id, "title": title.strip(), "source_url": source_url.strip(),
            "source_type": source_type, "status": status, "content": text,
            "chunk_count": len(chunks), "created_at": repo.now_iso()})
        return self.get_material(mid)

    def get_material(self, material_id: str) -> dict:
        m = repo.get(self.db, "materials", material_id)
        if not m:
            raise NotFound("material not found")
        return m

    def set_material_status(self, material_id: str, status: str) -> dict:
        self.get_material(material_id)
        repo.update(self.db, "materials", material_id, {"status": status})
        return self.get_material(material_id)

    def _drop_material_vectors(self, material_id: str) -> None:
        m = repo.get(self.db, "materials", material_id)
        if m:
            self.vectors.delete([f"{material_id}:{i}" for i in range(m["chunk_count"] or 0)])

    def delete_material(self, material_id: str) -> None:
        self.get_material(material_id)
        self._drop_material_vectors(material_id)
        repo.delete_where(self.db, "materials", "id", material_id)

    def research(self, plan_id: str, focus: str = "") -> dict:
        """Ask the LLM for study notes on the plan. Stored as *unverified* until you check it."""
        plan = self.get_plan(plan_id)
        points = "\n".join(f"- {p['text']}" for p in plan["points"]) or "(no specific points)"
        system = ("You are a careful research assistant preparing study notes. Be accurate and concrete. "
                  "Clearly separate well-established facts from anything uncertain, and omit marketing fluff, "
                  "anecdotes and filler. Use '## Heading' sections with short paragraphs.")
        user = (f"Topic: {plan['topic']}\n{plan['description']}\nFocus: {focus or 'general overview'}\n"
                f"Points I want to learn:\n{points}\n\nWrite the study notes.")
        notes = self.llm.chat(system, user, max_tokens=3500)
        return self.add_material(plan_id, f"AI research: {focus or plan['topic']}", notes,
                                 source_type="ai_research", status="unverified")

    # ----------------------------------------------------------------- search
    def search(self, query: str, plan_id: str | None = None, kind: str | None = None, k: int = 8) -> list[dict]:
        filt = {k_: v for k_, v in (("plan_id", plan_id), ("kind", kind)) if v}
        vec = self.embedder.embed([query])[0]
        hits = self.vectors.query(vec, k, filt or None)
        return [self._hit(h) for h in hits]

    @staticmethod
    def _hit(h: dict) -> dict:
        m = h["metadata"]
        return {"key": h["key"], "score": round(float(h["score"]), 4), "kind": m.get("kind"),
                "plan_id": m.get("plan_id"), "material_id": m.get("material_id"),
                "topic": m.get("topic"), "title": m.get("title"), "text": m.get("text")}

    # ---------------------------------------------------------------- lessons
    def build_lesson(self, material_id: str) -> dict:
        mat = self.get_material(material_id)
        plan = self.get_plan(mat["plan_id"])
        # compare-and-contrast: pull what you already know from *other* materials
        related = [h for h in self.search(mat["content"][:800], plan_id=None, kind="material", k=6)
                   if h["material_id"] != material_id][:3]
        sections = None
        title = mat["title"]
        if self.llm.available:
            try:
                sections, title = self._llm_sections(plan["topic"], mat, related, title)
            except (ValueError, KeyError, TypeError):
                sections = None  # fall back to deterministic structuring
        if not sections:
            sections = heuristic_sections(mat["content"])
        now = repo.now_iso()
        lesson = repo.insert(self.db, "lessons", {
            "id": repo.new_id(), "plan_id": mat["plan_id"], "material_id": material_id,
            "title": title, "created_at": now, "updated_at": now})
        for i, s in enumerate(sections):
            repo.insert(self.db, "lesson_sections", {
                "id": repo.new_id(), "lesson_id": lesson["id"], "position": i, "heading": s["heading"],
                "key_points": json.dumps(s["key_points"]), "explanation": s["explanation"], "marked": 0})
        out = self.get_lesson(lesson["id"])
        out["related"] = related
        return out

    def _llm_sections(self, topic, mat, related, title):
        ctx = "\n".join(f"- ({r['title']}) {r['text'][:300]}" for r in related) or "(none)"
        system = ("You turn vetted study material into a structured lesson. Use ONLY the material given; "
                  "do not add facts. Reply with JSON only: "
                  '{"title": str, "sections": [{"heading": str, "key_points": [str, ...], "explanation": str}]}. '
                  "3-8 sections, 2-5 concise key_points each, explanation 2-5 sentences. If the related notes "
                  "overlap or conflict with the material, mention how in the explanation.")
        user = f"Topic: {topic}\n\nMaterial:\n{mat['content'][:12000]}\n\nRelated notes I already have:\n{ctx}"
        data = extract_json(self.llm.chat(system, user, max_tokens=3500))
        secs = [{"heading": str(s["heading"]).strip(),
                 "key_points": [str(p).strip() for p in s.get("key_points", []) if str(p).strip()],
                 "explanation": str(s.get("explanation", "")).strip()}
                for s in data["sections"] if str(s.get("heading", "")).strip()]
        return secs, str(data.get("title") or title)

    def list_lessons(self, plan_id: str | None = None) -> list[dict]:
        if plan_id:
            return self.db.query("SELECT * FROM lessons WHERE plan_id = :p ORDER BY created_at DESC", {"p": plan_id})
        return self.db.query("SELECT * FROM lessons ORDER BY created_at DESC")

    def get_lesson(self, lesson_id: str) -> dict:
        lesson = repo.get(self.db, "lessons", lesson_id)
        if not lesson:
            raise NotFound("lesson not found")
        secs = self.db.query("SELECT * FROM lesson_sections WHERE lesson_id = :l ORDER BY position", {"l": lesson_id})
        for s in secs:
            s["key_points"] = json.loads(s["key_points"] or "[]")
        lesson["sections"] = secs
        return lesson

    def set_section_marked(self, section_id: str, marked: bool) -> dict:
        sec = repo.get(self.db, "lesson_sections", section_id)
        if not sec:
            raise NotFound("section not found")
        repo.update(self.db, "lesson_sections", section_id, {"marked": int(marked)})
        return {**sec, "marked": int(marked), "key_points": json.loads(sec["key_points"] or "[]")}

    def delete_lesson(self, lesson_id: str) -> None:
        self.get_lesson(lesson_id)
        repo.delete_where(self.db, "lesson_sections", "lesson_id", lesson_id)
        repo.delete_where(self.db, "lessons", "id", lesson_id)

    def lesson_html(self, lesson_id: str) -> str:
        return render_lesson_html(self.get_lesson(lesson_id))

    # ------------------------------------------------------------------ cards
    def generate_cards(self, lesson_id: str, auto: bool = False) -> dict:
        """Cards for the sections you marked; `auto` marks and uses every section."""
        lesson = self.get_lesson(lesson_id)
        sections = lesson["sections"] if auto else [s for s in lesson["sections"] if s["marked"]]
        if not sections:
            raise ValueError("No sections marked. Mark sections for flashcards, or use auto-select.")
        existing = {_norm(r["question"]) for r in self.db.query(
            "SELECT question FROM cards WHERE plan_id = :p", {"p": lesson["plan_id"]})}
        plan = self.get_plan(lesson["plan_id"])
        created, skipped = [], 0
        for sec in sections:
            pairs = None
            if self.llm.available:
                try:
                    pairs = self._llm_cards(plan["topic"], sec)
                except (ValueError, KeyError, TypeError):
                    pairs = None
            for q, a in pairs or heuristic_cards(sec):
                if _norm(q) in existing:
                    skipped += 1
                    continue
                existing.add(_norm(q))
                created.append(self._add_card(lesson["plan_id"], plan["topic"], q, a,
                                              lesson_id=lesson_id, section_id=sec["id"],
                                              material_id=lesson["material_id"]))
        return {"created": created, "skipped_duplicates": skipped}

    def _llm_cards(self, topic, sec):
        system = ("You write flashcards for active recall. Reply with JSON only: "
                  '[{"q": str, "a": str}, ...]. 1-4 cards. Each question must be answerable without seeing '
                  "the lesson, test one idea, and have a short, factual answer. Use only the given text.")
        body = f"Topic: {topic}\nSection: {sec['heading']}\nKey points: {sec['key_points']}\n{sec['explanation']}"
        data = extract_json(self.llm.chat(system, body, max_tokens=1200))
        return [(str(c["q"]).strip(), str(c["a"]).strip()) for c in data if str(c["q"]).strip() and str(c["a"]).strip()]

    def add_card(self, plan_id: str, question: str, answer: str, topic: str | None = None) -> dict:
        plan = self.get_plan(plan_id)
        return self._add_card(plan_id, topic or plan["topic"], question, answer)

    def _add_card(self, plan_id, topic, question, answer, lesson_id=None, section_id=None,
                  material_id=None, chunk_id=None) -> dict:
        text = f"Q: {question}\nA: {answer}"
        vec = self.embedder.embed([text])[0]
        if material_id and not chunk_id:  # link back to the best-matching source chunk
            top = self.vectors.query(vec, 1, {"material_id": material_id})
            chunk_id = top[0]["key"] if top else None
        cid = repo.new_id()
        self.vectors.put([{"key": f"card:{cid}", "vector": vec, "metadata": {
            "kind": "card", "plan_id": plan_id, "topic": topic, "title": question, "text": text}}])
        now = repo.now_iso()
        return repo.insert(self.db, "cards", {
            "id": cid, "plan_id": plan_id, "lesson_id": lesson_id, "section_id": section_id,
            "topic": topic, "question": question.strip(), "answer": answer.strip(), "chunk_id": chunk_id,
            "ease": 2.5, "interval_days": 0.0, "reps": 0, "lapses": 0, "due": now, "created_at": now})

    def import_cards(self, plan_topic: str, cards: list[dict], description: str = "") -> dict:
        """Bulk-load ready-made cards ({topic, q, a}) into a plan, creating it if needed."""
        plan = next((p for p in self.list_plans() if p["topic"] == plan_topic), None)
        plan = plan or self.create_plan(plan_topic, description)
        existing = {_norm(r["question"]) for r in self.db.query(
            "SELECT question FROM cards WHERE plan_id = :p", {"p": plan["id"]})}
        fresh = [c for c in cards if _norm(c["q"]) not in existing]
        vecs = self.embedder.embed([f"Q: {c['q']}\nA: {c['a']}" for c in fresh])
        now, items = repo.now_iso(), []
        for c, vec in zip(fresh, vecs):
            cid = repo.new_id()
            topic = c.get("topic") or plan_topic
            items.append({"key": f"card:{cid}", "vector": vec, "metadata": {
                "kind": "card", "plan_id": plan["id"], "topic": topic, "title": c["q"],
                "text": f"Q: {c['q']}\nA: {c['a']}"}})
            repo.insert(self.db, "cards", {
                "id": cid, "plan_id": plan["id"], "lesson_id": None, "section_id": None, "topic": topic,
                "question": c["q"], "answer": c["a"], "chunk_id": None, "ease": 2.5, "interval_days": 0.0,
                "reps": 0, "lapses": 0, "due": now, "created_at": now})
        self.vectors.put(items)
        return {"plan_id": plan["id"], "imported": len(fresh), "skipped_duplicates": len(cards) - len(fresh)}

    def list_cards(self, plan_id: str | None = None, topic: str | None = None) -> list[dict]:
        sql, p = "SELECT * FROM cards WHERE 1 = 1", {}
        if plan_id:
            sql, p["p"] = sql + " AND plan_id = :p", plan_id
        if topic:
            sql, p["t"] = sql + " AND topic = :t", topic
        return self.db.query(sql + " ORDER BY created_at DESC, id", p)

    def update_card(self, card_id: str, fields: dict) -> dict:
        if not repo.get(self.db, "cards", card_id):
            raise NotFound("card not found")
        repo.update(self.db, "cards", card_id, fields)
        card = repo.get(self.db, "cards", card_id)
        if fields.keys() & {"question", "answer"}:  # keep the vector store in step
            text = f"Q: {card['question']}\nA: {card['answer']}"
            self.vectors.put([{"key": f"card:{card_id}", "vector": self.embedder.embed([text])[0], "metadata": {
                "kind": "card", "plan_id": card["plan_id"], "topic": card["topic"],
                "title": card["question"], "text": text}}])
        return card

    def delete_card(self, card_id: str) -> None:
        if not repo.get(self.db, "cards", card_id):
            raise NotFound("card not found")
        self.vectors.delete([f"card:{card_id}"])
        repo.delete_where(self.db, "reviews", "card_id", card_id)
        repo.delete_where(self.db, "cards", "id", card_id)

    def card_source(self, card_id: str) -> dict:
        """The vector-store entry a card is grounded in (its source chunk, or the card itself)."""
        card = repo.get(self.db, "cards", card_id)
        if not card:
            raise NotFound("card not found")
        key = card["chunk_id"] or f"card:{card_id}"
        found = self.vectors.get([key])
        if not found:
            return {"key": key, "title": None, "text": None}
        m = found[0]["metadata"]
        return {"key": key, "title": m.get("title"), "text": m.get("text"), "material_id": m.get("material_id")}

    # ----------------------------------------------------------------- review
    def review_queue(self, plan_id: str | None = None, topic: str | None = None, mode: str = "due",
                     limit: int = 50, new_limit: int = 20) -> list[dict]:
        sql, p = "SELECT * FROM cards WHERE 1 = 1", {"now": repo.now_iso()}
        if plan_id:
            sql, p["p"] = sql + " AND plan_id = :p", plan_id
        if topic:
            sql, p["t"] = sql + " AND topic = :t", topic
        cards = self.db.query(sql + " ORDER BY due, id", p)
        if mode == "all":
            random.shuffle(cards)
            return cards[:limit]
        due = [c for c in cards if (c["reps"] or 0) > 0 or (c["lapses"] or 0) > 0]
        due = [c for c in due if c["due"] <= p["now"]]
        new = [c for c in cards if not c["reps"] and not c["lapses"]][:new_limit]
        return (due + new)[:limit]

    def review(self, card_id: str, grade: int) -> dict:
        card = repo.get(self.db, "cards", card_id)
        if not card:
            raise NotFound("card not found")
        now = datetime.now(timezone.utc)
        nxt = schedule(card["ease"], card["interval_days"], card["reps"], card["lapses"], grade, now)
        repo.update(self.db, "cards", card_id, {**nxt, "due": repo.fmt_iso(nxt["due"])})
        repo.insert(self.db, "reviews", {
            "id": repo.new_id(), "card_id": card_id, "grade": grade, "correct": int(grade >= GOOD),
            "reviewed_at": repo.fmt_iso(now)})
        return repo.get(self.db, "cards", card_id)

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        week = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        month = (now - timedelta(days=29)).strftime("%Y-%m-%d")
        one = lambda sql, p=None: (self.db.query(sql, p or {})[0] or {})
        total = one("SELECT COUNT(*) AS n FROM cards")["n"] or 0
        due = one("SELECT COUNT(*) AS n FROM cards WHERE due <= :now AND (reps > 0 OR lapses > 0)",
                  {"now": repo.fmt_iso(now)})["n"] or 0
        new = one("SELECT COUNT(*) AS n FROM cards WHERE reps = 0 AND lapses = 0")["n"] or 0
        made7 = one("SELECT COUNT(*) AS n FROM cards WHERE substr(created_at, 1, 10) >= :d", {"d": week})["n"] or 0
        rev = self.db.query("SELECT substr(reviewed_at, 1, 10) AS day, grade FROM reviews WHERE substr(reviewed_at, 1, 10) >= :d",
                            {"d": month})
        by_day = Counter(r["day"] for r in rev)
        last30 = [r for r in rev]
        retention = (sum(1 for r in last30 if r["grade"] >= 2) / len(last30)) if last30 else None
        days = {r["day"] for r in self.db.query("SELECT DISTINCT substr(reviewed_at, 1, 10) AS day FROM reviews")}
        days |= {r["date"] for r in self.db.query("SELECT DISTINCT date FROM study_sessions")}
        streak, d = 0, now.date()
        if d.isoformat() not in days:
            d -= timedelta(days=1)
        while d.isoformat() in days:
            streak, d = streak + 1, d - timedelta(days=1)
        return {
            "cards_total": total, "cards_due": due, "cards_new": new, "cards_made_7d": made7,
            "reviews_7d": sum(n for day, n in by_day.items() if day >= week), "reviews_today": by_day.get(today, 0),
            "retention_30d": retention, "streak_days": streak,
            "reviews_by_day": [{"day": (now - timedelta(days=i)).strftime("%Y-%m-%d"),
                                "n": by_day.get((now - timedelta(days=i)).strftime("%Y-%m-%d"), 0)}
                               for i in range(13, -1, -1)],
        }

    # -------------------------------------------------------------------- log
    def log_list(self) -> list[dict]:
        return self.db.query("SELECT * FROM study_sessions ORDER BY date DESC, created_at DESC")

    def log_add(self, row: dict) -> dict:
        return repo.insert(self.db, "study_sessions", {**row, "id": repo.new_id(), "created_at": repo.now_iso()})

    def log_update(self, log_id: str, fields: dict) -> dict:
        if not repo.get(self.db, "study_sessions", log_id):
            raise NotFound("session not found")
        repo.update(self.db, "study_sessions", log_id, fields)
        return repo.get(self.db, "study_sessions", log_id)

    def log_delete(self, log_id: str) -> None:
        if not repo.get(self.db, "study_sessions", log_id):
            raise NotFound("session not found")
        repo.delete_where(self.db, "study_sessions", "id", log_id)


# ---------------------------------------------------------------- pure helpers
def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


_HEADING = re.compile(r"^#{1,4}\s+(.*)$", re.M)


def heuristic_sections(text: str) -> list[dict]:
    """Structure plain text without an LLM: markdown headings if present, else paragraph groups."""
    parts: list[tuple[str, str]] = []
    marks = list(_HEADING.finditer(text))
    if marks:
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            body = text[m.end():end].strip()
            if body:
                parts.append((m.group(1).strip(), body))
    else:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
        for i in range(0, len(paras), 2):
            body = "\n\n".join(paras[i:i + 2])
            first = split_sentences(body)[0] if body else ""
            head = " ".join(first.split()[:8]).rstrip(".,;:") + ("…" if len(first.split()) > 8 else "")
            parts.append((head or f"Part {len(parts) + 1}", body))
    out = []
    for head, body in parts:
        sents = split_sentences(re.sub(r"^[-*]\s+", "", body, flags=re.M).replace("\n", " "))
        out.append({"heading": head, "key_points": [s for s in sents[:3]], "explanation": body})
    return out


_DEF = re.compile(r"^(?P<term>[A-Z][^.:;]{2,60}?)\s+(?:is|are|means|refers to)\s+(?P<rest>.+)$")


def heuristic_cards(sec: dict) -> list[tuple[str, str]]:
    """No-LLM card drafting: 'X is Y' sentences become 'What is X?'; otherwise one card per section."""
    pairs = []
    for point in sec["key_points"] or split_sentences(sec["explanation"])[:3]:
        m = _DEF.match(point)
        if m:
            term = re.sub(r"^(The|A|An)\s+", "", m.group("term").strip())
            pairs.append((f"What {'are' if ' are ' in point[:80] else 'is'} {term}?", point))
    if not pairs:
        answer = " ".join(sec["key_points"]) or sec["explanation"][:400]
        pairs.append((f"Explain: {sec['heading']}", answer))
    return pairs[:4]


def render_lesson_html(lesson: dict) -> str:
    e = html.escape
    body = []
    for s in lesson["sections"]:
        pts = "".join(f"<li>{e(p)}</li>" for p in s["key_points"])
        star = " ★" if s["marked"] else ""
        body.append(f"<section><h2>{e(s['heading'])}{star}</h2><ul>{pts}</ul><p>{e(s['explanation'])}</p></section>")
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{e(lesson['title'])}</title><style>"
        ":root{color-scheme:light dark}body{font:16px/1.55 system-ui,sans-serif;max-width:720px;margin:0 auto;padding:20px 16px}"
        "section{margin:0 0 24px}h2{font-size:1.15rem;margin:0 0 8px}li{margin:4px 0}"
        f"</style></head><body><h1>{e(lesson['title'])}</h1>{''.join(body)}</body></html>"
    )
