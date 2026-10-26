(function () {
  "use strict";
  const app = document.getElementById("app");
  let health = {};
  let token = safeGet("porygon-token") || "";
  const lastRelated = {}; // lessonId -> related hits from the build response

  /* ---------- helpers ---------- */
  function safeGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function safeSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "class") el.className = v;
      else if (k === "for" || k === "list" || k.includes("-")) el.setAttribute(k, v);
      else if (k in el) el[k] = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
    return el;
  }
  const clear = (el) => { el.textContent = ""; return el; };
  // like Element.append, but flattens arrays and skips null/false
  const mount = (...nodes) => app.append(...nodes.flat(Infinity).filter((n) => n != null && n !== false));

  let toastTimer;
  function toast(msg, isErr) {
    const t = document.getElementById("toast");
    t.textContent = msg; t.className = "toast" + (isErr ? " err" : ""); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), isErr ? 6000 : 2500);
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = "Bearer " + token;
    const res = await fetch("/api" + path, {
      method: opts.method || "GET", headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    if (res.status === 401) { renderLogin(); throw new Error("Sign in required"); }
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : (j.detail || []).map((d) => d.msg).join("; "); } catch (e) {}
      throw new Error(msg);
    }
    return res.status === 204 ? null : res.json();
  }
  // run an async action, surface failures as toasts
  async function run(fn) { try { return await fn(); } catch (e) { if (e.message !== "Sign in required") toast(e.message, true); return undefined; } }

  const today = () => { const d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
  const fmtMin = (n) => !n ? "0 min" : n < 60 ? n + " min" : Math.floor(n / 60) + " h" + (n % 60 ? " " + (n % 60) + " min" : "");
  const fmtDate = (s) => { const p = s.split("-"); return new Date(+p[0], +p[1] - 1, +p[2]).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" }); };
  const plural = (n, w) => n + " " + w + (n === 1 ? "" : "s");

  function confirmBtn(label, onConfirm, cls) {
    const b = h("button", { type: "button", class: cls || "link del" }, label);
    b.addEventListener("click", () => {
      if (b.dataset.armed) return onConfirm();
      b.dataset.armed = "1"; b.textContent = "Tap again to confirm";
      setTimeout(() => { delete b.dataset.armed; b.textContent = label; }, 3000);
    });
    return b;
  }

  function field(label, input, cls, hint) {
    const id = "f" + Math.random().toString(36).slice(2, 8);
    input.id = id;
    return h("div", { class: "f " + (cls || "") }, h("label", { for: id }, label, hint ? h("span", { class: "hint" }, " " + hint) : null), input);
  }

  function head(title, sub, ...actions) {
    return h("div", { class: "page-head" }, h("div", null, h("h1", null, title), sub ? h("p", { class: "sub" }, sub) : null), h("div", { class: "row" }, actions));
  }

  /* ---------- router ---------- */
  const routes = {
    today: viewToday, plans: viewPlans, plan: viewPlan, lesson: viewLesson,
    search: viewSearch, review: viewReview, log: viewLog,
  };
  async function route() {
    const [path, qs] = (location.hash.slice(2) || "today").split("?");
    const [name, arg] = path.split("/");
    const params = Object.fromEntries(new URLSearchParams(qs || ""));
    document.querySelectorAll("#nav a").forEach((a) => {
      const on = a.dataset.nav === name || (a.dataset.nav === "plans" && (name === "plan" || name === "lesson"));
      on ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
    });
    window.scrollTo(0, 0);
    detachKeys();
    const view = routes[name] || viewToday;
    clear(app);
    await run(() => view(arg, params));
    refreshBadge();
  }
  async function refreshBadge() {
    const s = await run(() => api("/stats"));
    const b = document.getElementById("due-badge");
    if (s) { b.textContent = s.cards_due + s.cards_new > 0 ? Math.min(99, s.cards_due + Math.min(s.cards_new, 20)) : ""; b.hidden = !b.textContent; }
  }
  let keyHandler = null;
  function attachKeys(fn) { keyHandler = fn; document.addEventListener("keydown", fn); }
  function detachKeys() { if (keyHandler) document.removeEventListener("keydown", keyHandler); keyHandler = null; }

  function renderLogin() {
    clear(app).append(h("div", { class: "login panel" },
      h("h2", null, "Sign in"),
      h("p", { class: "muted" }, "This server needs an access token (APP_TOKEN)."),
      h("form", { onSubmit: (e) => { e.preventDefault(); token = e.target.t.value.trim(); safeSet("porygon-token", token); route(); } },
        field("Access token", h("input", { type: "password", name: "t", required: true, autocomplete: "current-password" })),
        h("div", { class: "actions", style: "margin-top:12px" }, h("button", { class: "btn", type: "submit" }, "Continue")))));
  }

  /* ---------- Today ---------- */
  async function viewToday() {
    const [s, plans] = await Promise.all([api("/stats"), api("/plans")]);
    const ready = s.cards_due + Math.min(s.cards_new, 20);
    mount(
      head("Today", "Plan it, learn it, recall it."),
      h("div", { class: "cards" },
        h("div", { class: "index-card made" },
          h("div", { class: "label" }, "Ready to review"), h("div", { class: "big" }, String(ready)),
          h("div", { class: "note" }, plural(s.cards_due, "card") + " due, " + plural(s.cards_new, "new card")),
          h("a", { class: "btn small", href: "#/review", style: "text-decoration:none;display:inline-flex;align-items:center" }, ready ? "Start review" : "Open review")),
        h("div", { class: "index-card studied" },
          h("div", { class: "label" }, "Reviews, last 7 days"), h("div", { class: "big" }, String(s.reviews_7d)),
          h("div", { class: "note" }, plural(s.cards_made_7d, "card") + " made this week"))),
      h("div", { class: "mini" },
        h("div", null, h("b", null, s.retention_30d == null ? "-" : Math.round(s.retention_30d * 100) + "%"), h("span", null, "Recall (30 days)")),
        h("div", null, h("b", null, String(s.streak_days)), h("span", null, s.streak_days === 1 ? "Day streak" : "Days streak")),
        h("div", null, h("b", null, String(s.cards_total)), h("span", null, "Total cards"))),
      h("section", { class: "panel", "aria-labelledby": "act" }, h("h2", { id: "act" }, "Reviews, last 14 days"),
        h("div", { class: "spark", role: "img", "aria-label": "Reviews per day for the last 14 days" },
          s.reviews_by_day.map((d) => {
            const max = Math.max(1, ...s.reviews_by_day.map((x) => x.n));
            return h("i", { class: d.n ? "" : "zero", style: "height:" + Math.max(5, Math.round((d.n / max) * 100)) + "%", title: d.day + ": " + d.n });
          }))),
      h("section", { class: "panel" }, h("h2", null, "Learning plans"),
        plans.length ? plans.map((p) => h("div", { class: "item" },
          h("div", { class: "item-top" }, h("a", { class: "title", href: "#/plan/" + p.id }, p.topic),
            h("span", { class: "chips" }, h("span", { class: "chip" }, plural(p.card_count, "card")), p.due_count ? h("span", { class: "chip warn" }, p.due_count + " due") : null, p.new_count ? h("span", { class: "chip" }, p.new_count + " new") : null)),
          p.description ? h("div", { class: "meta" }, p.description) : null))
          : h("div", null, h("p", { class: "empty" }, "No plans yet. Start one, or load the Databricks starter deck from your flashcards page."),
            h("div", { class: "row" },
              h("a", { class: "btn", href: "#/plans", style: "text-decoration:none" }, "Create a plan"),
              h("button", { class: "btn ghost", type: "button", onClick: () => run(async () => { const r = await api("/seed/databricks", { method: "POST" }); toast("Imported " + r.imported + " cards"); route(); }) }, "Load Databricks starter deck"))))
    );
  }

  /* ---------- Plans ---------- */
  async function viewPlans() {
    const plans = await api("/plans");
    const form = h("form", { class: "grid", autocomplete: "off", onSubmit: (e) => {
      e.preventDefault();
      const f = e.target;
      run(async () => {
        const p = await api("/plans", { method: "POST", body: { topic: f.topic.value.trim(), description: f.desc.value.trim(), points: f.points.value.split("\n").map((x) => x.trim()).filter(Boolean) } });
        location.hash = "#/plan/" + p.id;
      });
    } },
      field("Topic", h("input", { name: "topic", required: true, maxLength: 200, placeholder: "e.g. Databricks vector search" })),
      field("Description", h("input", { name: "desc", maxLength: 2000, placeholder: "Why are you learning this?" }), "", "(optional)"),
      field("Points to learn", h("textarea", { name: "points", placeholder: "One per line\nWhat is a vector index?\nHow does filtering work?" }), "", "(optional)"),
      h("div", { class: "actions" }, h("button", { class: "btn", type: "submit" }, "Create plan")));
    mount(head("Plans", "Define what you want to learn, then add material to study."),
      h("section", { class: "panel" }, h("h2", null, "New plan"), form),
      h("section", { class: "panel" }, h("h2", null, "Your plans"),
        plans.length ? plans.map((p) => h("div", { class: "item" },
          h("div", { class: "item-top" }, h("a", { class: "title", href: "#/plan/" + p.id }, p.topic), h("span", { class: "chip" }, p.status)),
          h("div", { class: "meta" }, plural(p.card_count, "card") + (p.due_count ? ", " + p.due_count + " due" : "") + (p.new_count ? ", " + p.new_count + " new" : "")))) : h("p", { class: "empty" }, "Nothing here yet.")));
  }

  async function viewPlan(id) {
    const [plan, cards] = await Promise.all([api("/plans/" + id), api("/cards?plan_id=" + id)]);
    const reload = () => route();

    const pointsBox = h("div", null, plan.points.length ? plan.points.map((pt) =>
      h("label", { class: "check" + (pt.done ? " done" : ""), style: "font-weight:400" },
        h("input", { type: "checkbox", checked: !!pt.done, onChange: (e) => run(() => api("/points/" + pt.id, { method: "PATCH", body: { done: e.target.checked } }).then(reload)) }),
        h("span", null, pt.text))) : h("p", { class: "empty" }, "No points yet."),
      h("form", { class: "row", style: "margin-top:8px", onSubmit: (e) => { e.preventDefault(); const v = e.target.t.value.trim(); if (v) run(() => api("/plans/" + id + "/points", { method: "POST", body: { text: v } }).then(reload)); } },
        h("input", { name: "t", class: "grow", placeholder: "Add a point", "aria-label": "Add a point" }), h("button", { class: "btn ghost small", type: "submit" }, "Add")));

    const matForm = h("form", { class: "grid", autocomplete: "off", onSubmit: (e) => {
      e.preventDefault(); const f = e.target;
      run(async () => {
        await api("/plans/" + id + "/materials", { method: "POST", body: { title: f.title.value.trim(), text: f.text.value, source_url: f.url.value.trim(), source_type: f.stype.value, status: f.vstat.value } });
        toast("Embedded and saved to the vector store"); reload();
      });
    } },
      field("Title", h("input", { name: "title", required: true, maxLength: 200, placeholder: "e.g. Vector Search docs" }), "half"),
      field("Source URL", h("input", { name: "url", type: "url", maxLength: 1000, placeholder: "https://" }), "half", "(optional)"),
      field("Type", h("select", { name: "stype" }, ["note", "article", "docs", "course", "book", "video"].map((t) => h("option", { value: t }, t))), "half"),
      field("Trust", h("select", { name: "vstat" }, h("option", { value: "unverified" }, "Unverified"), h("option", { value: "verified" }, "Verified (I checked it)")), "half"),
      field("Material", h("textarea", { name: "text", required: true, rows: 8, placeholder: "Paste the text you are studying. Use ## headings to shape lesson sections." })),
      h("div", { class: "actions" }, h("button", { class: "btn", type: "submit" }, "Save to vector store")));

    const researchRow = health.llm_available ? h("form", { class: "row", style: "margin-bottom:14px", onSubmit: (e) => {
      e.preventDefault(); const btn = e.target.querySelector("button"); btn.disabled = true; btn.textContent = "Researching...";
      run(() => api("/plans/" + id + "/research", { method: "POST", body: { focus: e.target.focus.value.trim() } }).then(() => { toast("Notes saved as unverified"); reload(); })).finally(() => { btn.disabled = false; btn.textContent = "Research with AI"; });
    } }, h("input", { name: "focus", class: "grow", placeholder: "Focus (optional)", "aria-label": "Research focus" }), h("button", { class: "btn ghost", type: "submit" }, "Research with AI")) : null;

    mount(
      head(plan.topic, plan.description, h("a", { class: "btn ghost", href: "#/review?plan=" + id, style: "text-decoration:none" }, "Review cards"),
        confirmBtn("Delete plan", () => run(() => api("/plans/" + id, { method: "DELETE" }).then(() => (location.hash = "#/plans"))), "btn danger small")),
      h("section", { class: "panel" }, h("h2", null, "Points to learn"), pointsBox),
      h("section", { class: "panel" }, h("h2", null, "Material"),
        plan.materials.length ? plan.materials.map((m) => h("div", { class: "item" },
          h("div", { class: "item-top" }, h("span", { class: "title" }, m.title), h("span", { class: "chips" },
            h("button", { class: "chip " + (m.status === "verified" ? "ok" : "warn"), type: "button", title: "Toggle verified", onClick: () => run(() => api("/materials/" + m.id, { method: "PATCH", body: { status: m.status === "verified" ? "unverified" : "verified" } }).then(reload)) }, m.status),
            h("span", { class: "chip" }, m.source_type), h("span", { class: "chip" }, plural(m.chunk_count, "chunk")))),
          m.source_url ? h("div", { class: "meta" }, h("a", { href: m.source_url, target: "_blank", rel: "noopener noreferrer" }, m.source_url)) : null,
          h("div", { class: "row" },
            h("button", { class: "btn small", type: "button", onClick: (e) => { e.target.disabled = true; run(() => api("/materials/" + m.id + "/lesson", { method: "POST" }).then((l) => { lastRelated[l.id] = l.related; location.hash = "#/lesson/" + l.id; })).finally(() => (e.target.disabled = false)); } }, "Build lesson"),
            confirmBtn("Delete", () => run(() => api("/materials/" + m.id, { method: "DELETE" }).then(reload)))))) : h("p", { class: "empty" }, "Add material below. It is chunked, embedded and stored in the vector store."),
        researchRow, h("h3", { style: "margin:12px 0" }, "Add material"), matForm),
      h("section", { class: "panel" }, h("h2", null, "Lessons"),
        plan.lessons.length ? plan.lessons.map((l) => h("div", { class: "item" }, h("a", { class: "title", href: "#/lesson/" + l.id }, l.title), h("div", { class: "meta" }, new Date(l.created_at).toLocaleDateString()))) : h("p", { class: "empty" }, "Build a lesson from a piece of material.")),
      h("section", { class: "panel" }, h("h2", null, "Flashcards (" + cards.length + ")"),
        cards.length ? h("details", null, h("summary", null, "Show all cards"),
          cards.map((c) => h("div", { class: "item" }, h("div", { class: "item-top" }, h("span", { class: "title" }, c.question), confirmBtn("Delete", () => run(() => api("/cards/" + c.id, { method: "DELETE" }).then(reload)))), h("div", { class: "meta" }, c.answer)))) : h("p", { class: "empty" }, "No cards yet."),
        h("h3", { style: "margin:14px 0 8px" }, "Add a card by hand"),
        h("form", { class: "grid", autocomplete: "off", onSubmit: (e) => { e.preventDefault(); const f = e.target; run(() => api("/cards", { method: "POST", body: { plan_id: id, question: f.q.value.trim(), answer: f.a.value.trim() } }).then(reload)); } },
          field("Question", h("input", { name: "q", required: true, maxLength: 1000 }), "half"), field("Answer", h("input", { name: "a", required: true, maxLength: 4000 }), "half"),
          h("div", { class: "actions" }, h("button", { class: "btn ghost", type: "submit" }, "Add card")))));
  }

  /* ---------- Lesson ---------- */
  async function viewLesson(id) {
    const lesson = await api("/lessons/" + id);
    const reload = () => route();
    const marked = () => lesson.sections.filter((s) => s.marked).length;
    const genBtn = h("button", { class: "btn", type: "button", disabled: !marked() }, "Make cards from " + marked() + " marked");
    const gen = (auto) => run(async () => {
      const r = await api("/lessons/" + id + "/cards", { method: "POST", body: { auto } });
      toast(plural(r.created.length, "card") + " created" + (r.skipped_duplicates ? " (" + plural(r.skipped_duplicates, "duplicate") + " skipped)" : "")); reload();
    });
    genBtn.addEventListener("click", () => gen(false));
    const related = lastRelated[id] || [];
    mount(
      head(lesson.title, "Mark the sections worth remembering, or let auto-select pick them all.",
        h("a", { class: "btn ghost small", href: "#/plan/" + lesson.plan_id, style: "text-decoration:none" }, "Back to plan"),
        h("button", { class: "btn ghost small", type: "button", onClick: () => run(async () => {
          const res = await fetch("/api/lessons/" + id + "/html", { headers: token ? { Authorization: "Bearer " + token } : {} });
          if (!res.ok) throw new Error("Export failed");
          const url = URL.createObjectURL(new Blob([await res.text()], { type: "text/html" }));
          const a = h("a", { href: url, download: lesson.title.replace(/[^\w]+/g, "-").toLowerCase() + ".html" }); document.body.append(a); a.click(); a.remove();
        }) }, "Export HTML")),
      lesson.sections.map((s) => h("article", { class: "section" + (s.marked ? " marked" : "") },
        h("div", { class: "row" }, h("h2", null, s.heading),
          h("button", { class: "btn small star " + (s.marked ? "" : "ghost"), type: "button", "aria-pressed": String(!!s.marked), onClick: () => run(() => api("/sections/" + s.id, { method: "PATCH", body: { marked: !s.marked } }).then(reload)) }, s.marked ? "★ Marked" : "☆ Make flashcards")),
        s.key_points.length ? h("ul", null, s.key_points.map((p) => h("li", null, p))) : null,
        s.explanation ? h("p", null, s.explanation) : null)),
      h("div", { class: "row", style: "margin:16px 0" }, genBtn, h("button", { class: "btn ghost", type: "button", onClick: () => gen(true) }, "Auto-select all sections")),
      related.length ? h("section", { class: "panel" }, h("h2", null, "From your other notes"), h("p", { class: "muted" }, "Closest passages already in your vector store. Compare and contrast."),
        related.map((r) => h("div", { class: "hit" }, h("div", { class: "row" }, h("span", { class: "title" }, r.title), h("span", { class: "score" }, r.score.toFixed(2))), h("p", null, r.text)))) : null,
      h("div", null, confirmBtn("Delete lesson", () => run(() => api("/lessons/" + id, { method: "DELETE" }).then(() => (location.hash = "#/plan/" + lesson.plan_id))), "btn danger small")));
  }

  /* ---------- Search ---------- */
  async function viewSearch(_, params) {
    const plans = await api("/plans");
    const out = h("div", null);
    const q = h("input", { type: "search", class: "grow", name: "q", placeholder: "Ask your study material, e.g. how does Delta handle schema changes?", "aria-label": "Search query", value: params.q || "" });
    const plan = h("select", { "aria-label": "Plan", style: "width:auto;flex:1 1 140px" }, h("option", { value: "" }, "All plans"), plans.map((p) => h("option", { value: p.id }, p.topic)));
    const kind = h("select", { "aria-label": "Kind", style: "width:auto;flex:1 1 140px" }, h("option", { value: "" }, "Material + cards"), h("option", { value: "material" }, "Material only"), h("option", { value: "card" }, "Cards only"));
    async function go() {
      const text = q.value.trim(); if (!text) return;
      clear(out).append(h("p", { class: "muted" }, "Searching..."));
      const sp = new URLSearchParams({ q: text, k: "10" }); if (plan.value) sp.set("plan_id", plan.value); if (kind.value) sp.set("kind", kind.value);
      const hits = await run(() => api("/search?" + sp));
      if (!hits) return clear(out);
      clear(out).append(hits.length ? h("section", { class: "panel" }, hits.map((r) => h("div", { class: "hit" },
        h("div", { class: "row" }, h("span", { class: "title" }, r.title || r.key), h("span", { class: "chip " + (r.kind === "card" ? "warn" : "ok") }, r.kind), r.topic ? h("span", { class: "chip" }, r.topic) : null, h("span", { class: "score" }, "match " + r.score.toFixed(2))),
        h("p", null, r.text)))) : h("p", { class: "empty" }, "No matches. Add material to a plan first."));
    }
    mount(head("Search", "Semantic search over everything in your vector store." + (health.embeddings === "local" ? " (Local embeddings match keywords. Set EMBED_BACKEND=databricks or bedrock for meaning-based search.)" : "")),
      h("form", { class: "row", style: "margin-bottom:16px", onSubmit: (e) => { e.preventDefault(); go(); } }, q, plan, kind, h("button", { class: "btn", type: "submit" }, "Search")), out);
    if (params.q) go();
  }

  /* ---------- Review (flashcards) ---------- */
  async function viewReview(_, params) {
    const plans = await api("/plans");
    const state = { plan: params.plan || "", topic: "", mode: "due", queue: [], i: 0, flipped: false, source: null };
    const box = h("div", null);
    const bar = h("div", { class: "row", style: "margin-bottom:8px" });
    mount(head("Review", "Flip, recall, grade yourself honestly. Space flips, 1-4 grades."), bar, box);

    async function load() {
      const sp = new URLSearchParams({ mode: state.mode }); if (state.plan) sp.set("plan_id", state.plan); if (state.topic) sp.set("topic", state.topic);
      state.queue = (await run(() => api("/review/queue?" + sp))) || []; state.i = 0; state.flipped = false; state.source = null; draw();
    }
    async function drawBar() {
      const cards = state.plan ? await run(() => api("/cards?plan_id=" + state.plan)) : [];
      const topics = [...new Set((cards || []).map((c) => c.topic))].sort();
      const planSel = h("select", { "aria-label": "Plan", style: "width:auto;flex:1 1 160px", onChange: (e) => { state.plan = e.target.value; state.topic = ""; drawBar(); load(); } }, h("option", { value: "" }, "All plans"), plans.map((p) => h("option", { value: p.id, selected: p.id === state.plan }, p.topic)));
      const topicSel = h("select", { "aria-label": "Topic", style: "width:auto;flex:1 1 160px", onChange: (e) => { state.topic = e.target.value; load(); } }, h("option", { value: "" }, topics.length ? "All topics" : "All topics (pick a plan)"), topics.map((t) => h("option", { value: t, selected: t === state.topic }, t)));
      const modeBtn = h("button", { class: "btn ghost small", type: "button", onClick: () => { state.mode = state.mode === "due" ? "all" : "due"; modeBtn.textContent = state.mode === "due" ? "Due only" : "Everything (shuffled)"; load(); } }, state.mode === "due" ? "Due only" : "Everything (shuffled)");
      clear(bar).append(planSel, topicSel, modeBtn);
    }

    function draw() {
      clear(box);
      if (!state.queue.length) {
        box.append(h("div", { class: "done" }, h("h2", null, "All caught up"), h("p", { class: "muted" }, state.mode === "due" ? "No cards are due right now." : "There are no cards yet."),
          state.mode === "due" ? h("button", { class: "btn ghost", type: "button", onClick: () => { state.mode = "all"; drawBar(); load(); } }, "Review everything anyway") : h("a", { class: "btn", href: "#/plans", style: "text-decoration:none" }, "Go to plans")));
        return;
      }
      const c = state.queue[state.i];
      const card = h("div", { class: "flip" + (state.flipped ? " flipped" : ""), tabindex: "0", role: "button", "aria-label": "Flip card", onClick: flip, onKeydown: (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); flip(); } } },
        h("div", { class: "face" }, h("div", { class: "tag" }, c.topic), h("div", { class: "ftext" }, c.question), h("div", { class: "fhint" }, "Tap or press Space to reveal")),
        h("div", { class: "face back" }, h("div", { class: "tag" }, "Answer"), h("div", { class: "ftext" }, c.answer)));
      function flip() { state.flipped = !state.flipped; card.classList.toggle("flipped", state.flipped); grades.hidden = !state.flipped; srcBtn.hidden = !state.flipped; }
      const grades = h("div", { class: "grades", hidden: !state.flipped }, [["Again", "10 min"], ["Hard", ""], ["Good", ""], ["Easy", ""]].map(([label], g) =>
        h("button", { class: "btn ghost", type: "button", onClick: () => grade(g) }, label, h("small", null, String(g + 1)))));
      const srcBox = h("div", null);
      const srcBtn = h("button", { class: "link", type: "button", hidden: !state.flipped, onClick: async () => {
        const s = await run(() => api("/cards/" + c.id + "/source")); if (!s) return;
        clear(srcBox).append(h("div", { class: "src" }, h("b", null, "Source (vector store): "), s.title ? s.title + "\n\n" : "", s.text || "Source not found in the vector store."));
      } }, "Show source");
      box.append(h("div", { class: "row" }, h("span", { class: "muted" }, (state.i + 1) + " / " + state.queue.length)),
        h("div", { class: "progress" }, h("div", { style: "width:" + (state.i / state.queue.length) * 100 + "%" })),
        h("div", { class: "stage" }, card), grades, srcBtn, srcBox);
      card.focus({ preventScroll: true });
      state.flip = flip;
    }
    async function grade(g) {
      const c = state.queue[state.i];
      const res = await run(() => api("/review", { method: "POST", body: { card_id: c.id, grade: g } })); if (!res) return;
      if (g === 0) state.queue.push(c); // see failed cards again this session
      state.i++; state.flipped = false;
      if (state.i >= state.queue.length) { state.queue = []; refreshBadge(); }
      draw();
    }
    attachKeys((e) => {
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) || e.ctrlKey || e.metaKey) return;
      if (!state.queue.length) return;
      if (e.key === " " && e.target === document.body) { e.preventDefault(); state.flip && state.flip(); }
      else if (state.flipped && "1234".includes(e.key) && e.key) grade(+e.key - 1);
    });
    await drawBar(); await load();
  }

  /* ---------- Log (from the Learning log page) ---------- */
  async function viewLog() {
    let items = await api("/log");
    let range = "7", editing = null;
    const root = h("div", null);
    mount(root);
    const DEFAULT_CATS = ["AI & machine learning", "Data engineering", "Cloud & infrastructure", "AI governance & security", "Business & leadership", "Languages", "Other"];

    const inRange = (e) => {
      if (range === "all") return true;
      const p = e.date.split("-"), d = new Date(+p[0], +p[1] - 1, +p[2]), s = new Date();
      s.setHours(0, 0, 0, 0); s.setDate(s.getDate() - (+range - 1)); return d >= s;
    };
    const label = () => (range === "all" ? "all time" : "last " + range + " days");
    const num = (v) => { const n = parseInt(v, 10); return isNaN(n) || n < 0 ? 0 : n; };

    function draw() {
      clear(root);
      const scoped = items.filter(inRange);
      let made = 0, studied = 0, mins = 0; const days = new Set(), by = {};
      scoped.forEach((e) => {
        made += e.made; studied += e.studied; mins += e.minutes; days.add(e.date);
        const b = (by[e.category] = by[e.category] || { sessions: 0, minutes: 0, made: 0, studied: 0 });
        b.sessions++; b.minutes += e.minutes; b.made += e.made; b.studied += e.studied;
      });
      const cats = Object.keys(by), useMin = cats.some((k) => by[k].minutes > 0), w = (k) => (useMin ? by[k].minutes : by[k].sessions);
      cats.sort((a, b) => w(b) - w(a));
      const max = cats.length ? w(cats[0]) || 1 : 1;

      const seg = h("div", { class: "seg", role: "group", "aria-label": "Summary period" }, [["7", "7 days"], ["30", "30 days"], ["all", "All time"]].map(([v, t]) =>
        h("button", { type: "button", "aria-pressed": String(range === v), onClick: () => { range = v; draw(); } }, t)));
      const cur = editing ? items.find((x) => x.id === editing) : null;
      const form = h("form", { class: "grid", autocomplete: "off", onSubmit: (e) => {
        e.preventDefault(); const f = e.target;
        const body = { date: f.date.value || today(), minutes: num(f.minutes.value), category: f.category.value.trim(), topic: f.topic.value.trim(), resource: f.resource.value.trim(), made: num(f.made.value), studied: num(f.studied.value) };
        run(async () => { editing ? await api("/log/" + editing, { method: "PUT", body }) : await api("/log", { method: "POST", body }); editing = null; items = await api("/log"); draw(); toast("Saved"); });
      } },
        field("Date", h("input", { type: "date", name: "date", required: true, value: cur ? cur.date : today() }), "third pair"),
        field("Minutes", h("input", { type: "number", name: "minutes", min: 0, step: 5, inputMode: "numeric", placeholder: "45", value: cur && cur.minutes ? cur.minutes : "" }), "third pair", "(optional)"),
        field("Category", h("input", { name: "category", list: "cats", required: true, placeholder: "Pick or type your own", value: cur ? cur.category : "" }), "third"),
        h("datalist", { id: "cats" }, [...new Set([...DEFAULT_CATS, ...items.map((i) => i.category)])].map((c) => h("option", { value: c }))),
        field("Topic", h("input", { name: "topic", required: true, placeholder: "e.g. Vector search indexes", value: cur ? cur.topic : "" }), "half"),
        field("Resource", h("input", { name: "resource", required: true, placeholder: "Course, book, article, video", value: cur ? cur.resource : "" }), "half"),
        field("New flashcards made", h("input", { type: "number", name: "made", min: 0, value: cur ? cur.made : 0, required: true }), "half"),
        field("Flashcards studied", h("input", { type: "number", name: "studied", min: 0, value: cur ? cur.studied : 0, required: true }), "half"),
        h("div", { class: "actions" }, h("button", { class: "btn", type: "submit" }, cur ? "Save changes" : "Save session"), cur ? h("button", { class: "btn ghost", type: "button", onClick: () => { editing = null; draw(); } }, "Cancel edit") : null));

      const q = h("input", { type: "search", class: "grow", placeholder: "Search topic or resource", "aria-label": "Search topic or resource", value: filterState.q, onInput: (e) => { filterState.q = e.target.value; drawList(); } });
      const catSel = h("select", { "aria-label": "Filter by category", style: "width:auto;flex:1 1 160px", onChange: (e) => { filterState.cat = e.target.value; drawList(); } }, h("option", { value: "" }, "All categories"), [...new Set(items.map((i) => i.category))].sort().map((c) => h("option", { value: c, selected: c === filterState.cat }, c)));
      const list = h("div", null);
      function drawList() {
        const ql = filterState.q.trim().toLowerCase();
        const rows = items.filter((e) => (!filterState.cat || e.category === filterState.cat) && (!ql || (e.topic + " " + e.resource).toLowerCase().includes(ql)));
        clear(list).append(...(rows.length ? rows.map((e) => h("article", { class: "item" },
          h("div", { class: "item-top" }, h("span", { class: "title" }, e.topic), h("span", { class: "meta" }, fmtDate(e.date))),
          h("div", { class: "meta" }, e.category + " · " + e.resource),
          h("div", { class: "chips" }, e.minutes ? h("span", { class: "chip" }, fmtMin(e.minutes)) : null, h("span", { class: "chip warn" }, e.made + " made"), h("span", { class: "chip ok" }, e.studied + " studied"),
            h("span", { style: "margin-left:auto" }, h("button", { class: "link", type: "button", onClick: () => { editing = e.id; draw(); window.scrollTo(0, 0); } }, "Edit"), confirmBtn("Delete", () => run(async () => { await api("/log/" + e.id, { method: "DELETE" }); items = await api("/log"); draw(); }))))))
          : [h("p", { class: "empty" }, items.length ? "No sessions match that filter." : "No sessions yet. Log your first one above.")]));
      }
      const exportBtn = items.length ? h("button", { class: "btn ghost small", type: "button", onClick: () => {
        const cell = (v) => { const s = String(v == null ? "" : v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
        const rows = [["Date", "Category", "Topic", "Resource", "Minutes", "Flashcards made", "Flashcards studied"].join(",")].concat(items.slice().sort((a, b) => (a.date < b.date ? -1 : 1)).map((e) => [e.date, e.category, e.topic, e.resource, e.minutes, e.made, e.studied].map(cell).join(",")));
        const a = h("a", { href: URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" })), download: "learning-log.csv" }); document.body.append(a); a.click(); a.remove();
      } }, "Export CSV") : null;

      root.append(
        head("Learning log", "What you studied, with what, and how many flashcards.", seg),
        h("div", { class: "cards", "aria-live": "polite" },
          h("div", { class: "index-card made" }, h("div", { class: "label" }, "Flashcards made"), h("div", { class: "big" }, made.toLocaleString()), h("div", { class: "note" }, label())),
          h("div", { class: "index-card studied" }, h("div", { class: "label" }, "Flashcards studied"), h("div", { class: "big" }, studied.toLocaleString()), h("div", { class: "note" }, label()))),
        h("div", { class: "mini" }, h("div", null, h("b", null, fmtMin(mins)), h("span", null, "Time spent")), h("div", null, h("b", null, String(scoped.length)), h("span", null, "Sessions")), h("div", null, h("b", null, String(days.size)), h("span", null, "Days studied"))),
        h("section", { class: "panel" }, h("h2", null, cur ? "Edit session" : "Log a session"), form),
        h("section", { class: "panel" }, h("h2", null, "By category"),
          cats.length ? h("div", { class: "bars" }, cats.map((k) => h("div", { class: "bar-row" }, h("div", null, k),
            h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: "width:" + Math.max(2, Math.round((w(k) / max) * 100)) + "%" })),
            h("div", { class: "bar-val" }, (useMin ? fmtMin(by[k].minutes) : plural(by[k].sessions, "session")) + ", " + by[k].made + " made, " + by[k].studied + " studied")))) : h("p", { class: "empty" }, "Nothing logged in this period yet.")),
        h("section", { class: "panel" }, h("h2", null, "Sessions"), h("div", { class: "row", style: "margin-bottom:12px" }, q, catSel, exportBtn), list));
      drawList();
    }
    const filterState = { q: "", cat: "" };
    draw();
  }

  /* ---------- boot ---------- */
  window.addEventListener("hashchange", route);
  (async function boot() {
    try { health = await (await fetch("/api/health")).json(); } catch (e) { health = {}; }
    route();
  })();
})();
