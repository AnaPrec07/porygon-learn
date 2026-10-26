"""FastAPI app: JSON API under /api and the static frontend at /."""
from __future__ import annotations

import hmac
import json
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .core import Learning, NotFound
from .llm import LLMUnavailable

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
SEED = ROOT / "seed" / "databricks_flashcards.json"


class PlanIn(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    points: list[str] = []


class PlanPatch(BaseModel):
    topic: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    status: Literal["active", "paused", "done"] | None = None


class PointIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class PointPatch(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=500)
    done: bool | None = None


class MaterialIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=200_000)
    source_url: str = Field("", max_length=1000)
    source_type: Literal["note", "article", "course", "book", "video", "docs"] = "note"
    status: Literal["unverified", "verified"] = "unverified"


class MaterialPatch(BaseModel):
    status: Literal["unverified", "verified"]


class ResearchIn(BaseModel):
    focus: str = Field("", max_length=300)


class SectionPatch(BaseModel):
    marked: bool


class GenerateCardsIn(BaseModel):
    auto: bool = False


class CardIn(BaseModel):
    plan_id: str
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=4000)
    topic: str | None = None


class CardPatch(BaseModel):
    question: str | None = Field(None, min_length=1, max_length=1000)
    answer: str | None = Field(None, min_length=1, max_length=4000)
    topic: str | None = None


class ReviewIn(BaseModel):
    card_id: str
    grade: int = Field(ge=0, le=3)  # 0 again, 1 hard, 2 good, 3 easy


class SessionIn(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    minutes: int = Field(0, ge=0, le=1440)
    category: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=200)
    resource: str = Field(min_length=1, max_length=200)
    made: int = Field(0, ge=0)
    studied: int = Field(0, ge=0)


