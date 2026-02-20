import re
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

def normalize_img_url(src: str) -> str:
    if not src:
        return ""
    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://www.aliexpress.com" + src
    return src

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def extract_aliexpress_product(url: str, max_retries: int = 3) -> dict:
    print("Opening browser...")
    base_url = url.split('#')[0]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        })

        # Navigate with retries
        for attempt in range(max_retries):
            try:
                page.goto(base_url, timeout=120, wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle", timeout=600)
                break
            except Exception as e:
                print(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    browser.close()
                    return {"title": "", "description_text": "", "images": []}
                page.wait_for_timeout(500)

        # Scroll to load dynamic content
        for _ in range(10):
            page.mouse.wheel(0, 20)
            page.wait_for_timeout(30)

        # Try to click the "Description" tab to load content
        try:
            desc_tab = page.query_selector('a:has-text("Description")') or page.query_selector('a:has-text("description")')
            if desc_tab:
                print("Clicking Description tab...")
                desc_tab.click()
                page.wait_for_timeout(500)  # Increased wait for content to load
                page.wait_for_load_state("networkidle", timeout=1500)
                # Additional scrolling in the description area
                desc_container = page.query_selector("#product-description")
                if desc_container:
                    desc_container.scroll_into_view_if_needed()
                    for _ in range(5):  # Scroll within the container
                        page.mouse.wheel(0, 300)
                        page.wait_for_timeout(500)
        except Exception as e:
            print(f"Could not click Description tab or scroll: {e}")

        # --- Safe extraction helpers ---
        def safe_query_text(selector: str) -> str:
            el = page.query_selector(selector)
            return el.text_content().strip() if el else ""

        # Extract title (unchanged)
        title = safe_query_text(
            "#root > div > div.pdp-body.pdp-wrap > div > div.pdp-body-top-left > div.pdp-info > div.pdp-info-right > div.title--wrap--UUHae_g > h1"
        )
        print(f"Title found: {title[:50]}...") if title else print("Title not found")

        # Extract full description text and images
                # Extract full description text and images
        description_text = ""
        images = []
        try:
            container = page.query_selector("#product-description")
            if container:
                print("Found description container, checking inner HTML...")
                html = container.inner_html()
                print(f"Inner HTML snippet: {html[:200]}...")  # Debug: Print first 200 chars of HTML
                
                # Targeted extraction for text (based on your selectors)
                text_elements = container.query_selector_all("p.detail-desc-decorate-content")
                for el in text_elements:
                    text = el.text_content().strip()
                    if text:
                        description_text += text + " "
                
                # If no text found, try broader p tags inside the container
                if not description_text:
                    all_p = container.query_selector_all("p")
                    for el in all_p:
                        text = el.text_content().strip()
                        if text:
                            description_text += text + " "
                
                # Targeted extraction for images
                img_elements = container.query_selector_all("img")
                print(f"Found {len(img_elements)} img elements in container before filtering.")  # Debug: Count img elements
                for img in img_elements:
                    src = img.get_attribute("src") or img.get_attribute("data-src")
                    if src:
                        src = normalize_img_url(src)
                        if "alicdn" in src:  # Filter to AliExpress images
                            images.append(src)
                
                # Specific check for your provided selector (e.g., 25th child div's img)
                specific_img = container.query_selector("div:nth-child(25) > img")
                if specific_img:
                    src = specific_img.get_attribute("src") or specific_img.get_attribute("data-src")
                    if src:
                        src = normalize_img_url(src)
                        if src not in images:  # Avoid duplicates
                            images.append(src)
                            print("Added image from specific selector: div:nth-child(25) > img")
                
                # Deduplicate images
                images = list(dict.fromkeys(images))
                print(f"Extracted {len(images)} images and description text (length: {len(description_text)})")
            else:
                print("Description container not found.")
        except Exception as e:
            print(f"Error extracting description and images: {e}")
        

        browser.close()

        return {
            "title": clean_text(title),
            "description_text": clean_text(description_text),
            "images": images
        }
