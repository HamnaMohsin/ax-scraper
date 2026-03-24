import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def random_delay(min_seconds: float = 1, max_seconds: float = 3):
    time.sleep(random.uniform(min_seconds, max_seconds))


def random_viewport():
    return random.choice([
        {'width': 1366, 'height': 768},
        {'width': 1920, 'height': 1080},
        {'width': 1440, 'height': 900},
        {'width': 1280, 'height': 720},
    ])


def rotate_tor_circuit():
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print("   Waiting 15s for new Tor circuit...")
            for i in range(15):
                time.sleep(1)
                if i % 5 == 4:
                    print(f"   ... {15 - i - 1}s remaining")
        print("✅ Tor circuit rotated")
        return True
    except Exception as e:
        print(f"⚠️ Tor error: {e}")
        return False


def is_captcha_page(page) -> bool:
    page_url = page.url.lower()
    page_title = page.title().lower()

    if any(kw in page_url for kw in ["baxia", "captcha", "verify"]):
        return True

    for selector in [
        "iframe[src*='recaptcha']",
        "[id*='captcha']",
        "[class*='captcha']",
    ]:
        try:
            if page.locator(selector).count() > 0:
                return True
        except:
            pass

    return False


def extract_title_universal(page) -> str:
    for selector in [
        '[data-pl="product-title"]',
        'h1',
        '[class*="product-title"]',
    ]:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if len(title) > 10:
                    return title
        except:
            continue
    return ""


def extract_store_info_universal(page) -> dict:
    store_info = {}
    try:
        soup = BeautifulSoup(page.content(), "html.parser")
        table = soup.find("table")
        if table:
            for row in table.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) >= 2:
                    key = clean_text(cols[0].text).replace(":", "")
                    val = clean_text(cols[1].text)
                    if key and val:
                        store_info[key] = val
    except:
        pass
    return store_info


def extract_aliexpress_product(url: str) -> dict:
    print(f"\n🔍 Scraping: {url}")

    for attempt in range(3):
        print(f"\n📍 Attempt {attempt + 1}")

        if attempt > 0:
            rotate_tor_circuit()
            time.sleep(20)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"}
            )

            page = browser.new_page(
                viewport=random_viewport(),
                user_agent=random.choice([
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                    'Mozilla/5.0 (Macintosh)',
                ])
            )

            try:
                page.goto(url, timeout=120000)
                time.sleep(5)

                if is_captcha_page(page):
                    browser.close()
                    continue

                title = extract_title_universal(page)
                store_info = extract_store_info_universal(page)

                print("📝 Extracting description...")

                description_text = ""
                description_images = []

                try:
                    desc_container = page.locator('#product-description').first

                    if desc_container.count() > 0:
                        print("   ✓ Found description container")

                        # ✅ FIXED TEXT EXTRACTION
                        print("   🎯 Extracting ALL text recursively...")

                        try:
                            description_text = page.evaluate("""
                            () => {
                                const container = document.querySelector('#product-description');
                                if (!container) return "";

                                function getAllText(node) {
                                    let text = "";

                                    for (let child of node.childNodes) {
                                        if (child.nodeType === Node.TEXT_NODE) {
                                            let t = child.textContent.trim();
                                            if (t) text += t + " ";
                                        } else if (child.nodeType === Node.ELEMENT_NODE) {
                                            const tag = child.tagName.toLowerCase();
                                            if (["script", "style", "noscript"].includes(tag)) continue;
                                            text += getAllText(child);
                                        }
                                    }
                                    return text;
                                }

                                return getAllText(container);
                            }
                            """)

                            description_text = re.sub(r'\s+', ' ', description_text).strip()
                            print(f"   ✅ Text length: {len(description_text)}")

                        except Exception as e:
                            print(f"   ❌ Text extraction error: {e}")

                        # IMAGES (unchanged)
                        imgs = desc_container.locator('img').all()

                        for img in imgs:
                            src = img.get_attribute("src") or img.get_attribute("data-src")
                            if src and "alicdn.com" in src:
                                description_images.append(src.split("?")[0])

                        description_images = list(set(description_images))[:20]

                except Exception as e:
                    print(f"⚠️ Description error: {e}")

                browser.close()

                return {
                    "title": title,
                    "description_text": description_text,
                    "images": description_images,
                    "store_info": store_info
                }

            except Exception as e:
                print(f"❌ Error: {e}")
                browser.close()

    return {
        "title": "",
        "description_text": "",
        "images": [],
        "store_info": {}
    }
