import json
import pickle
import hashlib
import numpy as np
from openai import OpenAI
import os

client = OpenAI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── File paths ─────────────────────────────────────────────────────────────────
RESTRICTED_EMBEDDINGS_FILE = os.path.join(BASE_DIR, "restricted_keywords_embeddings.pkl")
TITLE_CACHE_FILE           = os.path.join(BASE_DIR, "title_embeddings_cache.pkl")


# ── Embedding cache ────────────────────────────────────────────────────────────

def _load_title_cache() -> dict:
    """Load persisted title→embedding cache from disk. Returns empty dict if missing."""
    if os.path.exists(TITLE_CACHE_FILE):
        try:
            with open(TITLE_CACHE_FILE, "rb") as f:
                cache = pickle.load(f)
            print(f"📦 Title cache loaded: {len(cache)} entries from '{TITLE_CACHE_FILE}'")
            return cache
        except Exception as e:
            print(f"⚠️  Could not load title cache (starting fresh): {e}")
    else:
        print(f"📦 No title cache found — will create '{TITLE_CACHE_FILE}'")
    return {}


def _save_title_cache(cache: dict) -> None:
    """Persist the title→embedding cache to disk."""
    try:
        with open(TITLE_CACHE_FILE, "wb") as f:
            pickle.dump(cache, f)
        print(f"💾 Title cache saved: {len(cache)} entries → '{TITLE_CACHE_FILE}'")
    except Exception as e:
        print(f"⚠️  Could not save title cache: {e}")


def _cache_key(text: str) -> str:
    """Stable cache key: SHA-256 of the stripped, lowercased title."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


# ── Core helpers ───────────────────────────────────────────────────────────────

def get_embedding(text: str) -> np.ndarray:
    response = client.embeddings.create(
        model="text-embedding-3-large",
        input=text.strip()
    )
    return np.array(response.data[0].embedding)


def get_embedding_cached(text: str, cache: dict) -> tuple[np.ndarray, bool]:
    """
    Return (embedding, from_cache).
    Looks up by SHA-256 key; fetches from API only on miss and updates cache in-place.
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


# ── Restricted-keyword loader ──────────────────────────────────────────────────

def load_restricted_embeddings(embeddings_file: str) -> tuple[list, list]:
    """Handles list-of-dicts, 2-column list, or dict format."""
    with open(embeddings_file, "rb") as f:
        data = pickle.load(f)

    print(f"📋 Restricted embeddings — raw type: {type(data)}, length: {len(data)}")

    if isinstance(data, list) and isinstance(data[0], dict):
        keywords   = [item["keyword"] for item in data]
        embeddings = [np.array(item["embedding"], dtype=np.float64) for item in data]
        print("✅ Detected LIST-OF-DICTS format")

    elif isinstance(data, list) and len(data) >= 2 and not isinstance(data[0], dict):
        keywords   = data[0]
        embeddings = [np.array(emb, dtype=np.float64) for emb in data[1]]
        print("✅ Detected 2-COLUMN LIST format")

    elif isinstance(data, dict):
        keywords   = data["keyword"]
        embeddings = [np.array(emb, dtype=np.float64) for emb in data["embedding"]]
        print("✅ Detected DICT format")

    else:
        raise ValueError(f"Unknown restricted-embeddings format: {type(data)}")

    print(f"   {len(keywords)} keywords, embedding shape: {embeddings[0].shape}")
    print(f"   Keywords: {keywords}")
    return keywords, embeddings


# ── Matcher ────────────────────────────────────────────────────────────────────

def find_best_restricted_match(title_embedding: np.ndarray,
                                restricted_embeddings: list) -> dict:
    """Return best cosine-similarity score and the index of the matched keyword."""
    best_score = -1.0
    best_index = 0

    for i, stored_emb in enumerate(restricted_embeddings):
        score = cosine_similarity(title_embedding, stored_emb)
        if score > best_score:
            best_score = score
            best_index = i

    return {"max_similarity": best_score, "matched_keyword_index": best_index}


# ── Main filter ────────────────────────────────────────────────────────────────

