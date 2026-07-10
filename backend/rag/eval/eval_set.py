"""
eval_set.py -- retrieval-quality gold labels for Tripy's RAG search.

Methodology: relevance judgments are generated with a RULE, not hand-picked
by eye. Each query names a `category_in` list and/or `vibe_any` keyword list
(substring match against the landmarks.csv `category` / vibe_tags.csv
`vibe_tags` columns); `resolve_gold()` applies the rule to the real dataset
to produce the gold id set. This is weak supervision over metadata that
already exists in the data, not invented judgments -- it's reproducible
(rerun against a changed dataset and the gold set updates itself) and every
query's rule is visible right next to it, so a reviewer can sanity-check the
label instead of trusting a black box.

Trade-off, stated plainly: keyword rules are coarser than a human relevance
judgment (e.g. a "Peaceful temples" rule will miss a temple that's peaceful
in its review text but wasn't tagged that vibe). That noise is why the
harness also reports raw hit lists, not just a score -- so the numbers stay
inspectable, not just a headline percentage.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


@dataclass
class EvalQuery:
    query: str
    category_in: Optional[List[str]] = None
    vibe_any: Optional[List[str]] = None
    combine: str = "OR"   # how to combine category_in + vibe_any when both given: "AND" | "OR"
    note: str = ""        # what real-world intent this query is standing in for


QUERIES: List[EvalQuery] = [
    EvalQuery("Ancient temples to explore in the city", category_in=["Temple"]),
    EvalQuery("Museums with history and artifacts", category_in=["Museum"]),
    EvalQuery("Royal palaces and old forts", category_in=["Heritage"]),
    EvalQuery("Sandy beaches for a sunset walk", category_in=["Beach"]),
    EvalQuery("Family-friendly parks and gardens", category_in=["Park"]),
    EvalQuery("Bustling local markets and bazaars", category_in=["Market"]),
    EvalQuery("Art galleries and creative cultural spaces", category_in=["Art"]),
    EvalQuery("Offbeat hidden gems away from the tourist crowd", category_in=["Hidden Gem"]),
    EvalQuery("Nature spots and scenic landscapes", category_in=["Nature"]),
    EvalQuery("Zoo to see wild animals", category_in=["Zoo"]),
    EvalQuery("Fun theme park with shows for kids", category_in=["Theme Park"]),
    EvalQuery("Historic mosque to visit", category_in=["Religious"]),
    EvalQuery("Cozy cafes for coffee or tea", category_in=["Cafe"]),
    EvalQuery("Wildlife and animal sanctuaries", vibe_any=["Wildlife"]),
    EvalQuery("Science museums and planetariums for kids", vibe_any=["Science"]),
    EvalQuery("Waterfalls and trekking trails", vibe_any=["Trekking", "Trek", "Waterfall", "Waterfalls"]),
    EvalQuery(
        "Quiet, peaceful temples away from the crowds",
        category_in=["Temple"], vibe_any=["Peaceful", "Quiet", "Serene", "Tranquil"], combine="AND",
    ),
    EvalQuery("Rooftop spots with a view in the evening", vibe_any=["Rooftop"]),
    EvalQuery("Boating and backwater experiences", vibe_any=["Boating", "Backwaters"]),
    EvalQuery("Best sunset viewpoints by the sea", vibe_any=["Sunset"]),
    EvalQuery("Scenic spots that are great for photography", vibe_any=["Photography"]),
    EvalQuery("Fun family activities with kids", vibe_any=["Family", "Kids", "Child-friendly"]),
    EvalQuery("Colonial-era and British heritage sites", vibe_any=["British-era", "Colonial"]),
    EvalQuery("Coastal landmarks and lighthouses", vibe_any=["Lighthouse", "Coastal"]),
    EvalQuery("Pure vegetarian restaurants for lunch", category_in=["Restaurant"], vibe_any=["Vegetarian", "Pure-veg"], combine="AND"),
    EvalQuery("Where can I get good biryani", vibe_any=["Biryani"]),
    EvalQuery("Seafood restaurants near the coast", vibe_any=["Seafood", "Fish", "Prawns"]),
    EvalQuery(
        "Cheap, budget-friendly places to eat",
        category_in=["Restaurant", "Cafe"], vibe_any=["Budget", "Affordable"], combine="AND",
    ),
    EvalQuery("Upscale fine dining for a special evening", vibe_any=["Fine-dining", "Upscale", "Luxury"]),
    EvalQuery("Ice cream and dessert spots", vibe_any=["Ice-cream", "Dessert"]),
    EvalQuery("Traditional Kerala martial arts performance", vibe_any=["Kalaripayattu", "Martial-arts"]),
    EvalQuery("Sacred and devotional pilgrimage sites", vibe_any=["Sacred", "Devotional"]),
    EvalQuery("Adventure and hiking spots", vibe_any=["Adventure", "Hiking"]),
    EvalQuery("Places known for striking architecture", vibe_any=["Architecture", "Architectural"]),
    EvalQuery("Lakes and dams for a calm afternoon", vibe_any=["Lake", "Dam", "Reservoir"]),
    EvalQuery("Street food and local snacking spots", vibe_any=["Street-food"]),
    EvalQuery("North Indian food restaurant", vibe_any=["North-Indian"]),
    EvalQuery("Palace with intricate woodwork and antiques", vibe_any=["Antique", "Craftsmanship", "Woodwork"]),
]


def _load_df() -> pd.DataFrame:
    main  = pd.read_csv(DATA_DIR / "landmarks.csv")
    vibes = pd.read_csv(DATA_DIR / "vibe_tags.csv")
    return pd.merge(main.drop(columns=["vibe_tags", "name"]), vibes, on="id")


def resolve_gold(df: pd.DataFrame, q: EvalQuery) -> List[str]:
    """Apply one EvalQuery's rule to the dataset, return the matching id list."""
    cat_mask = pd.Series(False, index=df.index)
    if q.category_in:
        cat_mask = df["category"].isin(q.category_in)

    vibe_mask = pd.Series(False, index=df.index)
    if q.vibe_any:
        lowered = df["vibe_tags"].str.lower()
        vibe_mask = lowered.apply(lambda tags: any(kw.lower() in tags for kw in q.vibe_any))

    if q.category_in and q.vibe_any:
        mask = (cat_mask & vibe_mask) if q.combine == "AND" else (cat_mask | vibe_mask)
    elif q.category_in:
        mask = cat_mask
    else:
        mask = vibe_mask

    return df.loc[mask, "id"].astype(str).tolist()


def build_gold_set() -> List[dict]:
    """Resolve every query's gold ids against the live dataset. Drops (and
    warns on) any rule that happens to match nothing, so a typo'd keyword
    fails loud in eval output instead of silently scoring 0."""
    df = _load_df()
    out = []
    for q in QUERIES:
        ids = resolve_gold(df, q)
        if not ids:
            print(f"WARNING: query {q.query!r} resolved to an EMPTY gold set -- check the rule.")
            continue
        out.append({"query": q.query, "relevant_ids": ids, "note": q.note})
    return out


if __name__ == "__main__":
    # Sanity-print every query's resolved gold set for manual review.
    df = _load_df()
    id_to_name = dict(zip(df["id"].astype(str), df["name"]))
    for q in QUERIES:
        ids = resolve_gold(df, q)
        names = ", ".join(id_to_name[i] for i in ids)
        print(f"[{len(ids):>2}] {q.query}\n     -> {names or '(EMPTY -- fix this rule)'}\n")
