from openai import OpenAI
import json
import re

client = OpenAI()

# Octopia template hard limits (from Row 8 of the template)
TITLE_MAX_CHARS = 132
DESCRIPTION_MAX_CHARS = 2000


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

STRICT CHARACTER LIMITS — you must respect these exactly:
- refined_title: MAXIMUM {TITLE_MAX_CHARS} characters (including spaces). Count carefully.
- refined_description: MAXIMUM {DESCRIPTION_MAX_CHARS} characters (including spaces). Count carefully.

Rules:
- Do NOT hallucinate features not implied by the input
- Do NOT include explanations or extra text
- Do NOT use HTML tags
- Return ONLY valid JSON

Return JSON in this EXACT format:
{{
  "refined_title": "Improved product title under {TITLE_MAX_CHARS} characters",
  "refined_description": "Improved product description under {DESCRIPTION_MAX_CHARS} characters"
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
            result = {
                "refined_title": title,
                "refined_description": description
            }

    # ── Post-LLM validation ───────────────────────────────────────────────────
    # Even if the LLM ignores the limit, we hard-truncate here as a safety net.

    refined_title = result.get("refined_title", title) or title
    refined_description = result.get("refined_description", description) or ""
    if description < 1:
        refined_description= ""
    if len(refined_title) > TITLE_MAX_CHARS:
        print(f"WARNING: LLM title exceeded {TITLE_MAX_CHARS} chars "
              f"({len(refined_title)}) — truncating.")
        refined_title = refined_title[:TITLE_MAX_CHARS].rstrip()

    if len(refined_description) > DESCRIPTION_MAX_CHARS:
        print(f"WARNING: LLM description exceeded {DESCRIPTION_MAX_CHARS} chars "
              f"({len(refined_description)}) — truncating.")
        refined_description = refined_description[:DESCRIPTION_MAX_CHARS].rstrip()

    return {
        "refined_title":       refined_title,
        "refined_description": refined_description,

    }
