import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller


def clean_text(text: str) -> str:
    """Clean and normalize text"""
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def random_delay(min_seconds: float = 1, max_seconds: float = 3):
    """Random delay to mimic human behavior"""
    delay = random.uniform(min_seconds, max_seconds)
    time.sleep(delay)


def random_viewport():
    """Return random viewport size"""
    viewports = [
        {'width': 1366, 'height': 768},
        {'width': 1920, 'height': 1080},
        {'width': 1440, 'height': 900},
        {'width': 1280, 'height': 720},
    ]
    return random.choice(viewports)


def rotate_tor_circuit():
    """Rotate Tor circuit to get new exit IP - wait longer for actual change"""
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            print("   Waiting 15s for new Tor circuit...")
            for i in range(15):
                time.sleep(1)
                if i % 5 == 4:
                    print(f"   ... {15 - i - 1}s remaining")
        print("✅ Tor circuit rotated - new IP acquired")
        return True
    except Exception as e:
        print(f"⚠️ Could not rotate Tor circuit: {e}")
        return False


def is_captcha_page(page) -> bool:
    """Detect if page is a CAPTCHA/block page - multiple selectors"""
    page_url = page.url.lower()
    page_title = page.title().lower()

    captcha_url_keywords = ["baxia", "punish", "captcha", "verify", "_____tmd_____"]
    if any(kw in page_url for kw in captcha_url_keywords):
        print("❌ CAPTCHA detected in URL")
        return True

    captcha_selectors = [
        "iframe[src*='recaptcha']",
        ".baxia-punish",
        "#captcha-verify",
        "[id*='captcha']",
        "iframe[src*='geetest']",
        "[class*='captcha']",
    ]

    for selector in captcha_selectors:
        try:
            if page.locator(selector).count() > 0:
                print(f"❌ CAPTCHA detected: {selector}")
                return True
        except:
            continue

    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    block_title_keywords = ["verify", "access", "denied", "blocked", "challenge"]
    if not is_product_page and any(kw in page_title for kw in block_title_keywords):
        print("❌ Block page detected from title")
        return True

    return False


def extract_store_info_universal(page) -> dict:
    """Extract store info - try multiple selectors with debugging"""
    store_info = {}

    print("📦 Extracting store info...")

    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Search 1: Direct class selector
        print("   🔍 Search 1: Looking for store-detail elements...")
        store_elem = soup.find('div', class_=lambda x: x and 'store-detail' in x)

        if store_elem:
            print(f"   ✓ Found store element")
            table = store_elem.find('table')
            if table:
                print(f"   ✓ Found table with store info")
                for row in table.find_all('tr'):
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        key = clean_text(cols[0].get_text()).replace(":", "").strip()
                        value = clean_text(cols[1].get_text()).strip()
                        if key and value:
                            store_info[key] = value
                            print(f"      {key}: {value}")
            else:
                print(f"   ⚠️ No table found in store element")
                elem_text = store_elem.get_text(" ", strip=True)[:200]
                print(f"   Element content: {elem_text}...")

        # Search 2: Broader search
        if not store_info:
            print("   🔍 Search 2: Broader search for store information...")
            all_divs = soup.find_all('div', class_=lambda x: x and any(s in str(x).lower() for s in ['store', 'seller', 'shop']))
            print(f"   Found {len(all_divs)} potential store divs")

            for div in all_divs[:5]:
                tbl = div.find('table')
                if tbl:
                    print(f"   ✓ Found table in div")
                    for row in tbl.find_all('tr'):
                        cols = row.find_all('td')
                        if len(cols) >= 2:
                            key = clean_text(cols[0].get_text()).replace(":", "").strip()
                            value = clean_text(cols[1].get_text()).strip()
                            if key and value:
                                store_info[key] = value
                                print(f"      {key}: {value}")
                    if store_info:
                        break

        # Search 3: Text-based fallback
        if not store_info:
            print("   🔍 Search 3: Looking for store info in text elements...")
            all_text = soup.get_text()
            if 'store no' in all_text.lower():
                print("   📝 Store info text found on page but table structure not matching")
                containers = soup.find_all(['div', 'span'], class_=lambda x: x and any(s in str(x).lower() for s in ['store', 'seller', 'shop', 'info']))
                for container in containers:
                    text = container.get_text()
                    if 'store' in text.lower() or 'seller' in text.lower():
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        for i, line in enumerate(lines):
                            if ':' in line or (i + 1 < len(lines) and line.endswith(('Store', 'no.', 'Location', 'since'))):
                                if ':' in line:
                                    parts = line.split(':', 1)
                                    key = parts[0].strip()
                                    value = parts[1].strip() if len(parts) > 1 else ""
                                    if key and value and len(key) < 50:
                                        store_info[key] = value
                                        print(f"      {key}: {value}")

        if not store_info:
            print("   ⚠️ Could not extract store information")

    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")
        import traceback
        traceback.print_exc()

    return store_info


