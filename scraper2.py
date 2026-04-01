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
    viewports = [
        {'width': 1366, 'height': 768},
        {'width': 1920, 'height': 1080},
        {'width': 1440, 'height': 900},
        {'width': 1280, 'height': 720},
    ]
    return random.choice(viewports)


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
        print("✅ Tor circuit rotated - new IP acquired")
        return True
    except Exception as e:
        print(f"⚠️ Could not rotate Tor circuit: {e}")
        return False


def is_captcha_page(page) -> bool:
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


def debug_screenshot(page, label: str):
    """Save a debug screenshot with a label."""
    try:
        path = f"/tmp/debug_{label}.png"
        page.screenshot(path=path, full_page=False)
        print(f"   📸 Screenshot saved: {path}")
    except Exception as e:
        print(f"   ⚠️ Screenshot failed: {e}")


def slow_scroll_to_bottom(page, steps: int = 8):
    """Slowly scroll down the page to trigger lazy-loaded content."""
    print("   🖱️ Slow-scrolling page to trigger lazy loads...")
    for i in range(steps):
        page.mouse.wheel(0, random.randint(300, 500))
        time.sleep(random.uniform(0.4, 0.8))
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1)


def wait_for_description_section(page, timeout_ms: int = 15000) -> bool:
    """
    Try to wait for the description section to appear using multiple selectors.
    Returns True if found.
    """
    description_selectors = [
        "#product-description",
        "[id*='description']",
        "[class*='product-description']",
        "[class*='productDescription']",
        "[class*='detail-desc']",
        "[class*='detailDesc']",
        "div[class*='description--wrap']",
        "div[class*='descriptionModule']",
    ]

    for sel in description_selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout_ms)
            print(f"   ✅ Description container found: {sel}")
            return True
        except:
            continue

    print("   ⚠️ No description container found with known selectors")
    return False


def click_description_tab(page) -> bool:
    """
    Try multiple strategies to click the Description tab/anchor.
    Returns True if successfully clicked.
    """
    print("   🖱️ Attempting to click Description tab...")

    # Strategy 1: comet-v2-anchor-link buttons
    try:
        buttons = page.locator('a.comet-v2-anchor-link').all()
        for btn in buttons:
            text = btn.inner_text().strip().lower()
            if 'description' in text or 'beschrijving' in text or 'descripción' in text:
                btn.scroll_into_view_if_needed()
                time.sleep(0.5)
                btn.click(force=True, timeout=3000)
                print(f"   ✅ Strategy 1: Clicked anchor tab '{text}'")
                return True
    except Exception as e:
        print(f"   ⚠️ Strategy 1 failed: {e}")

    # Strategy 2: Any tab/button containing 'description' text
    try:
        desc_tab = page.locator(
            "a:has-text('Description'), button:has-text('Description'), "
            "a:has-text('Beschrijving'), a:has-text('Descripción')"
        ).first
        if desc_tab.count() > 0:
            desc_tab.scroll_into_view_if_needed()
            time.sleep(0.5)
            desc_tab.click(force=True, timeout=3000)
            print("   ✅ Strategy 2: Clicked description tab by text")
            return True
    except Exception as e:
        print(f"   ⚠️ Strategy 2 failed: {e}")

    # Strategy 3: Scroll to #product-description directly (no click needed)
    try:
        target = page.locator("#product-description, [id*='description']").first
        if target.count() > 0:
            target.scroll_into_view_if_needed()
            print("   ✅ Strategy 3: Scrolled directly to description section")
            time.sleep(2)
            return True
    except Exception as e:
        print(f"   ⚠️ Strategy 3 failed: {e}")

    # Strategy 4: Use JS scroll to bottom (description usually at bottom)
    try:
        print("   🔃 Strategy 4: JS scroll to bottom of page...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(3)
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1)
        return False  # Didn't click a tab but attempted scroll
    except Exception as e:
        print(f"   ⚠️ Strategy 4 failed: {e}")

    return False


