FROM python:3.12-slim

WORKDIR /app

# CPU-only torch first -- sentence-transformers otherwise pulls the full CUDA
# build from PyPI (1GB+ of unused GPU libraries for a server with no GPU).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# data/ is a sibling of backend/ in the repo (see BASE_DIR = parents[2] in
# rag/ingest.py), so the container mirrors that layout under /app.
COPY data/ ./data/
COPY backend/ ./backend/

# Bake the vector store into the image so the container starts ready to serve
# -- reproducible (rebuild = re-embed from the CSVs) and the store is tiny
# (a few MB for ~65 landmarks), unlike the trip database below which is real
# runtime state and must NOT be baked in.
RUN python -m backend.rag.ingest

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
