"""
ingest.py  --  builds the local Chroma vector store.

Logic is identical to the original ingest_local.py:
  - same metadata fields stored in Chroma (name, lat, lng, category,
    closed_on, regular_hours, special_hours, vibe_tags, avg_duration)
  - same document text: "Place: {name}. Vibe: {tags}. Insight: {review}"
  - same merge of landmarks.csv + vibe_tags.csv on id

Also merges data/parking.csv (built by rag/enrich_parking.py from real
OpenStreetMap data, NOT invented) on id. A row whose Overpass query never
succeeded (parking_source == "query_failed") is stored with has_parking =
False rather than a null/unknown state -- Chroma metadata has to be one
concrete value, and "don't claim parking exists without confirmation" is
the safer default for a requires_parking filter. parking_source is kept in
the metadata precisely so that fail-safe collapsing is never silently
lossy -- a caller that cares can still tell "confirmed absent" apart from
"never confirmed" by checking it.

Changes vs original:
  - sentence-transformers replaces Gemini embeddings (no API key, runs offline)
  - --stub flag uses a deterministic hash embedding for network-restricted
    environments (CI, this sandbox, etc.) -- NOT for production use

Usage:
    python -m rag.ingest          # real sentence-transformers embeddings
    python -m rag.ingest --stub   # offline test (wiring only, not quality)
"""

import argparse
import sys
from pathlib import Path

import chromadb
import pandas as pd

BASE_DIR   = Path(__file__).resolve().parents[2]   # tripy_v2/
DATA_DIR   = BASE_DIR / "data"
VDB_PATH   = Path(__file__).resolve().parent.parent / "trivandrum_vdb"
COLLECTION = "landmark_repository"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def _real_embedder():
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)
    return lambda text: model.encode(text).tolist()


def _stub_embedder():
    """Deterministic hash embedding. No language understanding -- wiring test only."""
    import hashlib, re
    def embed(text):
        vec = [0.0] * 8
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vec[h % 8] += 1.0
        n = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / n for v in vec]
    return embed


def _parse_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() == "true"


def run_ingest(use_stub: bool = False):
    print("Loading CSVs...")
    df_main    = pd.read_csv(DATA_DIR / "landmarks.csv")
    df_vibes   = pd.read_csv(DATA_DIR / "vibe_tags.csv")
    df = pd.merge(df_main.drop(columns=["vibe_tags", "name"]), df_vibes, on="id")

    parking_path = DATA_DIR / "parking.csv"
    if parking_path.exists():
        df_parking = pd.read_csv(parking_path)
        df = pd.merge(df, df_parking, on="id", how="left")
    else:
        print(f"NOTE: {parking_path} not found (run `python -m rag.enrich_parking` first) "
              "-- ingesting with has_parking = False for every place.")
        df["has_parking"] = False
        df["parking_lat"] = df["parking_lng"] = df["parking_distance_m"] = pd.NA
        df["parking_name"] = df["parking_source"] = pd.NA
    print(f"Loaded {len(df)} landmarks.")

    if use_stub:
        print("Using STUB embeddings (offline test -- not for production).")
        embed = _stub_embedder()
    else:
        print(f"Loading embedding model ({MODEL_NAME}) -- downloads ~80MB once from HuggingFace...")
        embed = _real_embedder()

    client = chromadb.PersistentClient(path=str(VDB_PATH))
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    collection = client.get_or_create_collection(name=COLLECTION)

    print(f"Ingesting {len(df)} landmarks...")
    for _, row in df.iterrows():
        l_id = str(row["id"])
        name = row["name"]

        txt_path = DATA_DIR / "landmark_reviews" / f"{l_id}.txt"
        review_content = txt_path.read_text(encoding="utf-8") if txt_path.exists() \
            else f"A {row['category']} located in Thiruvananthapuram."

        doc_text = f"Place: {name}. Vibe: {row['vibe_tags']}. Insight: {review_content}"
        vector   = embed(doc_text)

        metadata = {
            "name":          str(name),
            "lat":           float(row["lat"]),
            "lng":           float(row["lng"]),
            "category":      str(row["category"]),
            "closed_on":     str(row["closed_on"])     if pd.notna(row["closed_on"])           else "None",
            "regular_hours": str(row["regular_hours"]) if pd.notna(row["regular_hours"])       else "Unknown",
            "special_hours": str(row["special_hours"]) if pd.notna(row.get("special_hours"))   else "None",
            "vibe_tags":     str(row["vibe_tags"]),
            "avg_duration":  float(row["avg_duration"]) if pd.notna(row["avg_duration"])       else 1.0,
            # Food-only fields (na / 0.0 for non-food rows) -- power diet filtering
            # and the per-meal restaurant suggestion cards.
            "diet":          str(row["diet"])           if pd.notna(row.get("diet"))           else "na",
            "rating":        float(row["rating"])       if pd.notna(row.get("rating"))         else 0.0,
            # Parking, from data/parking.csv (real OSM data -- see enrich_parking.py).
            # 0.0 / -1 / "" are "not applicable" sentinels, gated behind has_parking;
            # Chroma metadata values can't be null, so this mirrors the "None"-string
            # sentinel pattern already used above for closed_on/special_hours.
            "has_parking":         _parse_bool(row.get("has_parking", False)),
            "parking_lat":         float(row["parking_lat"]) if pd.notna(row.get("parking_lat")) else 0.0,
            "parking_lng":         float(row["parking_lng"]) if pd.notna(row.get("parking_lng")) else 0.0,
            "parking_distance_m":  int(row["parking_distance_m"]) if pd.notna(row.get("parking_distance_m")) else -1,
            "parking_name":        str(row["parking_name"]) if pd.notna(row.get("parking_name")) else "",
            "parking_source":      str(row["parking_source"]) if pd.notna(row.get("parking_source")) else "not_checked",
        }

        collection.add(
            ids=[l_id],
            embeddings=[vector],
            documents=[doc_text],
            metadatas=[metadata],
        )
        print(f"  {l_id}: {name}")

    count = collection.count()
    if count != len(df):
        raise RuntimeError(
            f"Ingest mismatch: {len(df)} in CSV but only {count} landed in Chroma. "
            f"Delete {VDB_PATH} and retry."
        )
    print(f"\nDone -- {count} places in '{COLLECTION}' at {VDB_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stub", action="store_true",
                        help="Use stub embeddings (no internet needed, wiring test only)")
    args = parser.parse_args()
    run_ingest(use_stub=args.stub)
