import json
import pickle
import hashlib
import numpy as np
from openai import OpenAI
import os

from assign_embeddings2 import categorize_product

client = OpenAI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── File paths ─────────────────────────────────────────────────────────────────
RESTRICTED_EMBEDDINGS_FILE  = os.path.join(BASE_DIR, "restricted_categories_embeddings.pkl")

# Cache 1: title_hash → full categorization result dict  (skips LLM on re-runs)
TITLE_CATEGORY_CACHE_FILE   = os.path.join(BASE_DIR, "title_categorization_cache.pkl")

# Cache 2: predicted_category_text_hash → embedding  (skips embedding API on re-runs)
CATEGORY_EMBED_CACHE_FILE   = os.path.join(BASE_DIR, "predicted_category_embeddings_cache.pkl")


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_key(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def _load_pkl(path: str, label: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            print(f"📦 {label} loaded: {len(data)} entries from '{os.path.basename(path)}'")
            return data
        except Exception as e:
            print(f"⚠️  Could not load {label} (starting fresh): {e}")
    else:
        print(f"📦 No {label} found — will create '{os.path.basename(path)}'")
    return {}


def _save_pkl(path: str, data: dict, label: str) -> None:
    try:
        with open(path, "wb") as f:
            pickle.dump(data, f)
        print(f"💾 {label} saved: {len(data)} entries → '{os.path.basename(path)}'")
    except Exception as e:
        print(f"⚠️  Could not save {label}: {e}")


# ── LLM categorization (with cache) ───────────────────────────────────────────

def categorize_product_cached(title: str, cache: dict) -> tuple[dict, bool]:
    """
    Return (categorization_result, from_cache).
    Keyed by title hash — skips the LLM entirely on re-runs for seen titles.
    """
    key = _cache_key(title)
    if key in cache:
        return cache[key], True
    result = categorize_product(title, "")   # no description available
    cache[key] = result
    return result, False


# ── Embedding helpers ──────────────────────────────────────────────────────────

def get_embedding(text: str) -> np.ndarray:
    response = client.embeddings.create(
        model="text-embedding-3-large",
        input=text.strip()
    )
    return np.array(response.data[0].embedding)


def get_embedding_cached(text: str, cache: dict) -> tuple[np.ndarray, bool]:
    """
    Return (embedding, from_cache).
    Keyed by predicted category text hash — products sharing the same predicted
    category reuse the same embedding vector for free.
    """
    key = _cache_key(text)
    if key in cache:
        return cache[key], True
    embedding = get_embedding(text)
    cache[key] = embedding
    return embedding, False


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    v1, v2 = np.array(vec1), np.array(vec2)
    denom = np.linalg.norm(v1) * np.linalg.norm(v2)
    return float(np.dot(v1, v2) / denom) if denom else 0.0


# ── Restricted categories loader ───────────────────────────────────────────────

def load_restricted_embeddings(embeddings_file: str) -> tuple[list, list]:
    with open(embeddings_file, "rb") as f:
        data = pickle.load(f)

    TEXT_KEYS = ("category", "name", "label", "keyword")

    if isinstance(data, list) and isinstance(data[0], dict):
        text_key = next((k for k in TEXT_KEYS if k in data[0]), None)
        if not text_key:
            raise ValueError(f"No recognised text key. Keys: {set(data[0].keys())}")
        categories = [item[text_key] for item in data]
        embeddings = [np.array(item["embedding"], dtype=np.float64) for item in data]

    elif isinstance(data, list) and len(data) == 2 and not isinstance(data[0], dict):
        categories = data[0]
        embeddings = [np.array(e, dtype=np.float64) for e in data[1]]

    elif isinstance(data, dict):
        text_key = next((k for k in TEXT_KEYS if k in data), None)
        if not text_key:
            raise ValueError(f"No recognised text key. Keys: {list(data.keys())}")
        categories = data[text_key]
        embeddings = [np.array(e, dtype=np.float64) for e in data["embedding"]]

    else:
        raise ValueError(f"Unknown pkl format: {type(data)}")

    print(f"✅ {len(categories)} restricted categories loaded")
    return categories, embeddings


def find_best_restricted_match(query_embedding: np.ndarray,
                                restricted_embeddings: list) -> dict:
    best_score, best_index = -1.0, 0
    for i, emb in enumerate(restricted_embeddings):
        score = cosine_similarity(query_embedding, emb)
        if score > best_score:
            best_score, best_index = score, i
    return {"max_similarity": best_score, "matched_index": best_index}


# ── Main filter ────────────────────────────────────────────────────────────────

def filter_products_by_category(
    products_file:   str,
    embeddings_file: str,
    output_file:     str,
    threshold:       float = 0.45,
) -> dict:
    """
    Category-first product filter — two caching layers:

      Cache 1  title_categorization_cache.pkl
               title hash → categorization result (LLM output)
               Skips the gpt-4o-mini call entirely for seen titles on re-runs.

      Cache 2  predicted_category_embeddings_cache.pkl
               predicted_category_text hash → embedding vector
               Skips the embedding API call when multiple products share the
               same predicted category path.

    Flow per product:
      title → [LLM, cache 1] → predicted_category_text
            → [embed API, cache 2] → category_embedding
            → cosine similarity vs restricted_categories_embeddings.pkl
            → keep / remove
    """
    print("=" * 68)
    print("🚀  Product filter — category-first similarity check")
    print(f"    Threshold : {threshold}")
    print("=" * 68)

    # ── Load restricted categories ─────────────────────────────────────────────
    print("\n[1/5] Loading restricted category embeddings …")
    restricted_cats, restricted_embeddings = load_restricted_embeddings(embeddings_file)
    print(f"      {len(restricted_cats)} restricted categories\n")

    # ── Load both caches ───────────────────────────────────────────────────────
    print("[2/5] Loading caches …")
    title_cat_cache  = _load_pkl(TITLE_CATEGORY_CACHE_FILE, "LLM categorization cache")
    embed_cache      = _load_pkl(CATEGORY_EMBED_CACHE_FILE,  "Category embedding cache")
    llm_hits = llm_calls = embed_hits = embed_calls = 0
    print()

    # ── Load products ──────────────────────────────────────────────────────────
    print("[3/5] Loading products …")
    with open(products_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    total_products = data.get("total_products", 0)
    print(f"      {total_products} products across {len(data['results'])} categories\n")

    # ── Filter ─────────────────────────────────────────────────────────────────
    print("[4/5] Filtering …")
    print("-" * 68)

    filtered_results = {}
    total_removed    = 0

    for cat_name, cat_data in data["results"].items():
        products          = cat_data["products"]
        filtered_products = []
        cat_removed       = 0

        print(f"\n📂  '{cat_name}' ({len(products)} products)")

        for product in products:
            title = product.get("title", "")

            try:
                # Step 1: LLM assigns Octopia category (cache 1 — skips LLM if seen)
                categorization, llm_from_cache = categorize_product_cached(title, title_cat_cache)
                if llm_from_cache:
                    llm_hits += 1
                else:
                    llm_calls += 1

                predicted_cat    = categorization["llm_predicted_category"]
                octopia_cat_id   = categorization["category_id"]
                octopia_cat_path = categorization["category_path"]
                octopia_sim      = categorization["similarity_score"]

                # Step 2: Embed predicted category (cache 2 — skips API if same path seen)
                cat_embedding, embed_from_cache = get_embedding_cached(predicted_cat, embed_cache)
                if embed_from_cache:
                    embed_hits += 1
                else:
                    embed_calls += 1

                # Step 3: Compare against restricted categories
                match          = find_best_restricted_match(cat_embedding, restricted_embeddings)
                max_similarity = match["max_similarity"]
                matched_cat    = restricted_cats[match["matched_index"]]

                # Attach full categorization metadata to product for audit/downstream use
                product["_category"] = {
                    "llm_predicted":    predicted_cat,
                    "octopia_id":       octopia_cat_id,
                    "octopia_path":     octopia_cat_path,
                    "octopia_sim":      octopia_sim,
                    "restricted_match": matched_cat,
                    "restricted_sim":   max_similarity,
                    "llm_from_cache":   llm_from_cache,
                }

                src = f"llm={'cache' if llm_from_cache else 'API'}, emb={'cache' if embed_from_cache else 'API'}"

                if max_similarity > threshold:
                    print(
                        f"  ❌ REMOVED  ({src}, sim={max_similarity:.3f}, "
                        f"matched='{matched_cat}', predicted='{predicted_cat}'): "
                        f"{title[:60]}"
                    )
                    cat_removed   += 1
                    total_removed += 1
                else:
                    filtered_products.append(product)
                    if max_similarity > 0.65:
                        print(
                            f"  ⚠️  KEPT    ({src}, sim={max_similarity:.3f}, "
                            f"closest='{matched_cat}', predicted='{predicted_cat}'): "
                            f"{title[:60]}"
                        )

            except Exception as e:
                print(f"  ⚠️  ERROR on '{title[:50]}': {e}")
                filtered_products.append(product)   # keep on error

        filtered_results[cat_name] = {
            "keyword":  cat_data.get("keyword", cat_name),
            "products": filtered_products,
            "stats": {
                "original_count": len(products),
                "filtered_count": len(filtered_products),
                "removed_count":  cat_removed,
            },
        }

        print(f"  → {len(products)} → {len(filtered_products)} (-{cat_removed} removed)")

    # ── Persist both caches ────────────────────────────────────────────────────
    print("\n[5/5] Saving caches …")
    _save_pkl(TITLE_CATEGORY_CACHE_FILE, title_cat_cache, "LLM categorization cache")
    _save_pkl(CATEGORY_EMBED_CACHE_FILE,  embed_cache,     "Category embedding cache")

    # ── Build output ───────────────────────────────────────────────────────────
    total_filtered = sum(c["stats"]["filtered_count"] for c in filtered_results.values())

    output_data = {
        "metadata": {
            "scraped_at":                  data.get("scraped_at"),
            "categories_searched":         data.get("categories_searched"),
            "pages_per_category":          data.get("pages_per_category"),
            "similarity_threshold":        threshold,
            "total_original":              total_products,
            "total_filtered":              total_filtered,
            "total_removed":               total_removed,
            "restricted_categories_count": len(restricted_cats),
            "llm_cache_hits":              llm_hits,
            "llm_api_calls":               llm_calls,
            "embed_cache_hits":            embed_hits,
            "embed_api_calls":             embed_calls,
        },
        "results": filtered_results,
        "note": (
            f"Category-first filter: LLM assigns Octopia category per product "
            f"(cached by title), then category embedding (cached by category text) "
            f"is compared against restricted categories (threshold={threshold})."
        ),
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 68)
    print("🎉  FILTERING COMPLETE")
    print(f"    📊 Original      : {total_products:,}")
    print(f"    ✅ Kept          : {total_filtered:,}")
    print(f"    ❌ Removed       : {total_removed:,}")
    print(f"    🤖 LLM   hits/calls : {llm_hits:,} / {llm_calls:,}")
    print(f"    🔢 Embed hits/calls : {embed_hits:,} / {embed_calls:,}")
    print(f"    📁 Saved to      : {output_file}")
    print("=" * 68)

    return output_data


# ── CLI entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    PRODUCTS_FILE   = "aliexpress_products_filtered.json"
    EMBEDDINGS_FILE = "restricted_categories_embeddings.pkl"
    OUTPUT_FILE     = "aliexpress_products_filtered_by_category.json"

    filter_products_by_category(
        products_file   = PRODUCTS_FILE,
        embeddings_file = EMBEDDINGS_FILE,
        output_file     = OUTPUT_FILE,
        threshold       = 0.45,
    )
