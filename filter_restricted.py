import json
import pandas as pd
import numpy as np
import pickle
from openai import OpenAI
import os
from sklearn.metrics.pairwise import cosine_similarity
import warnings
warnings.filterwarnings('ignore')

# Initialize OpenAI client (set your API key)
client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

def get_embedding(text, model="text-embedding-3-large"):
    """Get embedding for a single text using OpenAI API"""
    try:
        response = client.embeddings.create(
            input=text.strip(),
            model=model
        )
        return np.array(response.data[0].embedding)
    except Exception as e:
        print(f"Error getting embedding for '{text[:50]}...': {e}")
        return None

def load_restricted_keywords(embeddings_file):
    """Load restricted keywords and their embeddings"""
    df = pd.read_pickle(embeddings_file)
    keywords = df['keyword'].tolist()  # Adjust column name if different
    embeddings = np.array(df['embedding'].tolist())  # Adjust column name if different
    return keywords, embeddings

def filter_products_by_similarity(products_json, embeddings_pkl, output_file, threshold=0.8):
    """
    Filter products by checking cosine similarity of titles against restricted keywords
    """
    # Load restricted keywords and embeddings
    print("Loading restricted keywords...")
    restricted_keywords, restricted_embeddings = load_restricted_keywords(embeddings_pkl)
    print(f"Loaded {len(restricted_keywords)} restricted keywords")
    
    # Load AliExpress products
    print("Loading AliExpress products...")
    with open(products_json, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    filtered_results = {}
    removed_count = 0
    
    # Process each category
    for category, category_data in data['results'].items():
        print(f"\nProcessing category: {category}")
        filtered_products = []
        
        for product in category_data['products']:
            title = product['title']
            
            # Get embedding for product title
            title_embedding = get_embedding(title)
            
            if title_embedding is None:
                # If embedding fails, keep the product (conservative approach)
                filtered_products.append(product)
                continue
            
            # Calculate cosine similarities to all restricted keywords
            similarities = cosine_similarity([title_embedding], restricted_embeddings)[0]
            max_similarity = np.max(similarities)
            
            if max_similarity > threshold:
                print(f"REMOVED (sim={max_similarity:.3f}): {title[:100]}...")
                removed_count += 1
            else:
                filtered_products.append(product)
                if max_similarity > 0.6:  # Log borderline cases
                    print(f"Kept (sim={max_similarity:.3f}): {title[:100]}...")
        
        filtered_results[category] = {
            "keyword": category_data["keyword"],
            "products": filtered_products,
            "original_count": len(category_data["products"]),
            "filtered_count": len(filtered_products),
            "removed_count": len(category_data["products"]) - len(filtered_products)
        }
        
        print(f"Category '{category}': {len(category_data['products'])} -> {len(filtered_products)} products")
    
    # Create output data structure
    output_data = {
        "scraped_at": data["scraped_at"],
        "categories_searched": data["categories_searched"],
        "pages_per_category": data["pages_per_category"],
        "total_products_original": data["total_products"],
        "total_products_filtered": sum(cat["filtered_count"] for cat in filtered_results.values()),
        "total_removed": data["total_products"] - sum(cat["filtered_count"] for cat in filtered_results.values()),
        "similarity_threshold": threshold,
        "note": f"Filtered out products with >{threshold} cosine similarity to restricted keywords",
        "results": filtered_results
    }
    
    # Save filtered results
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n=== FILTERING SUMMARY ===")
    print(f"Original products: {data['total_products']}")
    print(f"Filtered products: {output_data['total_products_filtered']}")
    print(f"Removed products: {output_data['total_removed']} (threshold: {threshold})")
    print(f"Results saved to: {output_file}")
    
    return output_data

# Usage
if __name__ == "__main__":
    # File paths
    EMBEDDINGS_FILE = "restricted_keywords_embeddings.pkl"
    PRODUCTS_FILE = "aliexpress_products.json"
    OUTPUT_FILE = "aliexpress_products_filtered.json"
    
    # Run filtering
    filtered_data = filter_products_by_similarity(
        products_json=PRODUCTS_FILE,
        embeddings_pkl=EMBEDDINGS_FILE,
        output_file=OUTPUT_FILE,
        threshold=0.8  # Similarity threshold
    )
