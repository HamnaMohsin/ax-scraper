from openai import OpenAI
import json
import re  # For regex-based JSON extraction as fallback

client = OpenAI()  # automatically reads OPENAI_API_KEY

def refine_with_llm(title: str, description: str) -> dict:
    print("Sending data to LLM...")

    prompt = f"""
You are an expert e-commerce product content optimizer.

You are given raw product data extracted from an AliExpress product page.

Original Title:
{title}

Original Description:
{description}

Your task:
- Improve the product title for clarity and SEO
- Rewrite the description to be clear, structured, and persuasive, including concise bullet points highlighting key benefits as part of the description text (e.g., "Key benefits:\n- Bullet 1\n- Bullet 2")
-if description is null, return empty description
Rules:
- Do NOT hallucinate features not implied by the input
- Do NOT include explanations or extra text
- Return ONLY valid JSON

Return JSON in this EXACT format:
{{
  "refined_title": "Improved product title",
  "refined_description": "Improved product description with embedded bullet points"
}}
"""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],temperature=0
        )
        
            
        content = response.choices[0].message.content.strip()
        
        return json.loads(content)

    except json.JSONDecodeError:
        # Fallback: extract JSON safely
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        print("LLM response invalid; falling back to original data.")
        return {
            "refined_title": title,
            "refined_description": description

        }

