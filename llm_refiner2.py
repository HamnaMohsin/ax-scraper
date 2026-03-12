import os
import json
import openai

client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

TITLE_MAX        = 132
DESCRIPTION_MAX  = 2000
MARKETING_MAX    = 5000

SYSTEM_PROMPT = """You are a product copywriter for a French marketplace (Octopia/Cdiscount).
Given a product title and description scraped from AliExpress, return a JSON object with exactly these three keys:

1. "refined_title"
   - Clean, accurate product title
   - Maximum 132 characters
   - English, no promotional filler ("Best!", "Hot sale!", etc.)
   - Keep brand names, model numbers, key specs

2. "refined_description"
   - Informative product description in English
   - Maximum 2000 characters
   - Plain text only — no HTML tags
   - Focus on features, specs, compatibility, use cases

3. "description_marketing"
   - HTML-formatted marketing version of the description
   - Maximum 5000 characters
   - Use ONLY these tags: <p>, <b>, <ul>, <li>, <h3>, <img>
   - No inline styles, no classes, no other tags
   - Structure it clearly: short intro paragraph, feature list, closing paragraph
   - Do NOT include <html>, <head>, <body>, or <div> tags

Return ONLY the JSON object — no preamble, no markdown fences, no extra text."""


def refine_product(title: str, description: str) -> dict:
    """
    Call GPT-4o-mini to refine title/description and generate marketing HTML.

    Returns:
        {
            "refined_title":         str,   # max 132 chars
            "refined_description":   str,   # max 2000 chars, plain text
            "description_marketing": str,   # max 5000 chars, HTML
        }
    """
    user_content = f"Title: {title}\n\nDescription: {description}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            temperature=0.3,
            max_tokens=1500,
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data = json.loads(raw)

        refined_title        = str(data.get("refined_title",        title))[:TITLE_MAX]
        refined_description  = str(data.get("refined_description",  description))[:DESCRIPTION_MAX]
        description_marketing = str(data.get("description_marketing", ""))[:MARKETING_MAX]

        return {
            "refined_title":         refined_title,
            "refined_description":   refined_description,
            "description_marketing": description_marketing,
        }

    except Exception as e:
        print(f"LLM refinement failed: {e}")
        return {
            "refined_title":         title[:TITLE_MAX],
            "refined_description":   description[:DESCRIPTION_MAX],
            "description_marketing": "",
        }
