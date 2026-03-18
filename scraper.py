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
    print("⏳ Human-like scrolling...")
    for _ in range(10):
        page.evaluate("window.scrollBy(0, 500)")
        time.sleep(1)


def extract_aliexpress_product(url: str, retries=2) -> dict:
    print(f"🔍 Scraping: {url}")

    empty_result = {
        "title": "",
        "description_text": "",
        "images": [],
        "store_name": ""
    }

    for attempt in range(retries + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars",
                        "--start-maximized"
                    ]
                )

                page = browser.new_page(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768},
                    locale="en-US"
                )

                # Anti-bot
                page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
                """)

                # -----------------------
                # LOAD PAGE
                # -----------------------
                page.goto(url, timeout=90000, wait_until="networkidle")
                page.wait_for_load_state("networkidle")

                # Human behavior (IMPORTANT)
                time.sleep(5)
                page.mouse.move(100, 200)
                page.mouse.wheel(0, 500)

                # -----------------------
                # BLOCK DETECTION
                # -----------------------
                content = page.content().lower()
                if "captcha" in content or "verify" in content:
                    print("🚫 BLOCKED by AliExpress")
                    browser.close()
                    time.sleep(5)
                    continue  # retry

                # -----------------------
                # TITLE (FIXED ✅)
                # -----------------------
                print("⏳ Extracting title...")
                title = ""

                title_selectors = [
                    '[data-pl="product-title"]',
                    'h1',
                    '[class*="title"]'
                ]

                for selector in title_selectors:
                    try:
                        page.wait_for_selector(selector, timeout=7000)
                        title = page.locator(selector).first.inner_text().strip()

                        if len(title) > 10:
                            print(f"✅ Title found using: {selector}")
                            break
                    except:
                        continue

                if not title:
                    print("❌ Title not found (retrying...)")
                    browser.close()
                    time.sleep(3)
                    continue

                # -----------------------
                # STORE
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
                # SCROLL
                # -----------------------
                human_scroll(page)

                # -----------------------
                # DESCRIPTION
                # -----------------------
                print("📝 Extracting description...")
                description_text = ""
                description_images = []

                try:
                    page.wait_for_selector("#product-description", timeout=20000)

                    locators = page.locator("#product-description")
                    count = locators.count()
                    print(f"🔍 Found {count} description blocks")

                    for i in range(count):
                        try:
                            html = locators.nth(i).inner_html()
                            soup = BeautifulSoup(html, "html.parser")

                            for tag in soup(["script", "style"]):
                                tag.decompose()

                            text = soup.get_text(" ", strip=True)

                            if len(text) > 200:
                                print(f"✅ Using block {i}")
                                description_text = text

                                for img in soup.find_all("img"):
                                    src = img.get("src")
                                    if (
                                        src
                                        and "alicdn" in src
                                        and not any(x in src for x in ["50x50", "icon", "logo"])
                                    ):
                                        description_images.append(src)

                                break
                        except:
                            continue

                except Exception as e:
                    print(f"❌ Description failed: {e}")

                # -----------------------
                # IFRAME FALLBACK
                # -----------------------
                if not description_text:
                    print("🔁 Trying iframe fallback...")
                    for frame in page.frames:
                        try:
                            html = frame.content()
                            soup = BeautifulSoup(html, "html.parser")
                            text = soup.get_text(" ", strip=True)

                            if len(text) > 300:
                                description_text = text
                                print("✅ Description from iframe")

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
            print(f"❌ Attempt {attempt+1} failed: {e}")
            time.sleep(3)

    return empty_result
