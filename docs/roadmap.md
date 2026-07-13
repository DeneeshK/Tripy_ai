# Tripy — improvement roadmap

Working reference for the "make this project stronger" suggestions from planning
sessions. Check items off as they're actually implemented (not just discussed).
Two lists: recruiter/portfolio-facing polish, and ML/AI-specific skill showcases.

## Recruiter / portfolio polish

- [x] **Agent Activity trace panel** — live UI showing each agent's real decisions
      and reasons (`frontend/src/components/AgentTracePanel.jsx`,
      `backend/agents/graph.py`'s `_log_trace`). Also fixed the panel's trigger
      button overlapping the map's layer switcher.
- [x] **Portfolio README rewrite** — real screenshots, feature pitch, eval
      results table, honest limitations section (`README.md`, `docs/screenshots/`).
- [x] **Fix trip persistence** — `TripStore` (`backend/agents/state.py`) is now
      SQLite-backed (`backend/tripy_trips.db`, gitignored). Verified live: created
      a trip, hard-killed the backend process, started a fresh one, called
      `/api/trip/{id}/check` against it — 200 with the full trace intact
      (Planning Agent entry from the old process + Weather/Schedule entries from
      the new one), not the 404 it used to be.
- [x] **Reflection / self-critique agent** — `critique_plan_node` in
      `backend/agents/graph.py` (the "Plan Critic Agent"), 4th orchestrator
      node, runs after every plan/replan. Deliberately deterministic, not
      another LLM call (re-derives facts from the finished plan: unfulfilled
      must-includes/end-place, unmet meals, travel-heavy pacing, idle time)
      rather than an LLM judging its own output. Findings are handed to the
      chat LLM via `plan_critique` in the tool response with a system-prompt
      instruction to address every one — verified live: a plan with a closed
      must-include, an unresolvable end place, an unmet meal, and idle time
      produced all 4 findings, and the chat narrative addressed all 4 without
      being told to notice them from raw data.
- [ ] **Itinerary feasibility eval suite** — the RAG retrieval eval is done (see
      below); a companion eval for the OR-Tools solver itself (does it respect
      opening hours / travel-time math on a battery of synthetic scenarios) is not.
- [ ] **Budget-aware planning** — add cost as an OR-Tools constraint. Reuses the
      existing solver, easy to narrate, not started.
- [ ] **Multi-traveler negotiation** — reconcile two people's differing
      preferences into one itinerary. Bigger build, not started.
- [ ] **Voice input** for the chat panel. Not started.
- [ ] **Live deployed demo** — no hosted URL yet. Still the single highest-leverage
      remaining item; a deployed link + short demo video outweighs any one feature.

## ML / AI skill-showcase specific

- [x] **Retrieval eval harness** — `backend/rag/eval/`: 38 rule-labeled queries,
      Recall/Precision/nDCG/MRR vs. random + TF-IDF baselines, compares the
      production embedding model against a challenger. Found bge-small-en-v1.5
      beats the current all-MiniLM-L6-v2 on every metric. See `results.md`.
- [ ] **Adopt the winning embedding model in production** — the eval above found
      a better model but it was deliberately NOT wired into `rag/search.py` /
      `rag/ingest.py` yet (changes live search results app-wide — flagged as a
      follow-on decision, not done silently). Still open.
- [ ] **Learned ranking model** — replace `smart_search`'s hand-tuned
      relevance/travel-time formula with a trained Learning-to-Rank model
      (logistic regression / LightGBM ranker). Not started.
- [ ] **Personalization / vibe vector** — per-user embedding learned from saved
      trip history, blended into search at query time. Not started.
- [ ] **LLMOps regression harness** — fixed chat transcripts asserting
      `plan_my_day` extracts the right fields, optionally an LLM-as-judge score
      for itinerary quality over time. Not started.
- [ ] **Unsupervised ML on the landmark corpus** — cluster place embeddings
      (KMeans/HDBSCAN) to auto-discover vibe groups, visualize with UMAP/t-SNE.
      Not started.
- [ ] **Multimodal search** — CLIP-based "find places like this photo" search.
      Highest effort/ceiling of the list, not started.