def filter_products_by_restricted_keywords(
    products_file:  str,
    embeddings_file: str,
    output_file:    str,
    threshold:      float = 0.75,   # ← raised from 0.5
) -> dict:
    """
    Filter AliExpress products whose titles are too similar to any restricted keyword.

    Improvements over v1:
      • Threshold raised to 0.75 to eliminate false positives.
      • Title embeddings are cached on disk — repeated runs skip the API for
        already-seen titles (huge cost & latency saving).
      • Detailed per-product logs show similarity score AND the matched keyword.
    """
    print("=" * 68)
    print("🚀  AliExpress product filter — restricted-keyword similarity check")
    print(f"    Threshold : {threshold}")
    print(f"    Cache file: {TITLE_CACHE_FILE}")
    print("=" * 68)

    # ── Load restricted keywords ───────────────────────────────────────────────
    print("\n[1/4] Loading restricted keyword embeddings …")
    restricted_keywords, restricted_embeddings = load_restricted_embeddings(embeddings_file)
    print(f"      {len(restricted_keywords)} restricted keywords ready\n")

    # ── Load title embedding cache ─────────────────────────────────────────────
    print("[2/4] Loading title embedding cache …")
    title_cache = _load_title_cache()
    cache_hits  = 0
    api_calls   = 0
    print()

    # ── Load products ──────────────────────────────────────────────────────────
    print("[3/4] Loading AliExpress products …")
    with open(products_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    total_products = data.get("total_products", 0)
    print(f"      {total_products} products across {len(data['results'])} categories\n")

    # ── Filter ─────────────────────────────────────────────────────────────────
    print("[4/4] Filtering …")
    print("-" * 68)

    filtered_results = {}
    total_removed    = 0

    for category_name, category_data in data["results"].items():
        products          = category_data["products"]
        filtered_products = []
        category_removed  = 0

        print(f"\n📂  Category: '{category_name}' ({len(products)} products)")

        for product in products:
            title = product["title"]

            try:
                title_embedding, from_cache = get_embedding_cached(title, title_cache)

                if from_cache:
                    cache_hits += 1
                    cache_label = "cache"
                else:
                    api_calls += 1
                    cache_label = "API"

                match_result   = find_best_restricted_match(title_embedding, restricted_embeddings)
                max_similarity = match_result["max_similarity"]
                matched_kw     = restricted_keywords[match_result["matched_keyword_index"]]

                if max_similarity > threshold:
                    print(
                        f"    ❌ REMOVED  "
                        f"(sim={max_similarity:.3f}, matched='{matched_kw}', src={cache_label}): "
                        f"{title[:80]}"
                    )
                    category_removed += 1
                    total_removed    += 1
                else:
                    filtered_products.append(product)

                    # Warn if close but kept
                    if max_similarity > 0.65:
                        print(
                            f"    ⚠️  KEPT    "
                            f"(sim={max_similarity:.3f}, closest='{matched_kw}', src={cache_label}): "
                            f"{title[:80]}"
                        )

            except Exception as e:
                print(f"    ⚠️  ERROR processing '{title[:50]}': {e}")
                filtered_products.append(product)   # keep on error

        filtered_results[category_name] = {
            "keyword":  category_data.get("keyword", category_name),
            "products": filtered_products,
            "stats": {
                "original_count": len(products),
                "filtered_count": len(filtered_products),
                "removed_count":  category_removed,
            },
        }

        print(
            f"    → {len(products)} → {len(filtered_products)} products "
            f"(-{category_removed} removed)"
        )

    # ── Persist updated cache ──────────────────────────────────────────────────
    _save_title_cache(title_cache)

    # ── Build output ───────────────────────────────────────────────────────────
    total_filtered = sum(c["stats"]["filtered_count"] for c in filtered_results.values())

    output_data = {
        "metadata": {
            "scraped_at":               data.get("scraped_at"),
            "categories_searched":      data.get("categories_searched"),
            "pages_per_category":       data.get("pages_per_category"),
            "similarity_threshold":     threshold,
            "total_original":           total_products,
            "total_filtered":           total_filtered,
            "total_removed":            total_removed,
            "restricted_keywords_count": len(restricted_keywords),
            "cache_hits":               cache_hits,
            "api_calls":                api_calls,
        },
        "results": filtered_results,
        "note": f"Filtered products with >{threshold} similarity to restricted keywords",
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("🎉  FILTERING COMPLETE")
    print(f"    📊 Original  : {total_products:,}")
    print(f"    ✅ Kept      : {total_filtered:,}")
    print(f"    ❌ Removed   : {total_removed:,}")
    print(f"    📦 Cache hits: {cache_hits:,}  |  🌐 API calls: {api_calls:,}")
    print(f"    📁 Saved to  : {output_file}")
    print("=" * 68)

    return output_data


# ── CLI entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    PRODUCTS_FILE   = "aliexpress_products.json"
    EMBEDDINGS_FILE = "restricted_keywords_embeddings.pkl"
    OUTPUT_FILE     = "aliexpress_products_filtered.json"

    filter_products_by_restricted_keywords(
        products_file   = PRODUCTS_FILE,
        embeddings_file = EMBEDDINGS_FILE,
        output_file     = OUTPUT_FILE,
        threshold       = 0.75,
    )
