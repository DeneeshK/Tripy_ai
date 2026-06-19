"""
ingest.py  --  builds the local Chroma vector store.

Logic is identical to the original ingest_local.py:
  - same metadata fields stored in Chroma
  - same document text: "Place: {name}. Vibe: {tags}. Insight: {review}"
  - same merge of landmarks.csv + vibe_tags.csv on id

Only change: sentence-transformers replaces Gemini embeddings.
No API key needed, runs fully offline after first download.

Usage:
    python -m rag.ingest          # from backend/
"""

import os
import sys
import chromadb
import pandas as pd
from pathlib import Path
from sentence_transformers import SentenceTransformer

BASE_DIR   = Path(__file__).resolve().parents[2]   # tripy_v2/
DATA_DIR   = BASE_DIR / "data"
VDB_PATH   = Path(__file__).resolve().parent.parent / "trivandrum_vdb"
COLLECTION = "landmark_repository"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def run_ingest():
    print("Loading CSVs...")
    df_main  = pd.read_csv(DATA_DIR / "landmarks.csv")
    df_vibes = pd.read_csv(DATA_DIR / "vibe_tags.csv")

    # Merge exactly like the original -- drop the thin vibe_tags from landmarks,
    # use the richer ones from vibe_tags.csv
    df = pd.merge(df_main.drop(columns=["vibe_tags", "name"]), df_vibes, on="id")
    print(f"Loaded {len(df)} landmarks.")

    print("Loading embedding model (downloads once from HuggingFace)...")
    model = SentenceTransformer(MODEL_NAME)

    chroma_client = chromadb.PersistentClient(path=str(VDB_PATH))
    try:
        chroma_client.delete_collection(COLLECTION)
    except Exception:
        pass
    collection = chroma_client.get_or_create_collection(name=COLLECTION)

    print(f"Starting ingestion for {len(df)} landmarks...")
    for _, row in df.iterrows():
        l_id = str(row["id"])
        name = row["name"]

        txt_path = DATA_DIR / "landmark_reviews" / f"{l_id}.txt"
        if txt_path.exists():
            review_content = txt_path.read_text(encoding="utf-8")
        else:
            review_content = f"A {row['category']} located in Thiruvananthapuram."

        # Same document text as original
        text_to_vectorize = f"Place: {name}. Vibe: {row['vibe_tags']}. Insight: {review_content}"

        vector = model.encode(text_to_vectorize).tolist()

        # Same metadata dict as original + special_hours (it's in the CSV, engine needs it)
        metadata = {
            "name":          str(name),
            "lat":           float(row["lat"]),
            "lng":           float(row["lng"]),
            "category":      str(row["category"]),
            "closed_on":     str(row["closed_on"])     if pd.notna(row["closed_on"])     else "None",
            "regular_hours": str(row["regular_hours"]) if pd.notna(row["regular_hours"]) else "Unknown",
            "special_hours": str(row["special_hours"]) if pd.notna(row.get("special_hours")) else "None",
            "vibe_tags":     str(row["vibe_tags"]),
            "avg_duration":  float(row["avg_duration"]) if pd.notna(row["avg_duration"]) else 1.0,
        }

        collection.add(
            ids=[l_id],
            embeddings=[vector],
            documents=[text_to_vectorize],
            metadatas=[metadata],
        )
        print(f"  Ingested {l_id}: {name}")

    print(f"\nDone. {collection.count()} places in '{COLLECTION}' at {VDB_PATH}")


if __name__ == "__main__":
    run_ingest()
