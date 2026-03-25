import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller


# ---------------------- UTILITIES ----------------------

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def random_delay(min_seconds=1, max_seconds=3):
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
            time.sleep(15)
        print("✅ Tor circuit rotated")
        return True
    except Exception as e:
        print(f"⚠️ Tor rotation failed: {e}")
        return False


# ---------------------- CAPTCHA DETECTION ----------------------

def is_captcha_page(page) -> bool:
    url = page.url.lower()
    title = page.title().lower()

    if any(x in url for x in ["captcha", "verify", "punish", "baxia"]):
        return True

    if any(x in title for x in ["verify", "blocked", "denied"]):
        return True

    selectors = [
        "iframe[src*='captcha']",
        "[class*='captcha']"
    ]

    for sel in selectors:
        try:
            if page.locator(sel).count() > 0:
                return True
        except:
            pass

    return False


# ---------------------- SHADOW DOM EXTRACTION ----------------------

def extract_description_shadow_dom(page):
    print("   🌐 Extracting Shadow DOM...")

    try:
        data = page.evaluate("""
        () => {
            const result = { texts: [], images: [] };

            const root = document.querySelector('#product-description > div');
            if (!root || !root.shadowRoot) return result;

            const walker = document.createTreeWalker(
                root.shadowRoot,
                NodeFilter.SHOW_ELEMENT,
                null,
                false
            );

            let node;
            while (node = walker.nextNode()) {

                if (node.innerText) {
                    result.texts.push(node.innerText);
                }

                if (node.tagName === 'IMG') {
                    let src = node.src || node.getAttribute('data-src') || node.getAttribute('data-lazy-src');
                    if (src) result.images.push(src);
                }
            }

            return result;
        }
        """)

        text = " ".join(data.get("texts", []))
        images = data.get("images", [])

        print(f"   ✅ Shadow text: {len(text)} chars")
        print(f"   🖼️ Shadow images: {len(images)}")

        return text, images

    except Exception as e:
        print(f"   ❌ Shadow DOM failed: {e}")
        return "", []


# ---------------------- TITLE ----------------------

def extract_title_universal(page):
    print("📌 Extracting title...")

    selectors = [
        '[data-pl="product-title"]',
        'h1',
        '[class*="product-title"]'
    ]

    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                title = el.inner_text().strip()
                if len(title) > 10:
                    print(f"✅ Title: {title[:80]}...")
                    return title
        except:
            pass

    return ""


# ---------------------- STORE INFO (BASIC) ----------------------

def extract_store_info_universal(page):
    print("📦 Extracting store info...")

    try:
        text = page.content()
        soup = BeautifulSoup(text, "html.parser")

        store = {}

        for div in soup.find_all('div'):
            t = div.get_text(" ", strip=True).lower()
            if "store" in t and len(t) < 200:
                store["raw"] = t
                break

        return store

    except:
        return {}


# ---------------------- MAIN SCRAPER ----------------------

def extract_aliexpress_product(url: str) -> dict:

    print(f"\n🔍 Scraping: {url}")

    max_retries = 3

    for attempt in range(max_retries):

        print(f"\n📍 Attempt {attempt + 1}/{max_retries}")

        if attempt > 0:
            rotate_tor_circuit()
            time.sleep(20)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=['--disable-blink-features=AutomationControlled']
            )

            page = browser.new_page(
                viewport=random_viewport(),
                user_agent=random.choice([
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                ])
            )

            try:
                page.goto(url, timeout=120000)
                time.sleep(8)

                if is_captcha_page(page):
                    browser.close()
                    continue

                # Scroll
                page.mouse.wheel(0, 500)
                time.sleep(2)

                # Title
                title = extract_title_universal(page)

                # Store
                store_info = extract_store_info_universal(page)

                # Description
                description_text = ""
                description_images = []

                container = page.locator("#product-description").first

                if container.count() > 0:

                    shadow_text, shadow_images = extract_description_shadow_dom(page)

                    if shadow_text and len(shadow_text) > 1000:
                        description_text = shadow_text
                        description_images = shadow_images
                        print("   ✅ Using Shadow DOM")
                    else:
                        text = container.inner_text()
                        description_text = text
                        print("   ⚠️ Using fallback inner_text")

                    # CLEAN TEXT
                    seen = set()
                    clean = []

                    for line in description_text.splitlines():
                        line = line.strip()
                        if line and line not in seen:
                            seen.add(line)
                            clean.append(line)

                    description_text = "\n".join(clean)

                    # CLEAN IMAGES
                    description_images = list(set(description_images))
                    description_images = [
                        img for img in description_images
                        if "alicdn.com" in img and "gif" not in img.lower()
                    ]

                browser.close()

                print("✅ SUCCESS\n")

                return {
                    "title": title,
                    "description_text": description_text,
                    "images": description_images,
                    "store_info": store_info
                }

            except PlaywrightTimeoutError:
                browser.close()
                continue

            except Exception as e:
                print(f"❌ Error: {e}")
                browser.close()
                continue

    return {
        "title": "",
        "description_text": "",
        "images": [],
        "store_info": {}
    }
