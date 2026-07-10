"""
eval_retrieval.py -- retrieval-quality benchmark for Tripy's RAG search.

Runs the 38 queries in eval_set.py against four retrievers over the SAME
65-place corpus, entirely in-memory:

  1. random       -- sanity floor. Anything must beat this to mean anything.
  2. tfidf        -- classic lexical baseline (bag-of-words cosine similarity).
  3. all-MiniLM-L6-v2   -- the model actually running in rag/search.py today.
  4. bge-small-en-v1.5  -- a modern, similarly-sized challenger.

Never touches the live Chroma store at trivandrum_vdb/ -- corpus embeddings
are computed fresh in memory each run, so this can't drift or corrupt the
app's actual index. Safe to run anytime, including while the app is live.

Usage:
    python -m rag.eval.eval_retrieval
"""

import math
import random
from pathlib import Path
from typing import Dict, List

import numpy as np

from .eval_set import build_gold_set, _load_df

DATA_DIR   = Path(__file__).resolve().parents[3] / "data"
RESULTS_MD = Path(__file__).resolve().parent / "results.md"
K_VALUES   = [5, 10]

CHALLENGER_MODEL = "BAAI/bge-small-en-v1.5"
CURRENT_MODEL    = "sentence-transformers/all-MiniLM-L6-v2"  # must match rag/search.py's MODEL_NAME


def build_corpus():
    df = _load_df()
    ids, texts, names = [], [], []
    for _, row in df.iterrows():
        pid = str(row["id"])
        review_path = DATA_DIR / "landmark_reviews" / f"{pid}.txt"
        review = review_path.read_text(encoding="utf-8") if review_path.exists() \
            else f"A {row['category']} located in Thiruvananthapuram."
        doc_text = f"Place: {row['name']}. Vibe: {row['vibe_tags']}. Insight: {review}"
        ids.append(pid)
        texts.append(doc_text)
        names.append(row["name"])
    return ids, texts, dict(zip(ids, names))


# ---------------------------------------------------------------------------
# Retrievers -- each is a factory returning rank(query) -> ranked id list
# ---------------------------------------------------------------------------

def random_retriever(ids, texts):
    rng = random.Random(42)
    def rank(query: str) -> List[str]:
        shuffled = ids[:]
        rng.shuffle(shuffled)
        return shuffled
    return rank


def tfidf_retriever(ids, texts):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    vec = TfidfVectorizer(stop_words="english")
    doc_matrix = vec.fit_transform(texts)
    def rank(query: str) -> List[str]:
        sims = cosine_similarity(vec.transform([query]), doc_matrix)[0]
        return [ids[i] for i in np.argsort(-sims)]
    return rank


def sbert_retriever(ids, texts, model_name: str):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    doc_emb = np.asarray(model.encode(texts, normalize_embeddings=True, show_progress_bar=False))
    def rank(query: str) -> List[str]:
        q_emb = np.asarray(model.encode([query], normalize_embeddings=True, show_progress_bar=False))[0]
        sims = doc_emb @ q_emb
        return [ids[i] for i in np.argsort(-sims)]
    return rank


# ---------------------------------------------------------------------------
# Metrics -- standard IR definitions, binary relevance
# ---------------------------------------------------------------------------

def precision_at_k(ranked, gold, k):
    return sum(1 for i in ranked[:k] if i in gold) / k

def recall_at_k(ranked, gold, k):
    return sum(1 for i in ranked[:k] if i in gold) / len(gold)

def reciprocal_rank(ranked, gold):
    for i, pid in enumerate(ranked, start=1):
        if pid in gold:
            return 1.0 / i
    return 0.0

def ndcg_at_k(ranked, gold, k):
    dcg = sum(1.0 / math.log2(i + 1) for i, pid in enumerate(ranked[:k], start=1) if pid in gold)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(k, len(gold)) + 1))
    return dcg / idcg if idcg > 0 else 0.0


