import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller

# =========================================================
# 🧹 UTILITIES
# =========================================================

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

# =========================================================
# 🧅 TOR CONTROL
# =========================================================

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

# =========================================================
# 🛑 CAPTCHA DETECTION
# =========================================================

def is_captcha_page(page) -> bool:
    url = page.url.lower()
    title = page.title().lower()

    if any(x in url for x in ["captcha", "verify", "punish", "baxia"]):
        return True

    for sel in ["iframe[src*='captcha']", "[id*='captcha']", ".baxia-punish"]:
        try:
            if page.locator(sel).count() > 0:
                return True
        except:
            pass

    if any(x in title for x in ["verify", "blocked", "denied"]):
        return True

    return False

# =========================================================
# 🏪 STORE INFO
# =========================================================

def extract_store_info(page) -> dict:
    store_info = {}

    try:
        soup = BeautifulSoup(page.content(), "html.parser")

        for row in soup.select("div[class*='store'] tr"):
            cols = row.find_all("td")
            if len(cols) >= 2:
                key = clean_text(cols[0].text).replace(":", "")
                val = clean_text(cols[1].text)
                if key and val:
                    store_info[key] = val

    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")

    return store_info

# =========================================================
# 📌 TITLE
# =========================================================

def extract_title(page) -> str:
    for sel in [
        '[data-pl="product-title"]',
        'h1',
        '[class*="title"]'
    ]:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                txt = el.inner_text().strip()
                if len(txt) > 10:
                    return txt
        except:
            pass
    return ""

# =========================================================
# 📝 DESCRIPTION (MAIN LOGIC)
# =========================================================

def extract_description(page):
    description_text = ""
    description_images = []
    shadow_dom_text = ""
    desc_html = ""

    try:
        # Click description tab
        for btn in page.locator("a,button,div[role='tab']").all():
            try:
                if "description" in btn.inner_text().lower():
                    btn.click(force=True)
                    page.wait_for_timeout(3000)
                    break
            except:
                pass

        desc_container = page.locator("#product-description").first

        if desc_container.count() == 0:
            return "", []

        # -------- METHOD 1: ALL <p> TEXT --------
        try:
            texts = [
                p.inner_text().strip()
                for p in page.locator("#product-description p").all()
                if p.inner_text().strip()
            ]
            if texts:
                description_text = clean_text(" ".join(texts))
        except:
            pass

        # -------- METHOD 2: SHADOW DOM --------
        try:
            desc_html = page.evaluate("""
                () => {
                    const el = document.querySelector('#product-description');
                    if (!el) return "";

                    const shadowHost = el.querySelector('[shadowrootmode]');
                    if (shadowHost && shadowHost.shadowRoot) {
                        return shadowHost.shadowRoot.innerHTML;
                    }

                    return el.innerHTML;
                }
            """)
            shadow_dom_text = clean_text(desc_html)
        except:
            pass

        # -------- COMBINE --------
        combined = (shadow_dom_text + " " + description_text).strip()
        combined = re.sub(r'\s+', ' ', combined)

        # -------- IMAGES --------
        try:
            imgs = desc_container.locator("img").all()
            for img in imgs:
                src = (
                    img.get_attribute("src")
                    or img.get_attribute("data-src")
                    or img.get_attribute("data-lazy-src")
                )
                if src and "alicdn.com" in src:
                    src = src.split("?")[0]
                    description_images.append(src)

            description_images = list(set(description_images))[:20]
        except:
            pass

        return combined, description_images

    except Exception as e:
        print(f"⚠️ Description error: {e}")
        return "", []

# =========================================================
# 🚀 MAIN SCRAPER
# =========================================================

def extract_aliexpress_product(url: str) -> dict:

    empty_result = {
        "title": "",
        "description_text": "",
        "images": [],
        "store_info": {}
    }

    for attempt in range(3):

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
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X)',
                    'Mozilla/5.0 (X11; Linux x86_64)'
                ])
            )

            try:
                page.goto(url, timeout=120000)
                time.sleep(5)

                if is_captcha_page(page):
                    browser.close()
                    continue

                # Extract data
                title = extract_title(page)
                store_info = extract_store_info(page)
                description_text, images = extract_description(page)

                browser.close()

                result = {
                    "title": title,
                    "description_text": description_text,
                    "images": images,
                    "store_info": store_info
                }

                # Safe debug
                print("\n🔍 DEBUG:")
                print(f"   title: {len(result.get('title',''))}")
                print(f"   desc: {len(result.get('description_text',''))}")
                print(f"   images: {len(result.get('images',[]))}")
                print(f"   store: {result.get('store_info',{})}")

                return result

            except PlaywrightTimeoutError:
                browser.close()
                continue

            except Exception as e:
                print(f"❌ Error: {e}")
                browser.close()
                continue

    return empty_result
