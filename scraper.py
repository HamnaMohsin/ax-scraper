import re
import time
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def human_scroll(page):
    """Scroll slowly to trigger lazy loading"""
    print("⏳ Human-like scrolling...")
    for _ in range(10):
        page.evaluate("window.scrollBy(0, 500)")
        time.sleep(1)


def extract_aliexpress_product(url: str) -> dict:
    print(f"🔍 Scraping: {url}")

    empty_result = {
        "title": "",
        "description_text": "",
        "images": [],
        "store_name": ""
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--start-maximized",
                "--disable-dev-shm-usage",
            ]
        )
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768}
        )
        page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        })
        """)

        try:
            page.goto(url, timeout=90000, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            time.sleep(2)

            # -----------------------
            # TITLE
            # -----------------------
            page.wait_for_selector('[data-pl="product-title"]', timeout=10000)
            title = page.locator('[data-pl="product-title"]').inner_text().strip()
            print(f"✅ Title: {title[:80]}")

            # -----------------------
            # STORE NAME
            # -----------------------
            print("🏪 Extracting store...")
            store_name = ""
            try:
                store_elem = page.locator('[class*="storeName"]').first
                if store_elem:
                    store_name = store_elem.inner_text().strip()
                    print(f"✅ Store: {store_name}")
            except:
                print("⚠️ Store not found")

            # -----------------------
            # SCROLL (LAZY LOAD FIX)
            # -----------------------
            human_scroll(page)

            # -----------------------
            # DESCRIPTION (ROBUST FIX ✅)
            # -----------------------
            print("📝 Extracting description...")
            description_text = ""
            description_images = []

            try:
                # Wait for ANY description block (not strict)
                page.wait_for_selector("#product-description", timeout=15000)

                locators = page.locator("#product-description")
                count = locators.count()
                print(f"🔍 Found {count} description blocks")

                for i in range(count):
                    try:
                        block = locators.nth(i)
                        html = block.inner_html()

                        soup = BeautifulSoup(html, "html.parser")

                        # Remove junk
                        for tag in soup(["script", "style"]):
                            tag.decompose()

                        text = soup.get_text(" ", strip=True)

                        # ✅ Heuristic: real description is long
                        if len(text) > 200:
                            print(f"✅ Using block {i}")

                            description_text = text

                            # Extract CLEAN images
                            for img in soup.find_all("img"):
                                src = img.get("src")

                                if (
                                    src
                                    and "alicdn" in src
                                    and not any(x in src for x in [
                                        "50x50", "27x27", "icon", "logo"
                                    ])
                                ):
                                    description_images.append(src)

                            break

                    except Exception:
                        continue

            except Exception as e:
                print(f"❌ Description failed: {e}")

            # -----------------------
            # IFRAME FALLBACK (IMPORTANT)
            # -----------------------
            if not description_text:
                print("🔁 Trying iframe fallback...")

                for frame in page.frames:
                    try:
                        html = frame.content()
                        soup = BeautifulSoup(html, "html.parser")
                        text = soup.get_text(" ", strip=True)

                        if len(text) > 300:
                            print("✅ Description from iframe")
                            description_text = text

                            for img in soup.find_all("img"):
                                src = img.get("src")
                                if src and "alicdn" in src:
                                    description_images.append(src)

                            break
                    except:
                        continue

            browser.close()

            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": list(set(description_images))[:20],
                "store_name": clean_text(store_name),
            }

        except Exception as e:
            print(f"❌ Error: {e}")
            browser.close()
            return empty_result