def extract_description_text(page) -> tuple[str, list]:
    """
    Extract description text and images with multiple fallback strategies.
    Returns (description_text, image_urls).
    """
    print("\n📝 Extracting description...")
    description_text = ""
    description_images = []

    # --- Click the description tab first ---
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    clicked = click_description_tab(page)

    # Wait longer after click for content to hydrate
    wait_after_click = 6000 if clicked else 3000
    print(f"   ⏳ Waiting {wait_after_click}ms for description to load...")
    page.wait_for_timeout(wait_after_click)

    # --- Wait for container to appear ---
    found = wait_for_description_section(page, timeout_ms=12000)
    if not found:
        debug_screenshot(page, "no_description_container")

    # Extra scroll to trigger lazy images inside description
    try:
        desc_area = page.locator(
            "#product-description, [class*='product-description'], [class*='descriptionModule']"
        ).first
        if desc_area.count() > 0:
            desc_area.scroll_into_view_if_needed()
            page.wait_for_timeout(2000)
            # Scroll through it slowly
            for _ in range(4):
                page.mouse.wheel(0, 400)
                page.wait_for_timeout(500)
            page.wait_for_timeout(3000)  # Wait for lazy images after scroll
            print("   ✅ Scrolled through description area")
    except Exception as e:
        print(f"   ⚠️ Description scroll error: {e}")

    # --- Try all known container selectors ---
    container_selectors = [
        "#product-description",
        "[class*='product-description--wrap']",
        "[class*='productDescription']",
        "[class*='description--content']",
        "[class*='detail-desc']",
        "[class*='descriptionModule']",
        "[class*='description--wrap']",
        "div[id*='description']",
    ]

    for sel in container_selectors:
        try:
            container = page.locator(sel).first
            if container.count() == 0:
                continue

            print(f"   🔍 Trying container: {sel}")

            # Method A: Extract <p> tags
            paragraphs = container.locator("p").all()
            para_parts = []
            for p in paragraphs:
                try:
                    txt = p.inner_text(timeout=2000).strip()
                    if txt and len(txt) > 2:
                        para_parts.append(txt)
                except:
                    pass

            if para_parts:
                text_from_p = " ".join(para_parts)
                text_from_p = re.sub(r"\s+", " ", text_from_p).strip()
                print(f"   ✅ Method A (<p> tags): {len(text_from_p)} chars")
                description_text = text_from_p

            # Method B: inner_text() of full container
            method_b = container.inner_text(timeout=6000).strip()
            method_b = re.sub(r"\s+", " ", method_b).strip()
            print(f"   ✅ Method B (inner_text): {len(method_b)} chars")

            # If Method B is richer, use it
            if len(method_b) > len(description_text):
                description_text = method_b

            # If still short, wait more and retry once
            if len(description_text) < 100:
                print("   ⏳ Content too short, waiting 6s and retrying...")
                page.wait_for_timeout(6000)
                retry_text = container.inner_text(timeout=6000).strip()
                retry_text = re.sub(r"\s+", " ", retry_text).strip()
                print(f"   ✅ After retry: {len(retry_text)} chars")
                if len(retry_text) > len(description_text):
                    description_text = retry_text

            # --- Extract images from this container ---
            print("   🖼️ Extracting description images...")
            imgs = container.locator("img").all()
            print(f"      Found {len(imgs)} <img> tags in container")

            for img in imgs:
                src = (
                    img.get_attribute("src") or
                    img.get_attribute("data-src") or
                    img.get_attribute("data-lazy-src") or
                    img.get_attribute("data-original")
                )
                if src and "alicdn.com" in src:
                    clean_src = src.split("?")[0]
                    if clean_src not in description_images:
                        description_images.append(clean_src)

            if description_text:
                break  # Got content, stop trying other selectors

        except Exception as e:
            print(f"   ⚠️ Container {sel} error: {e}")
            continue

    # --- Last resort: grab ALL page text near 'description' keyword ---
    if not description_text:
        print("   🆘 Last resort: Searching all page text for description-like content...")
        debug_screenshot(page, "last_resort_description")
        try:
            # Dump all text from page body and look for substantive content
            full_text = page.inner_text("body")
            # Heuristic: find large blocks of text (>200 chars between newlines)
            lines = [l.strip() for l in full_text.split('\n') if len(l.strip()) > 80]
            if lines:
                description_text = " ".join(lines[:20])  # Take first 20 long lines
                description_text = re.sub(r"\s+", " ", description_text).strip()
                print(f"   ✅ Last resort extracted: {len(description_text)} chars")
        except Exception as e:
            print(f"   ⚠️ Last resort failed: {e}")

    # --- Filter images ---
    description_images = [
        img for img in description_images
        if len(img) > 50 and not any(
            bad in img.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100']
        )
    ][:20]

    print(f"\n   📊 Description result: {len(description_text)} chars, {len(description_images)} images")
    return description_text, description_images


