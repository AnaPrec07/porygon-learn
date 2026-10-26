# Porygon Learn

A personal app for planning and tracking what you learn: define a plan, store your study
material in a **vector store on S3**, turn it into lessons, drill it with **flashcards on a
spaced-repetition schedule**, and search everything you've studied semantically.

Relational data lives in **Databricks Delta tables**; vectors live in **Amazon S3 Vectors**;
embeddings and the optional LLM come from **Databricks Model Serving** (or Bedrock).
The UI reuses your *Databricks Flashcards* and *Learning log* pages.

```
 Browser (static HTML/JS)  ──►  FastAPI (Lightsail / any container host)
                                   │
        ┌──────────────────────────┼───────────────────────────┐
        ▼                          ▼                           ▼
 Delta tables (Unity Catalog)   Amazon S3 Vectors          Model Serving
 plans, points, materials,      chunk text + embedding,     embeddings, lesson/card
 lessons, cards (SRS state),    cards too (kind=card),      drafting, research
 reviews, study log             filter by plan/kind         (Databricks or Bedrock)
 via SQL warehouse              (cosine index)
```

## The learning loop

1. **Plan** – a topic plus the points you want to cover.
2. **Material** – paste text (or **Research with AI**, saved as *unverified* until you tick it as verified).
   It is chunked, embedded and written to the vector store with `plan_id`/`topic`/`material_id` metadata.
3. **Lesson** – structured sections (key points + explanation). With an LLM it also pulls the closest passages
   from your *other* notes to compare and contrast; without one, headings/paragraphs are used.
4. **Flashcards** – star the sections worth remembering, or auto-select all. Each card is embedded too and
   linked back to its best-matching source chunk (**Show source** during review).
5. **Review** – Again / Hard / Good / Easy (keys `1`-`4`, `Space` flips), SM-2 style scheduling.
6. **Search** – semantic search across material and cards.
7. **Log** – the manual session log from your Learning log page (time, resource, cards made/studied).

Your 136-card Databricks deck ships in `seed/` – click **Load Databricks starter deck** on the Today page.

## Run it locally (no accounts needed)

```bash
pip install -r requirements.txt
cd backend && uvicorn app.main:app_factory --factory --reload
# open http://localhost:8000
python -m pytest            # from backend/
```

With no configuration it uses SQLite + a JSON vector file under `./data` and a keyword-hashing
embedder. That is enough to try everything, but **search only matches keywords**; switch embeddings to
Databricks or Bedrock for semantic search.

## Switch on Databricks + AWS (one piece at a time)

See `.env.example` for every variable.

| Piece | Setting | What you need |
|---|---|---|
| Delta tables | `STORE_BACKEND=databricks` | SQL warehouse ID, a token, and `CREATE SCHEMA`/`CREATE TABLE` on the catalog. Tables are created on first start (`python scripts/print_databricks_ddl.py` prints the DDL). |
| S3 vector store | `VECTOR_BACKEND=s3vectors` | `S3_VECTOR_BUCKET`, `AWS_REGION`, credentials with `infra/iam-policy.json`. Bucket and cosine index are created if missing. |
| Embeddings | `EMBED_BACKEND=databricks` (`EMBED_ENDPOINT`, default `databricks-gte-large-en`, 1024 dims) or `bedrock` | Set `EMBED_DIM` to match the model. |
| LLM | `LLM_BACKEND=databricks` or `bedrock` | Enables Research, LLM lessons and card writing. Any chat-style serving endpoint works. |

**The vector index dimension is fixed at creation.** If you change embedding model, use a new
`S3_VECTOR_INDEX` and re-add material (the app refuses mismatches rather than corrupting the index).

## Deploy (Lightsail container service or any Docker host)

```bash
docker build -t porygon-learn .
docker run -p 8000:8000 --env-file .env -v porygon-data:/data porygon-learn
```

**Set `APP_TOKEN` before exposing it publicly**; the API then requires `Authorization: Bearer <token>`
(the UI prompts for it). Use a Databricks service-principal token with access to just this schema, and an
IAM role scoped to the vector bucket. Lightsail has no instance role for containers, so use scoped access keys there,
or run on EC2/App Runner with a role if you prefer.

## Layout

```
backend/app/   config, schema (single source of DDL), db (SQLite + Databricks SQL API),
               embeddings, vectorstore (local + S3 Vectors), llm, core (the workflow), main (API)
backend/tests/ API flow, LLM path + fallbacks, SRS maths, S3 Vectors and Databricks adapters (faked)
frontend/      index.html, style.css, app.js (no build step)
seed/          your Databricks flashcards as JSON
infra/         IAM policy
```

## Honest status

* The local path is tested end to end (API tests plus a browser run through every screen).
* The **Databricks SQL, S3 Vectors, Bedrock and Databricks serving adapters are tested only against
  fakes** that mirror the documented request/response shapes. They have not been run against live accounts, so expect to
  tweak small details (permissions, endpoint names, region support for S3 Vectors) on first connection.
* LLM-written notes and cards are not fact-checked: research output is flagged *unverified* until you decide otherwise.
* Single-user by design (one shared token), no multi-tenant isolation.
