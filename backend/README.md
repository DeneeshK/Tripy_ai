# Tripy v2 -- backend

Phase 1 (itinerary engine) + Phase 2 (RAG search + Groq tool-calling)
are both in here now. Still no frontend -- this is the brain, not the
face, and Streamlit isn't part of this rebuild at all.

## Project layout

```
tripy_v2/
  data/                    landmarks.csv, vibe_tags.csv, landmark_reviews/*.txt
  backend/
    engine/                Phase 1: hours parsing, real travel-time matrix, OR-Tools solver
    rag/                   Phase 2: catalog loader, embeddings, ingestion, semantic search
    llm/                   Phase 2: the plan_my_day tool + the Groq tool-calling client
    test_real_data.py      Phase 1 end-to-end test (hand-built candidates)
    test_full_pipeline.py  Phase 2 end-to-end test (search -> hours -> solver)
```

## Phase 1: the itinerary engine

Just the part that decides *which places to visit and in what order*.

## What's in here

- `engine/hours.py` -- turns the CSV's opening-hours strings into
  plain (open_minute, close_minute) windows, handling weekly closures
  and single-day overrides (e.g. a museum with different Wednesday hours).
- `engine/distance_matrix.py` -- real road travel times via OSRM's
  Table API (one batched call for the whole matrix), with an explicit
  Haversine + flat-speed fallback if OSRM can't be reached.
- `engine/itinerary_engine.py` -- the actual solver. Given a home
  point, a time window, and a list of candidate places (each with a
  relevance score from the vector search, a visit duration, an
  opening window, and an optional "this is an anchor / must-visit"
  flag), it returns an ordered, timed route plus a reason for every
  place it left out.
- `test_real_data.py` -- runs the whole thing against the actual
  39-place Trivandrum dataset and asserts it behaves sanely.

## What's actually been tested vs. not

Tested for real, in this environment:
- The solver itself (OR-Tools), including time windows, the
  relevance-vs-travel-time tradeoff, anchor stops, and edge cases
  (no candidates, a place that's closed all day, a zero-length trip).
- The Haversine fallback path in `distance_matrix.py` -- it actually
  got exercised because this sandbox can't reach the public OSRM
  server, so the try/except genuinely caught a real network failure.

Written correctly per OSRM's documented API, but not live-tested here
(this sandbox's network allowlist doesn't include router.project-osrm.org):
- `distance_matrix.osrm_time_matrix()`. Run `test_real_data.py` from a
  normal machine with internet access and check
  `result.used_distance_fallback` -- it should print `False` there.

## A subtlety that cost a segfault during development

`RoutingIndexManager`'s `NodeToIndex()` returns `-1` for any node
you've declared as a vehicle's start or end -- it's not a general
node-lookup function once that happens. Setting a time-window range on
index `-1` doesn't raise in Python, it segfaults the C++ solver
silently. The fix is to always use `routing.Start(vehicle)` /
`routing.End(vehicle)` to get those two indices, never
`manager.NodeToIndex()`. Left a comment at the call site so it doesn't
get "fixed" back the wrong way later.

## Known v1 simplifications (carried over from the plan, not bugs)

- A place gets one opening window per day, picked by best overlap with
  the trip span -- a temple open 6-11am and 5-8pm won't be modeled as
  two separate opportunities yet.

## Phase 2: search (RAG) + the Groq tool-calling layer

- `rag/catalog.py` -- loads landmarks.csv + vibe_tags.csv into one
  in-memory dict, keyed by id. Single source of truth for structured
  facts (hours, coordinates, category, duration).
- `rag/embeddings.py` -- the embedding function used for both
  ingestion and queries. Real default is a local sentence-transformers
  model (free, offline, no API key). A deterministic hash-based stub
  is also included, used only for testing in network-restricted
  environments -- see "what's tested" below.
- `rag/ingest.py` -- builds the Chroma vector store from the catalog +
  review text files. Replaces `ingest_local.py`. No hardcoded keys.
- `rag/search.py` -- semantic search, joined back to the catalog, with
  today's opening window resolved, returned as ready-to-use
  `engine.Place` objects.
- `llm/tools.py` -- `plan_my_day`, the one tool exposed to the LLM.
  Wraps search + hours + the solver into a single call.
- `llm/groq_client.py` -- the tool-calling loop against Groq, replacing
  the old Gemini call.

### Two real bugs found and fixed while building this, not just style nits

1. **The old smart_search never asked Chroma for similarity scores.**
   It called `collection.query(..., include=["metadatas", "documents"])`
   -- no `"distances"`. Chroma still ranked results internally, but the
   actual numeric similarity was discarded the moment it left the
   function, so nothing downstream could ever use "how good a match is
   this" as a signal. `rag/search.py` requests `"distances"` and turns
   it into the `relevance` score the solver actually competes against
   travel time.

2. **`special_hours` was parsed from the CSV but never made it into
   Chroma's metadata.** `ingest_local.py`'s metadata dict has `name`,
   `lat`, `lng`, `category`, `closed_on`, `regular_hours`, `vibe_tags`,
   `avg_duration` -- no `special_hours` key at all. Since the old
   search layer only ever read fields back off that metadata, a
   museum's Wednesday-only hours could never actually take effect at
   runtime, even though the CSV had the override sitting right there.
   This rebuild avoids the class of bug entirely: `catalog.py` reads
   structured facts straight from the CSV, so there's no second copy
   to forget to sync. Confirmed in `test_full_pipeline.py`'s output --
   Sri Chitra Art Gallery's resolved window on a Wednesday is
   `13:00-16:45` (its special hours), not its regular `10:00-16:45`.

### What's actually been tested vs. not (Phase 2)

Tested for real:
- The full pipeline -- ingest, semantic query, catalog join, hours
  resolution, solver -- end to end in `test_full_pipeline.py`, using
  the stub embedding (see below) but the *real* 40-place dataset and
  *real* CSV-parsing logic.
- `plan_my_day` (the actual function the LLM would call) called
  directly with real arguments, confirmed to return clean output.
- The `groq` SDK installs and imports cleanly.

NOT tested -- needs your own machine/credentials:
- Real semantic search quality. This sandbox has no route to Hugging
  Face, so `rag/embeddings.py`'s production
  `LocalSentenceTransformerEF` never actually ran here --
  `test_full_pipeline.py` uses `StubHashEF` instead, a dependency-free
  bag-of-hashed-tokens stand-in with zero real language understanding.
  It proves the *wiring* (Chroma add/query/metadata round-trip), not
  search *quality*. Run `python3 -m rag.ingest` (no `--stub`) and
  `test_full_pipeline.py` with `use_stub=False` on a machine with real
  internet access before trusting the relevance scores.
- The actual Groq API call in `llm/groq_client.py`. No network route to
  `api.groq.com` here and no API key in this environment. Written
  directly against Groq's documented tool-use contract and the
  request/response shapes their docs show, but never executed. Set
  `GROQ_API_KEY` and run `python3 -m llm.groq_client` on a real machine
  to actually exercise it.

## Setup

```
pip install -r requirements.txt
cp .env.example .env        # then fill in GROQ_API_KEY
python3 -m rag.ingest        # builds trivandrum_vdb/ (use --stub to test without internet)
python3 test_real_data.py
python3 test_full_pipeline.py
python3 -m llm.groq_client   # needs GROQ_API_KEY and real internet access
```

