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
- [ ] **Fix trip persistence** — `TripStore` is in-memory (`backend/agents/state.py`);
      a saved trip 404s on edit after a server restart. Needs SQLite/Redis backing.
      Biggest "breaks live in front of an interviewer" risk still open.
- [ ] **Reflection / self-critique agent** — a cheap LLM pass that sanity-checks a
      finished plan against the user's stated constraints before returning it.
      Well-known agentic pattern, good interview talking point, not yet built.
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