def extract_store_info_universal(page) -> dict:
    store_info = {}
    print("📦 Extracting store info...")
    try:
        print("   🔍 Step 1: Extracting store name...")
        store_name_selector = "span[class*='store-detail--storeName']"
        store_name_elem = page.locator(store_name_selector).first
        if store_name_elem.count() > 0:
            store_name = store_name_elem.inner_text().strip()
            if store_name:
                store_info["Store Name"] = store_name
                print(f"   ✓ Store name: {store_name}")
        else:
            print("   ⚠️ Store name element not found")

        print("   🔍 Step 2: Hovering to reveal store detail popup...")
        store_link_selector = "div[class*='store-detail--storeNameWrap']"
        store_link_elem = page.locator(store_link_selector).first
        if store_link_elem.count() > 0:
            store_link_elem.hover()
            page.wait_for_timeout(1500)
            print("   ✓ Hovered over store element")
        else:
            print("   ⚠️ Store link element not found, skipping hover")

        print("   🔍 Step 3: Extracting popup store details...")
        row_selectors = [
            "div[class*='store-detail'] table tr",
            "div[class*='storeDetail'] table tr",
            "[class*='store-detail--detail'] tr",
        ]
        for row_selector in row_selectors:
            rows = page.locator(row_selector).all()
            if rows:
                print(f"   ✓ Found {len(rows)} rows with: {row_selector}")
                for row in rows:
                    try:
                        cols = row.locator('td').all()
                        if len(cols) >= 2:
                            key = cols[0].inner_text().strip().replace(":", "")
                            value = cols[1].inner_text().strip()
                            if key and value:
                                store_info[key] = value
                                print(f"      {key}: {value}")
                    except:
                        continue
                if len(store_info) > 1:
                    break

        if len(store_info) <= 1:
            print("   🔍 Step 4: Fallback — reading popup text directly...")
            popup_selectors = [
                "div[class*='store-detail--storePopup']",
                "div[class*='store-detail--popup']",
                "div[class*='storePopup']",
                "div[class*='store-detail']:not(a)",
            ]
            for popup_selector in popup_selectors:
                popup = page.locator(popup_selector).first
                if popup.count() > 0:
                    text = popup.inner_text().strip()
                    if text:
                        print(f"   ✓ Popup text ({popup_selector}):\n      {text[:200]}")
                        for line in text.split('\n'):
                            line = line.strip()
                            if ':' in line:
                                parts = line.split(':', 1)
                                key = parts[0].strip()
                                value = parts[1].strip()
                                if key and value and len(key) < 50:
                                    store_info[key] = value
                    if len(store_info) > 1:
                        break

        print(f"   ✅ Store info extracted: {store_info}")
    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")
        import traceback
        traceback.print_exc()
    return store_info


def extract_title_universal(page) -> str:
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
                    'America/New_York', 'America/Chicago',
                    'America/Denver', 'America/Los_Angeles',
                ])
            )

            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]})")

            try:
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

                # ✅ Wait for networkidle so JS-rendered content settles
                print("⏳ Waiting for network idle...")
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                    print("   ✅ Network idle reached")
                except:
                    print("   ⚠️ Network idle timeout - continuing anyway")

                # ✅ Extra wait for JS hydration
                print("⏳ Waiting 5s for JS hydration...")
                time.sleep(5)

                # ✅ Slow scroll to trigger all lazy-loaded content
                slow_scroll_to_bottom(page, steps=10)

                # ✅ Wait again after scroll
                print("⏳ Waiting 3s after scroll...")
                time.sleep(3)

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll - rotating IP and retrying...")
                    browser.close()
                    continue

                debug_screenshot(page, f"attempt_{attempt + 1}_after_scroll")

                # EXTRACT TITLE
                title = extract_title_universal(page)

                # EXTRACT STORE INFO
                store_info = extract_store_info_universal(page)

                # EXTRACT DESCRIPTION (improved)
                description_text, description_images = extract_description_text(page)

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

                # ✅ Only count as success if we got meaningful content
                if result["title"] and (result["description_text"] or result["store_info"]):
                    print(f"✅ Extraction successful on attempt {attempt + 1}\n")
                    return result
                else:
                    print(f"⚠️ Extraction incomplete on attempt {attempt + 1}, retrying...\n")
                    continue

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