def create_app(settings: Settings | None = None, learning: Learning | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Porygon Learn", version="0.1.0")
    svc = learning or Learning(settings)

    def auth(authorization: str | None = Header(None)) -> None:
        if not settings.app_token:
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, settings.app_token):
            raise HTTPException(401, "invalid or missing token")

    def guard(fn, *args, **kwargs):
        """Map domain errors onto HTTP codes."""
        try:
            return fn(*args, **kwargs)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except LLMUnavailable as e:
            raise HTTPException(501, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    api = FastAPI()  # mounted at /api so auth applies to every route in one place
    deps = [Depends(auth)]

    @api.get("/health")
    def health():  # open: lets the UI learn whether it needs a token
        return {"ok": True, **settings.public(), "llm_available": svc.llm.available}

    # plans -----------------------------------------------------------------
    @api.get("/plans", dependencies=deps)
    def plans():
        return svc.list_plans()

    @api.post("/plans", dependencies=deps, status_code=201)
    def plan_create(body: PlanIn):
        return svc.create_plan(body.topic, body.description, body.points)

    @api.get("/plans/{plan_id}", dependencies=deps)
    def plan_get(plan_id: str):
        return guard(svc.get_plan, plan_id)

    @api.patch("/plans/{plan_id}", dependencies=deps)
    def plan_patch(plan_id: str, body: PlanPatch):
        return guard(svc.update_plan, plan_id, body.model_dump(exclude_none=True))

    @api.delete("/plans/{plan_id}", dependencies=deps, status_code=204)
    def plan_delete(plan_id: str):
        guard(svc.delete_plan, plan_id)

    @api.post("/plans/{plan_id}/points", dependencies=deps, status_code=201)
    def point_add(plan_id: str, body: PointIn):
        return guard(svc.add_point, plan_id, body.text)

    @api.patch("/points/{point_id}", dependencies=deps)
    def point_patch(point_id: str, body: PointPatch):
        fields = body.model_dump(exclude_none=True)
        if "done" in fields:
            fields["done"] = int(fields["done"])
        return guard(svc.update_point, point_id, fields)

    # materials ---------------------------------------------------------------
    @api.post("/plans/{plan_id}/materials", dependencies=deps, status_code=201)
    def material_add(plan_id: str, body: MaterialIn):
        return guard(svc.add_material, plan_id, body.title, body.text, body.source_url,
                     body.source_type, body.status)

    @api.post("/plans/{plan_id}/research", dependencies=deps, status_code=201)
    def research(plan_id: str, body: ResearchIn):
        return guard(svc.research, plan_id, body.focus)

    @api.get("/materials/{material_id}", dependencies=deps)
    def material_get(material_id: str):
        return guard(svc.get_material, material_id)

    @api.patch("/materials/{material_id}", dependencies=deps)
    def material_patch(material_id: str, body: MaterialPatch):
        return guard(svc.set_material_status, material_id, body.status)

    @api.delete("/materials/{material_id}", dependencies=deps, status_code=204)
    def material_delete(material_id: str):
        guard(svc.delete_material, material_id)

    # search ------------------------------------------------------------------
    @api.get("/search", dependencies=deps)
    def search(q: str = Query(min_length=1, max_length=500), plan_id: str | None = None,
               kind: Literal["material", "card"] | None = None, k: int = Query(8, ge=1, le=50)):
        return svc.search(q, plan_id, kind, k)

    # lessons -----------------------------------------------------------------
    @api.post("/materials/{material_id}/lesson", dependencies=deps, status_code=201)
    def lesson_build(material_id: str):
        return guard(svc.build_lesson, material_id)

    @api.get("/lessons", dependencies=deps)
    def lessons(plan_id: str | None = None):
        return svc.list_lessons(plan_id)

    @api.get("/lessons/{lesson_id}", dependencies=deps)
    def lesson_get(lesson_id: str):
        return guard(svc.get_lesson, lesson_id)

    @api.get("/lessons/{lesson_id}/html", dependencies=deps, response_class=HTMLResponse)
    def lesson_html(lesson_id: str):
        return guard(svc.lesson_html, lesson_id)

    @api.delete("/lessons/{lesson_id}", dependencies=deps, status_code=204)
    def lesson_delete(lesson_id: str):
        guard(svc.delete_lesson, lesson_id)

    @api.patch("/sections/{section_id}", dependencies=deps)
    def section_patch(section_id: str, body: SectionPatch):
        return guard(svc.set_section_marked, section_id, body.marked)

    @api.post("/lessons/{lesson_id}/cards", dependencies=deps, status_code=201)
    def cards_generate(lesson_id: str, body: GenerateCardsIn):
        return guard(svc.generate_cards, lesson_id, body.auto)

    # cards + review ------------------------------------------------------------
    @api.get("/cards", dependencies=deps)
    def cards(plan_id: str | None = None, topic: str | None = None):
        return svc.list_cards(plan_id, topic)

    @api.post("/cards", dependencies=deps, status_code=201)
    def card_add(body: CardIn):
        return guard(svc.add_card, body.plan_id, body.question, body.answer, body.topic)

    @api.patch("/cards/{card_id}", dependencies=deps)
    def card_patch(card_id: str, body: CardPatch):
        return guard(svc.update_card, card_id, body.model_dump(exclude_none=True))

    @api.delete("/cards/{card_id}", dependencies=deps, status_code=204)
    def card_delete(card_id: str):
        guard(svc.delete_card, card_id)

    @api.get("/cards/{card_id}/source", dependencies=deps)
    def card_source(card_id: str):
        return guard(svc.card_source, card_id)

    @api.get("/review/queue", dependencies=deps)
    def queue(plan_id: str | None = None, topic: str | None = None,
              mode: Literal["due", "all"] = "due", limit: int = Query(50, ge=1, le=500)):
        return svc.review_queue(plan_id, topic, mode, limit)

    @api.post("/review", dependencies=deps)
    def review(body: ReviewIn):
        return guard(svc.review, body.card_id, body.grade)

    @api.get("/stats", dependencies=deps)
    def stats():
        return svc.stats()

    # study log -----------------------------------------------------------------
    @api.get("/log", dependencies=deps)
    def log_list():
        return svc.log_list()

    @api.post("/log", dependencies=deps, status_code=201)
    def log_add(body: SessionIn):
        return svc.log_add(body.model_dump())

    @api.put("/log/{log_id}", dependencies=deps)
    def log_put(log_id: str, body: SessionIn):
        return guard(svc.log_update, log_id, body.model_dump())

    @api.delete("/log/{log_id}", dependencies=deps, status_code=204)
    def log_delete(log_id: str):
        guard(svc.log_delete, log_id)

    # starter deck --------------------------------------------------------------
    @api.post("/seed/databricks", dependencies=deps)
    def seed():
        data = json.loads(SEED.read_text())
        return svc.import_cards(data["plan"], data["cards"], data.get("description", ""))

    app.mount("/api", api)
    if FRONTEND.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
    return app


def app_factory() -> FastAPI:  # `uvicorn app.main:app_factory --factory`
    return create_app()
