from openai import OpenAI
import pickle
import numpy as np
import os

client = OpenAI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Now uses octopia categories instead of google product categories
embeddings_path = os.path.join(BASE_DIR, "octopia_categories_embeddings.pkl")


def assign_category_text(title: str, description: str) -> str:
    prompt = f"""
You are a product categorization expert.

Given the product title and description, return the most appropriate
Octopia category path as a single text string.

The category paths follow the format: PARENT/CHILD/SUBCATEGORY
Example: ELECTRONICS/CAMERAS/SECURITY CAMERAS

Title:
{title}

Description:
{description}

Return ONLY the category path text.
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You assign Octopia product categories."},
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )
    return response.choices[0].message.content.strip()


def get_embedding(text: str):
    response = client.embeddings.create(
        model="text-embedding-3-large",
        input=text
    )
    return response.data[0].embedding


def load_category_embeddings() -> list:
    with open(embeddings_path, "rb") as f:
        return pickle.load(f)


def cosine_similarity(vec1, vec2):
    v1 = np.array(vec1)
    v2 = np.array(vec2)
    return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))


def find_best_category(query_embedding, stored_data) -> dict:
    """Find best matching entry in the octopia embeddings (list of dicts)."""
    best_score = -1
    best_match = None

    for item in stored_data:
        score = cosine_similarity(query_embedding, item["embedding"])
        if score > best_score:
            best_score = score
            best_match = item

    return {
        "category_id":      best_match["code"],
        "category_path":    best_match["category_text"],
        "similarity_score": best_score
    }


def categorize_product(title: str, description: str) -> dict:
    print("Categorizing product...")

    # Step 1: LLM predicts category text
    predicted_category_text = assign_category_text(title, description)

    # Step 2: Embed the prediction
    query_embedding = get_embedding(predicted_category_text)

    # Step 3: Load octopia embeddings DataFrame
    df = load_category_embeddings()

    # Step 4: Cosine similarity search
    result = find_best_category(query_embedding, df)

    return {
        "llm_predicted_category": predicted_category_text,
        "matched_category_id":    result["category_id"],
        "matched_category_path":  result["category_path"],
        "similarity_score":       result["similarity_score"]
    }
