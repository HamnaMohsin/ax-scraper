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
    base_url = url.split('#')[0].strip()
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy={"server": "socks5://127.0.0.1:9050"}
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        page = context.new_page()

        for attempt in range(max_retries):
            try:
                page.goto(base_url, timeout=120000, wait_until="domcontentloaded")
                page.wait_for_timeout(8000)
                break
            except Exception as e:
                print(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    browser.close()
                    return {"title": "", "description_text": "", "images": []}
                page.wait_for_timeout(5000)

        # Wait for JS to render
        page.wait_for_timeout(10000)

        # Scroll to trigger lazy loading
        for _ in range(10):
            page.mouse.wheel(0, 200)
            page.wait_for_timeout(300)

        # Try to click Description tab
        try:
            desc_tab = page.query_selector('a:has-text("Description")') or \
                       page.query_selector('a:has-text("description")')
            if desc_tab:
                print("Clicking Description tab...")
                desc_tab.click()
                page.wait_for_timeout(5000)
                
                page.wait_for_timeout(5000)
                desc_container = page.query_selector("#product-description")
                if desc_container:
                    desc_container.scroll_into_view_if_needed()
                    for _ in range(5):
                        page.mouse.wheel(0, 300)
                        page.wait_for_timeout(500)
        except Exception as e:
            print(f"Could not click Description tab: {e}")

        def safe_query_text(selector: str) -> str:
            el = page.query_selector(selector)
            return el.text_content().strip() if el else ""

        # Try multiple title selectors
        title = ""
        title_selectors = [
            "h1",
            "[data-pl='product-title']",
            ".product-title",
            ".title--wrap--UUHae_g h1",
            "#root h1",
        ]
        for sel in title_selectors:
            title = safe_query_text(sel)
            if title:
                print(f"Title found with selector '{sel}': {title[:50]}")
                break
        if not title:
            print("Title not found")

        description_text = ""
        images = []
        try:
            container = page.query_selector("#product-description")
            if container:
                print("Found description container...")
                text_elements = container.query_selector_all("p.detail-desc-decorate-content")
                for el in text_elements:
                    text = el.text_content().strip()
                    if text:
                        description_text += text + " "

                if not description_text:
                    all_p = container.query_selector_all("p")
                    for el in all_p:
                        text = el.text_content().strip()
                        if text:
                            description_text += text + " "

                img_elements = container.query_selector_all("img")
                for img in img_elements:
                    src = img.get_attribute("src") or img.get_attribute("data-src")
                    if src:
                        src = normalize_img_url(src)
                        if "alicdn" in src:
                            images.append(src)

                images = list(dict.fromkeys(images))
                print(f"Extracted {len(images)} images, description length: {len(description_text)}")
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
