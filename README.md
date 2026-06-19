# Tripy v2

Trip planner for Trivandrum. FastAPI backend + React/Leaflet frontend.

## Project layout

```
tripy_v2/
  data/                       your landmarks + reviews (unchanged from v1)
  backend/
    rag/
      ingest.py               builds Chroma vector store (sentence-transformers)
      search.py               semantic search + itinerary planning
    engine/
      hours.py                opening-hours parser
      distance_matrix.py      OSRM travel times with haversine fallback
      itinerary_engine.py     OR-Tools solver
    api/
      main.py                 FastAPI app (/api/plan, /api/chat, /api/route)
    .env.example
    requirements.txt
  frontend/
    src/
      App.jsx                 root layout + GPS
      components/
        ChatPanel.jsx         Groq streaming chat
        TripMap.jsx           Leaflet map + OSRM route
    package.json
    vite.config.js
```

## Setup

### 1. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and paste your GROQ_API_KEY

# Build the vector store (downloads ~80MB model once)
python -m rag.ingest

# Start the API
uvicorn api.main:app --reload --port 8000
```

### 2. Frontend (separate terminal)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## What changed vs v1

| Thing              | Before (Gemini + Streamlit)          | Now                                       |
|--------------------|--------------------------------------|-------------------------------------------|
| Embeddings         | Gemini API (paid, rate-limited)      | sentence-transformers local (free)        |
| Chat / tool-call   | Gemini                               | Groq llama-3.3-70b-versatile              |
| Travel time        | Haversine + flat 25 km/h             | OSRM real road times (fallback if offline)|
| Itinerary ordering | Greedy nearest-neighbour             | OR-Tools (sees all candidates at once)    |
| Relevance used?    | No (Chroma distances were discarded) | Yes (relevance vs travel time tradeoff)   |
| Frontend           | Streamlit                            | React + Leaflet                           |
| API layer          | None                                 | FastAPI                                   |

## Metadata in Chroma (unchanged from original)

`name`, `lat`, `lng`, `category`, `closed_on`, `regular_hours`,
`special_hours`, `vibe_tags`, `avg_duration`