def extract_title_universal(page) -> str:
    """Extract title - try multiple selectors"""

    print("📌 Extracting title...")

    title_selectors = [
        ('[data-pl="product-title"]', "data-pl product-title"),
        ('h1', "h1 heading"),
        ('[class*="product-title"]', "product-title class"),
        ('[class*="ProductTitle"]', "ProductTitle class"),
        ('span[class*="title"]', "span title class"),
    ]

    for selector, desc in title_selectors:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 10:
                    print(f"✅ Title ({desc}): {title[:80]}...")
                    return title
        except:
            continue

    print("⚠️ Could not extract title")
    return ""


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data with Tor routing and anti-detection.
    """

    print(f"\n🔍 Scraping: {url}")

    empty_result = {
        "title": "",
        "description_text": "",
        "images": [],
        "store_info": {}
    }

    max_retries = 3

    for attempt in range(max_retries):
        print(f"\n📍 Attempt {attempt + 1}/{max_retries}")

        if attempt > 0:
            print("🔄 Rotating Tor circuit...")
            rotate_tor_circuit()
            wait_time = 20 + (attempt * 5)
            print(f"   Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ]
            )

            page = browser.new_page(
                viewport=random_viewport(),
                user_agent=random.choice([
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]),
                timezone_id=random.choice([
                    'America/New_York',
                    'America/Chicago',
                    'America/Denver',
                    'America/Los_Angeles',
                ])
            )

            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]})")

            try:
                # NAVIGATION
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")
                time.sleep(2)

                current_url = page.url
                if current_url != url:
                    print(f"⚠️ Redirected to: {current_url}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue

                print("⏳ Waiting for page to render...")
                time.sleep(8)

                print("⏳ Scrolling to load images...")
                try:
                    for _ in range(3):
                        page.mouse.wheel(0, random.randint(150, 300))
                        time.sleep(random.uniform(0.2, 0.6))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll - rotating IP and retrying...")
                    browser.close()
                    continue

                # EXTRACT TITLE
                title = extract_title_universal(page)

                # EXTRACT STORE INFO
                store_info = extract_store_info_universal(page)

                # EXTRACT DESCRIPTION
                print("📝 Loading description...")
                description_text = ""
                description_images = []

                try:
                    # Click description tab
                    print("   Clicking Description tab...")
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)

                        buttons = page.locator('a.comet-v2-anchor-link').all()
                        for btn in buttons:
                            if 'description' in btn.inner_text().strip().lower():
                                print("   ✓ Found Description button (comet-v2-anchor-link)")
                                btn.click(force=True, timeout=2000)
                                print("   ⏳ Waiting for description content to load...")
                                page.wait_for_timeout(3000)
                                try:
                                    page.locator('#product-description').scroll_into_view_if_needed()
                                    page.wait_for_timeout(2000)
                                except:
                                    pass
                                page.wait_for_timeout(3000)
                                print("   ✓ Clicked Description tab")
                                break
                    except Exception as e:
                        print(f"   ⚠️ Description tab click error: {e}")

                    # Single extraction: inner_text() on container
                    desc_container = page.locator('#product-description').first

                    if desc_container.count() > 0:
                        print("   ✓ Found #product-description container")

                        description_text = desc_container.inner_text(timeout=5000).strip()
                        description_text = re.sub(r'\s+', ' ', description_text).strip()
                        print(f"   ✓ Extracted: {len(description_text)} chars")

                        # Retry once if too short
                        if len(description_text) < 100:
                            print("   ⏳ Content short, waiting 5s and retrying...")
                            page.wait_for_timeout(5000)
                            description_text = desc_container.inner_text(timeout=5000).strip()
                            description_text = re.sub(r'\s+', ' ', description_text).strip()
                            print(f"   ✓ After retry: {len(description_text)} chars")

                        # IMAGE EXTRACTION: direct locator on container
                        print("   🖼️ Extracting images...")
                        all_imgs = desc_container.locator('img').all()
                        print(f"      Found {len(all_imgs)} <img> tags")

                        for img in all_imgs:
                            src = (img.get_attribute("src") or
                                   img.get_attribute("data-src") or
                                   img.get_attribute("data-lazy-src"))
                            if src and "alicdn.com" in src:
                                clean_src = src.split('?')[0]
                                if clean_src not in description_images:
                                    description_images.append(clean_src)

                        # Quality filter + limit
                        description_images = [
                            img for img in description_images
                            if len(img) > 50 and not any(
                                bad in img.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100']
                            )
                        ][:20]
                        print(f"   ✓ Images: {len(description_images)}")

                        if description_images:
                            for i, img_url in enumerate(description_images[:3], 1):
                                print(f"      {i}. {img_url[:60]}...")
                    else:
                        print("   ❌ #product-description not found")

                except Exception as e:
                    print(f"⚠️ Description extraction error: {e}")

                # SUCCESS
                browser.close()

                result = {
                    "title": title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images": description_images if isinstance(description_images, list) else [],
                    "store_info": store_info if isinstance(store_info, dict) else {}
                }

                print(f"\n🔍 DEBUG RETURN VALUES:")
                print(f"   title: {len(result['title'])} chars")
                print(f"   description_text: {len(result['description_text'])} chars")
                print(f"   images: {len(result['images'])} images")
                print(f"   store_info: {result['store_info']}")
                print(f"✅ Extraction successful on attempt {attempt + 1}\n")
                return result

            except PlaywrightTimeoutError as e:
                print(f"⚠️ Timeout on attempt {attempt + 1}: {e}")
                browser.close()
                continue

            except Exception as e:
                print(f"❌ Error on attempt {attempt + 1}: {e}")
                import traceback
                traceback.print_exc()
                try:
                    browser.close()
                except:
                    pass
                continue

    print(f"❌ Failed after {max_retries} attempts")
    return empty_result