def evaluate(rank_fn, gold_set: List[dict]):
    """Returns (macro_metrics, per_query_rows) -- the latter for error analysis."""
    agg = {f"P@{k}": [] for k in K_VALUES}
    agg.update({f"R@{k}": [] for k in K_VALUES})
    agg.update({f"nDCG@{k}": [] for k in K_VALUES})
    agg["MRR"] = []
    rows = []

    for item in gold_set:
        ranked = rank_fn(item["query"])
        gold = set(item["relevant_ids"])
        row = {"query": item["query"], "n_gold": len(gold)}
        for k in K_VALUES:
            row[f"P@{k}"]    = precision_at_k(ranked, gold, k)
            row[f"R@{k}"]    = recall_at_k(ranked, gold, k)
            row[f"nDCG@{k}"] = ndcg_at_k(ranked, gold, k)
        row["MRR"] = reciprocal_rank(ranked, gold)
        for name, val in row.items():
            if name in agg:
                agg[name].append(val)
        rows.append(row)

    macro = {name: sum(vals) / len(vals) for name, vals in agg.items()}
    return macro, rows


# ---------------------------------------------------------------------------

def _fmt_row(name, m):
    return f"| {name:<20} | " + " | ".join(f"{m[k]:.3f}" for k in ["P@5", "R@5", "nDCG@5", "P@10", "R@10", "nDCG@10", "MRR"]) + " |"


def main():
    print("Loading corpus + gold set...")
    ids, texts, id_to_name = build_corpus()
    gold_set = build_gold_set()
    print(f"{len(ids)} places, {len(gold_set)} eval queries.\n")

    backends = [
        ("random",          lambda: random_retriever(ids, texts)),
        ("tfidf",           lambda: tfidf_retriever(ids, texts)),
        ("all-MiniLM-L6-v2 (current)", lambda: sbert_retriever(ids, texts, CURRENT_MODEL)),
        ("bge-small-en-v1.5",          lambda: sbert_retriever(ids, texts, CHALLENGER_MODEL)),
    ]

    header = ("| Model                | P@5   | R@5   | nDCG@5 | P@10  | R@10  | nDCG@10 | MRR   |\n"
              "|----------------------|-------|-------|--------|-------|-------|---------|-------|")
    print(header)
    results = {}
    per_query = {}
    for name, factory in backends:
        rank_fn = factory()
        macro, rows = evaluate(rank_fn, gold_set)
        results[name] = macro
        per_query[name] = rows
        print(_fmt_row(name, macro))

    # Error analysis: worst-scoring queries for the production model, so the
    # report shows WHERE retrieval is weak, not just a single aggregate number.
    prod_rows = sorted(per_query["all-MiniLM-L6-v2 (current)"], key=lambda r: r["nDCG@10"])[:8]

    lines = ["# Retrieval eval results\n",
             f"Corpus: {len(ids)} places (`data/landmarks.csv` + `data/vibe_tags.csv`). "
             f"{len(gold_set)} queries, gold labels rule-based (see `eval_set.py`).\n",
             header]
    for name, _ in backends:
        lines.append(_fmt_row(name, results[name]))
    lines.append("\n## Weakest queries for the production model (all-MiniLM-L6-v2)\n")
    lines.append("| Query | nDCG@10 | MRR | # gold |")
    lines.append("|---|---|---|---|")
    for r in prod_rows:
        lines.append(f"| {r['query']} | {r['nDCG@10']:.3f} | {r['MRR']:.3f} | {r['n_gold']} |")

    lines.append("\n## How to read this\n")
    lines.append(
        "- `random` is a sanity floor -- every retriever should clear it by a wide margin, "
        "otherwise the harness itself is broken, not the model.\n"
        "- **TF-IDF scores unusually well here because of a weakness in this eval's methodology, "
        "not because lexical search beats embeddings in general.** Gold labels are generated by "
        "matching `category`/`vibe_tags` keywords (see `eval_set.py`), and queries were written "
        "to name those same categories/vibes in plain English (\"ancient temples\" -> `category: "
        "Temple`). That keyword overlap is exactly what TF-IDF exploits, so its score here is "
        "inflated relative to how it'd do against real, messier user queries that don't echo the "
        "metadata vocabulary. It's kept in the table anyway because a lexical baseline any dense "
        "retriever can't beat on a knowledge base this literal is itself a useful signal.\n"
        "- The dense-model comparison (current vs. challenger) is the part that generalizes: both "
        "see the same queries and the same lexical-overlap advantage/disadvantage, so the gap "
        "between them isolates embedding quality, not eval bias.\n"
        f"- Rerun anytime with `python -m rag.eval.eval_retrieval` -- it never touches the live "
        f"vector store, only builds a fresh in-memory index from `data/*.csv` each time."
    )

    RESULTS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {RESULTS_MD}")


if __name__ == "__main__":
    main()
