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


def extract_aliexpress_product(url: str, max_retries: int = 2) -> dict:
    print("Opening browser...")
    base_url = url.split("#")[0]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.set_extra_http_headers({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        })

        # ---------- FAST NAVIGATION ----------
        for attempt in range(max_retries):
            try:
                page.goto(
                    base_url,
                    timeout=45000,
                    wait_until="domcontentloaded"
                )
                break
            except Exception as e:
                print(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    browser.close()
                    return {"title": "", "description_text": ""}
                page.wait_for_timeout(2000)

        # ---------- LIGHT SCROLL ----------
        for _ in range(4):
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(200)

        # ---------- TITLE ----------
        title = ""
        title_selectors = [
            "h1[data-pl='product-title']",
            ".product-title",
            "[data-pl*='title']",
            "h1",
        ]

        for sel in title_selectors:
            el = page.query_selector(sel)
            if el:
                text = el.text_content().strip()
                if text:
                    title = text
                    print(f"Title found via {sel}")
                    break

        if not title:
            print("Title not found")

        # ---------- DESCRIPTION TAB ----------
        try:
            tab = page.query_selector('a:has-text("Description")') \
                  or page.query_selector('a:has-text("description")')
            if tab:
                tab.click()
                page.wait_for_timeout(1500)
        except Exception:
            pass

        # ---------- DESCRIPTION ----------
        description_html = ""
        desc_selectors = [
            "#product-description",
            ".product-description",
            "[data-pl*='description']",
            ".description",
        ]

        for sel in desc_selectors:
            el = page.query_selector(sel)
            if el:
                html = el.inner_html()
                if html and len(html) > 150:
                    description_html = html
                    print(f"Description found via {sel}")
                    break

        soup = BeautifulSoup(description_html, "html.parser")
        description_text = soup.get_text(" ", strip=True)

        # ---------- IMAGES (FAST HTML PARSE) ----------
        images = []
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src and "alicdn" in src:
                images.append(normalize_img_url(src))

        images = list(dict.fromkeys(images))

        if not title or not description_text:
            page.screenshot(path="debug.png")
            print("Saved debug.png")

        browser.close()

        print(f"Title length: {len(title)}")
        print(f"Description length: {len(description_text)}")
        print(f"Images: {len(images)}")

        return {
            "title": clean_text(title),
            "description_text": clean_text(description_text),
        }

