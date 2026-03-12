from openai import OpenAI
import json
import re

client = OpenAI()

# Octopia template hard limits (from Row 8 of the template)
TITLE_MAX_CHARS = 132
DESCRIPTION_MAX_CHARS = 2000
MARKETING_DESC_MAX_CHARS = 5000


def refine_with_llm(title: str, description: str) -> dict:
    print("Sending data to LLM...")

    prompt = f"""
You are an expert e-commerce product content optimizer writing for the Octopia marketplace (Cdiscount, Carrefour).

You are given raw product data extracted from an AliExpress product page.

Original Title:
{title}

Original Description:
{description}

Your task:
- Improve the product title for clarity and SEO
- Rewrite the description to be clear, structured, and persuasive, including concise bullet points highlighting key benefits as part of the description text (e.g., "Key benefits:\\n- Bullet 1\\n- Bullet 2")
- If description is empty or null, return empty string for refined_description

Also return a 'description_marketing' field: an HTML-formatted marketing version
of the description using only these tags: <p>, <b>, <ul>, <li>, <h3>, <img>.
Maximum {MARKETING_DESC_MAX_CHARS} characters. Must be valid HTML. No inline styles.

STRICT CHARACTER LIMITS — you must respect these exactly:
- refined_title: MAXIMUM {TITLE_MAX_CHARS} characters (including spaces)
- refined_description: MAXIMUM {DESCRIPTION_MAX_CHARS} characters (including spaces)

Rules:
- Do NOT hallucinate features not implied by the input
- Do NOT include explanations or extra text
- Return ONLY valid JSON

Return JSON in this EXACT format:
{{
  "refined_title": "Improved product title under {TITLE_MAX_CHARS} characters",
  "refined_description": "Improved product description under {DESCRIPTION_MAX_CHARS} characters",
  "description_marketing": "<p>HTML marketing description...</p>"
}}
"""

    content = ""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        content = response.choices[0].message.content.strip()
        result = json.loads(content)

    except json.JSONDecodeError:

        json_match = re.search(r"\{.*\}", content, re.DOTALL)

        if json_match:
            try:
                result = json.loads(json_match.group(0))
            except json.JSONDecodeError:
                result = None
        else:
            result = None

        if result is None:
            print("LLM response invalid — falling back to original data.")

            return {
                "refined_title": title,
                "refined_description": description,
                "description_marketing": ""
            }

    # ── Post-LLM validation ─────────────────────────────────────────────

    refined_title = result.get("refined_title", title) or title
    refined_description = result.get("refined_description", description) or ""
    description_marketing = result.get("description_marketing", "") or ""

    if description == "":
        refined_description = ""

    if len(refined_title) > TITLE_MAX_CHARS:
        print(f"WARNING: LLM title exceeded {TITLE_MAX_CHARS} chars ({len(refined_title)}) — truncating.")
        refined_title = refined_title[:TITLE_MAX_CHARS].rstrip()

    if len(refined_description) > DESCRIPTION_MAX_CHARS:
        print(f"WARNING: LLM description exceeded {DESCRIPTION_MAX_CHARS} chars ({len(refined_description)}) — truncating.")
        refined_description = refined_description[:DESCRIPTION_MAX_CHARS].rstrip()

    if len(description_marketing) > MARKETING_DESC_MAX_CHARS:
        print(f"WARNING: LLM marketing description exceeded {MARKETING_DESC_MAX_CHARS} chars — truncating.")
        description_marketing = description_marketing[:MARKETING_DESC_MAX_CHARS].rstrip()

    return {
        "refined_title": refined_title,
        "refined_description": refined_description,
        "description_marketing": description_marketing
    }
