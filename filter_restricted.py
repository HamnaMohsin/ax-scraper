import json
import pickle
import numpy as np
from openai import OpenAI
import os

client = OpenAI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Now uses octopia categories instead of google product categories
embeddings_path = os.path.join(BASE_DIR, "restricted_keywords_embeddings.pkl")

def get_embedding(text: str):
    """Get embedding using text-embedding-3-large (your existing function)"""
    response = client.embeddings.create(
        model="text-embedding-3-large",
        input=text.strip()
    )
    return np.array(response.data[0].embedding)

def cosine_similarity(vec1, vec2):
    """Cosine similarity function (your existing function)"""
    v1 = np.array(vec1)
    v2 = np.array(vec2)
    return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))

def load_restricted_embeddings(embeddings_file):
    """column A=keyword, column B=embedding - YOUR EXACT FORMAT"""
    with open(embeddings_file, "rb") as f:
        data = pickle.load(f)
    
    # EXACT column names you specified
    keywords = data['keyword']           # column A
    embeddings = [np.array(emb) for emb in data['embedding']]  # column B
    
    print(f"✅ Loaded {len(keywords)} keywords + {len(embeddings)} embeddings")
    return keywords, embeddings

def find_best_restricted_match(title_embedding, restricted_embeddings):
    """Find highest similarity match (inspired by your find_best_category)"""
    best_score = -1
    best_keyword = None

    for i, stored_embedding in enumerate(restricted_embeddings):
        score = cosine_similarity(title_embedding, stored_embedding)

        if score > best_score:
            best_score = score
            best_keyword = i  # Store index for reference

    return {
        "max_similarity": best_score,
        "matched_keyword_index": best_keyword
    }

def filter_products_by_restricted_keywords(products_file, embeddings_file, output_file, threshold=0.8):
    """
    Filter AliExpress products by checking similarity against restricted keywords
    """
    print("Loading restricted keywords embeddings...")
    restricted_keywords, restricted_embeddings = load_restricted_embeddings(embeddings_file)
    print(f"Loaded {len(restricted_keywords)} restricted keywords")
    
    print("Loading AliExpress products...")
    with open(products_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    filtered_results = {}
    total_removed = 0
    
    # Process each category
    for category_name, category_data in data['results'].items():
        print(f"\n--- Processing category: {category_name} ---")
        filtered_products = []
        category_removed = 0
        
        for product in category_data['products']:
            title = product['title']
            
            try:
                # Get embedding for product title
                title_embedding = get_embedding(title)
                
                # Find best matching restricted keyword
                match_result = find_best_restricted_match(title_embedding, restricted_embeddings)
                max_similarity = match_result["max_similarity"]
                
                if max_similarity > threshold:
                    print(f"❌ REMOVED (sim={max_similarity:.3f}): {title[:80]}...")
                    category_removed += 1
                    total_removed += 1
                else:
                    filtered_products.append(product)
                    if max_similarity > 0.6:
                        print(f"⚠️  KEPT (sim={max_similarity:.3f}): {title[:80]}...")
                        
            except Exception as e:
                print(f"⚠️  ERROR processing '{title[:50]}...': {e}")
                filtered_products.append(product)  # Keep on error
        
        filtered_results[category_name] = {
            "keyword": category_data["keyword"],
            "products": filtered_products,
            "stats": {
                "original_count": len(category_data["products"]),
                "filtered_count": len(filtered_products),
                "removed_count": category_removed
            }
        }
        
        print(f"  {len(category_data['products'])} → {len(filtered_products)} products "
              f"(-{category_removed} removed)")
    
    # Create output structure
    output_data = {
        "metadata": {
            "scraped_at": data["scraped_at"],
            "categories_searched": data["categories_searched"],
            "pages_per_category": data["pages_per_category"],
            "similarity_threshold": threshold,
            "total_original": data["total_products"],
            "total_filtered": sum(cat["stats"]["filtered_count"] for cat in filtered_results.values()),
            "total_removed": total_removed,
            "restricted_keywords_count": len(restricted_keywords)
        },
        "results": filtered_results,
        "note": f"Filtered products with >{threshold} similarity to restricted keywords"
    }
    
    # Save filtered results
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print("\n" + "="*60)
    print("🎉 FILTERING COMPLETE")
    print(f"📊 Original: {data['total_products']:,}")
    print(f"✅ Filtered: {output_data['metadata']['total_filtered']:,}")
    print(f"❌ Removed: {output_data['metadata']['total_removed']:,}")
    print(f"📁 Saved to: {output_file}")
    print("="*60)
    
    return output_data

# MAIN EXECUTION
if __name__ == "__main__":
    # File paths
    PRODUCTS_FILE = "aliexpress_products.json"
    EMBEDDINGS_FILE = "restricted_keywords_embeddings.pkl"
    OUTPUT_FILE = "aliexpress_products_filtered.json"
    
    # Run filtering
    result = filter_products_by_restricted_keywords(
        products_file=PRODUCTS_FILE,
        embeddings_file=EMBEDDINGS_FILE,
        output_file=OUTPUT_FILE,
        threshold=0.8  # Adjust as needed (0.8 = strict)
    )
