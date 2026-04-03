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


def check_exit_node(page) -> bool:
    """Verify exit node is usable and log the IP before scraping."""
    try:
        page.goto("https://httpbin.org/ip", timeout=15000, wait_until="domcontentloaded")
        ip_text = page.inner_text("body").strip()
        print(f"   ✅ Exit node reachable. IP info: {ip_text}")
        return True
    except Exception as e:
        print(f"   ⚠️ Exit node unreachable: {e}")
        return False


def set_shipping_to_poland(page) -> bool:
    """
    Open the ship-to selector, choose Poland, and save.
    This makes AliExpress show the Product Compliance section
    regardless of which exit node country we are on.
    Returns True if successfully set, False otherwise.
    """
    print("🌍 Setting shipping destination to Poland...")

    try:
        # Step 1: Click the globe / ship-to arrow button to open the panel
        # The SVG arrow has a unique path — match it via the parent trigger element.
        # Multiple selectors tried in order of reliability.
        trigger_selectors = [
            "div[class*='es--trigger']",                  # most specific
            "div[class*='ship-to'] [class*='arrow']",
            "[class*='select--arrow']",                   # arrow chevron inside header
            "span[class*='comet-icon-arrowleftrtl']",    # icon class
        ]

        opened = False
        for sel in trigger_selectors:
            try:
                elem = page.locator(sel).first
                if elem.count() > 0:
                    elem.click(timeout=3000)
                    page.wait_for_timeout(1000)
                    # Confirm panel opened by checking for Ship to title
                    if page.locator("div[class*='form-item--title']").filter(has_text="Ship to").count() > 0:
                        print(f"   ✓ Ship-to panel opened via: {sel}")
                        opened = True
                        break
            except:
                continue

        if not opened:
            # Fallback: look for the visible country text (e.g. "Pakistan") and click it
            try:
                country_text = page.locator("div[class*='select--text']").first
                if country_text.count() > 0:
                    country_text.click(timeout=3000)
                    page.wait_for_timeout(1000)
                    opened = page.locator("div[class*='form-item--title']").filter(has_text="Ship to").count() > 0
                    if opened:
                        print("   ✓ Ship-to panel opened via country text fallback")
            except:
                pass

        if not opened:
            print("   ⚠️ Could not open ship-to panel — compliance may still appear")
            return False

        # Step 2: Click the Ship To dropdown to open country list
        print("   🔍 Opening country dropdown...")
        ship_to_wrap = page.locator("div[class*='form-item--content']").first
        try:
            ship_to_wrap.click(timeout=3000)
            page.wait_for_timeout(800)
        except:
            pass

        # Step 3: Search for Poland in the search input inside the dropdown
        print("   🔍 Searching for Poland...")
        search_input_selectors = [
            "div[class*='select--popup'] input",
            "div[class*='select--search'] input",
        ]
        typed = False
        for sel in search_input_selectors:
            try:
                inp = page.locator(sel).first
                if inp.count() > 0:
                    inp.click(timeout=2000)
                    inp.fill("Poland")
                    page.wait_for_timeout(600)
                    typed = True
                    print("   ✓ Typed 'Poland' in search")
                    break
            except:
                continue

        # Step 4: Click the Poland item in the dropdown
        print("   🔍 Clicking Poland option...")
        poland_selectors = [
            "div[class*='select--item']",           # generic item — filter by text
            "span.country-flag-y2023.PL",           # PL flag span
        ]

        clicked_poland = False

        # First try: flag span (most precise)
        try:
            pl_flag = page.locator("span.country-flag-y2023.PL").first
            if pl_flag.count() > 0:
                pl_flag.click(timeout=3000)
                page.wait_for_timeout(500)
                clicked_poland = True
                print("   ✓ Clicked Poland via PL flag span")
        except:
            pass

        # Second try: item div filtered by text
        if not clicked_poland:
            try:
                poland_item = page.locator("div[class*='select--item']").filter(has_text="Poland").first
                if poland_item.count() > 0:
                    poland_item.click(timeout=3000)
                    page.wait_for_timeout(500)
                    clicked_poland = True
                    print("   ✓ Clicked Poland via text filter")
            except:
                pass

        if not clicked_poland:
            print("   ⚠️ Could not find Poland in dropdown")
            return False

        # Step 5: Click Save button
        print("   💾 Clicking Save...")
        save_selectors = [
            "div[class*='es--saveBtn']",
            "div[class*='saveBtn']",
            "button:has-text('Save')",
        ]
        for sel in save_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    btn.click(timeout=3000)
                    print("   ✓ Clicked Save")
                    break
            except:
                continue

        # Step 6: Wait for page to reload/update with new shipping destination
        page.wait_for_timeout(3000)

        # Confirm Poland is now selected
        try:
            current = page.locator("div[class*='select--text']").first.inner_text(timeout=2000)
            if "poland" in current.lower():
                print("   ✅ Shipping destination confirmed: Poland")
            else:
                print(f"   ℹ️ Ship-to text now: {current.strip()}")
        except:
            pass

        return True

    except Exception as e:
        print(f"⚠️ set_shipping_to_poland error: {e}")
        import traceback
        traceback.print_exc()
        return False


def extract_store_info_universal(page) -> dict:
    """Extract store info by hovering over the store element to trigger the popup."""
    store_info = {}

    print("📦 Extracting store info...")

    try:
        # Step 1: Extract store name directly from known selector (always visible)
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

        # Step 2: Hover over the store link to trigger the popup
        print("   🔍 Step 2: Hovering to reveal store detail popup...")
        store_link_selector = "div[class*='store-detail--storeNameWrap']"
        store_link_elem = page.locator(store_link_selector).first

        if store_link_elem.count() > 0:
            store_link_elem.hover()
            page.wait_for_timeout(1500)
            print("   ✓ Hovered over store element")
        else:
            print("   ⚠️ Store link element not found, skipping hover")

        # Step 3: Extract all key-value rows from the popup (renders after hover)
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

        # Step 4: Fallback — read visible popup text and parse key: value lines
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
                                    print(f"      {key}: {value}")
                    if len(store_info) > 1:
                        break

        if not store_info:
            print("   ⚠️ Could not extract store information")
        else:
            print(f"   ✅ Store info extracted: {store_info}")

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


def extract_compliance_info(page) -> dict:
    """
    Click the 'Product compliance information' h2 heading to open the modal,
    then parse manufacturer info, EU responsible person, and product identifier.
    Only visible on GB exit nodes with en-GB locale.
    """
    compliance = {}
    print("📋 Extracting compliance info...")

    try:
        # Step 1: Find and click the compliance h2 heading
        # Use text-based selector — avoids brittle hashed CSS class suffixes
        heading = page.locator("h2").filter(has_text="Product compliance information").first

        if heading.count() == 0:
            print("   ⚠️ Compliance heading not found (may not be a GB IP or product has none)")
            return compliance

        print("   ✓ Found compliance heading — clicking...")
        heading.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        heading.click()
        page.wait_for_timeout(2000)

        # Step 2: Wait for modal body
        modal_selector = "div.comet-v2-modal-body"
        try:
            page.wait_for_selector(modal_selector, timeout=8000)
        except:
            print("   ⚠️ Modal did not appear after click")
            return compliance

        modal = page.locator(modal_selector).first
        if modal.count() == 0:
            print("   ⚠️ Modal body not found")
            return compliance

        raw_text = modal.inner_text().strip()
        print(f"   ✓ Modal text ({len(raw_text)} chars):\n      {raw_text[:300]}")

        # Step 3: Parse sections from raw text
        section_headers = [
            "Manufacturer information",
            "EU responsible person information",
            "Product identifier",
        ]

        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]

        current_section = None
        section_lines: dict = {}

        for line in lines:
            matched_header = next(
                (h for h in section_headers if line.lower().startswith(h.lower())),
                None
            )
            if matched_header:
                current_section = matched_header
                section_lines[current_section] = []
                # Content on same line after the header
                remainder = line[len(matched_header):].strip().lstrip(":").strip()
                if remainder:
                    section_lines[current_section].append(remainder)
            elif current_section:
                section_lines[current_section].append(line)

        # Step 4: Parse key:value pairs inside each section
        def parse_kv_block(lines_list: list) -> dict:
            result = {}
            for l in lines_list:
                if ":" in l:
                    k, _, v = l.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if k and v and len(k) < 60:
                        result[k] = v
                else:
                    # Plain text line (e.g. product identifier number)
                    if l and not result.get("value"):
                        result["value"] = l
            return result

        for section, s_lines in section_lines.items():
            parsed = parse_kv_block(s_lines)
            if parsed:
                compliance[section] = parsed
                print(f"   ✅ {section}: {parsed}")

        # Step 5: Close modal so it doesn't interfere with later extraction
        close_selectors = [
            "button.comet-v2-modal-close",
            "[class*='modal-close']",
            "[aria-label='Close']",
        ]
        for sel in close_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0:
                    btn.click()
                    page.wait_for_timeout(500)
                    print("   ✓ Closed compliance modal")
                    break
            except:
                continue

    except Exception as e:
        print(f"⚠️ Compliance extraction error: {e}")
        import traceback
        traceback.print_exc()

    return compliance


def extract_description(page) -> tuple:
    """
    Extract description text and images from #product-description.
    Uses JS DOM walker for reliable in-order extraction of all tag types
    (h1-h6, p, div.paragraph-element, li) without relying on hashed CSS classes.
    Returns (description_text: str, description_images: list[str])
    """
    description_text = ""
    description_images = []

    print("📝 Extracting description...")

    try:
        # Step 1: Click Description tab
        print("   Clicking Description tab...")
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

            buttons = page.locator('a.comet-v2-anchor-link').all()
            clicked = False
            for btn in buttons:
                try:
                    if 'description' in btn.inner_text(timeout=1000).strip().lower():
                        print("   ✓ Found Description button — clicking...")
                        btn.click(force=True, timeout=2000)
                        clicked = True
                        break
                except:
                    continue

            if not clicked:
                print("   ⚠️ Description tab not found via anchor links")
        except Exception as e:
            print(f"   ⚠️ Description tab click error: {e}")

        # Step 2: Wait for #product-description to appear
        print("   ⏳ Waiting for #product-description...")
        try:
            page.wait_for_selector('#product-description', timeout=10000)
        except:
            print("   ⚠️ #product-description did not appear — trying scroll fallback...")
            try:
                page.evaluate("document.querySelector('#product-description')?.scrollIntoView()")
                page.wait_for_timeout(3000)
            except:
                pass

        desc_container = page.locator('#product-description').first

        if desc_container.count() == 0:
            print("   ❌ #product-description not found after waiting")
            return description_text, description_images

        print("   ✓ Found #product-description container")

        # Step 3: Scroll into view to trigger lazy rendering
        try:
            desc_container.scroll_into_view_if_needed()
            page.wait_for_timeout(2500)
        except:
            pass

        # Step 4: JS DOM walker — extracts all text in DOM order
        # Handles h1-h6, p, li, div.paragraph-element; skips script/style
        # Deduplicates consecutive identical lines
        print("   🎯 Running JS DOM walker...")
        raw_js_text = page.evaluate("""
            () => {
                const container = document.querySelector('#product-description');
                if (!container) return '';

                const parts = [];
                const blockTags = new Set(['h1','h2','h3','h4','h5','h6','p','li','div','tr','td','th','br','blockquote','pre']);

                const walk = (node) => {
                    if (node.nodeType === Node.TEXT_NODE) {
                        const t = node.textContent.trim();
                        if (t && t.length > 1) parts.push(t);
                        return;
                    }
                    if (node.nodeType !== Node.ELEMENT_NODE) return;

                    const tag = node.tagName.toLowerCase();
                    if (tag === 'script' || tag === 'style') return;

                    if (blockTags.has(tag)) {
                        // Use innerText which handles nested inline elements cleanly
                        const text = (node.innerText || '').trim();
                        if (text && text.length > 1) {
                            parts.push(text);
                        }
                        return; // innerText already handled all children
                    }

                    // Inline elements — recurse into children
                    for (const child of node.childNodes) walk(child);
                };

                walk(container);

                // Deduplicate consecutive identical entries
                const deduped = parts.filter((v, i) => v !== parts[i - 1]);
                return deduped.join(' | ');
            }
        """)

        if raw_js_text and len(raw_js_text) > 50:
            description_text = re.sub(r'\s+', ' ', raw_js_text).strip()
            print(f"   ✅ JS DOM walker: {len(description_text)} chars")
        else:
            # Fallback: plain inner_text on the whole container
            print("   ⚠️ JS walker empty — falling back to inner_text...")
            fallback = desc_container.inner_text(timeout=8000).strip()
            description_text = re.sub(r'\s+', ' ', fallback).strip()
            print(f"   ✅ Fallback inner_text: {len(description_text)} chars")

        # Step 5: Image extraction
        print("   🖼️ Extracting description images...")
        all_imgs = desc_container.locator('img').all()
        print(f"      Found {len(all_imgs)} <img> tags")

        for img in all_imgs:
            src = (
                img.get_attribute("src") or
                img.get_attribute("data-src") or
                img.get_attribute("data-lazy-src")
            )
            if src and "alicdn.com" in src:
                clean_src = src.split('?')[0]
                if clean_src not in description_images:
                    description_images.append(clean_src)

        description_images = [
            img for img in description_images
            if len(img) > 50 and not any(
                bad in img.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100']
            )
        ][:20]
        print(f"   ✓ Description images: {len(description_images)}")
        for i, img_url in enumerate(description_images[:3], 1):
            print(f"      {i}. {img_url[:60]}...")

    except Exception as e:
        print(f"⚠️ Description extraction error: {e}")
        import traceback
        traceback.print_exc()

    return description_text, description_images


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data with Tor routing and anti-detection.
    Uses multi-country exit nodes for maximum success rate.
    Compliance info is obtained by setting Ship To = Poland via UI
    (works regardless of which exit country Tor assigns).
    """

    print(f"\n🔍 Scraping: {url}")

    empty_result = {
        "title":            "",
        "description_text": "",
        "images":           [],
        "store_info":       {},
        "compliance_info":  {},
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

            # Randomised viewport + UA for bot fingerprint variety
            # Locale stays en-GB so AliExpress renders compliance section text in English.
            # Timezone is randomised across EU zones — consistent with our EU-heavy exit nodes.
            page = browser.new_page(
                viewport=random.choice([
                    {'width': 1920, 'height': 1080},
                    {'width': 1440, 'height': 900},
                    {'width': 1366, 'height': 768},
                    {'width': 1280, 'height': 720},
                ]),
                user_agent=random.choice([
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]),
                timezone_id=random.choice([
                    'Europe/London',
                    'Europe/Berlin',
                    'Europe/Paris',
                    'Europe/Amsterdam',
                    'Europe/Warsaw',
                ]),
                locale='en-GB',   # keeps compliance text in English regardless of exit node
            )

            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]})")

            try:
                # PRE-FLIGHT: verify exit node is usable before hitting AliExpress
                print("🌐 Checking exit node...")
                if not check_exit_node(page):
                    print("⚠️ Exit node check failed — rotating and retrying...")
                    browser.close()
                    rotate_tor_circuit()
                    continue

                # NAVIGATION
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")

                current_url = page.url
                if current_url != url:
                    print(f"⚠️ Redirected to: {current_url}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected — rotating IP and retrying...")
                    browser.close()
                    continue

                # Wait for the product title to confirm page is rendered
                # rather than a fixed sleep — adapts to actual page load speed
                print("⏳ Waiting for product title to confirm page render...")
                try:
                    page.wait_for_selector('[data-pl="product-title"]', timeout=15000)
                    print("   ✓ Product title detected")
                except:
                    print("   ⚠️ Title selector timed out — trying h1...")
                    try:
                        page.wait_for_selector('h1', timeout=8000)
                        print("   ✓ h1 detected")
                    except:
                        print("   ⚠️ Page render confirmation failed — proceeding anyway")

                # Gentle scroll to trigger lazy loading without racing
                print("⏳ Scrolling to load lazy content...")
                try:
                    for _ in range(3):
                        page.mouse.wheel(0, random.randint(150, 300))
                        time.sleep(random.uniform(0.3, 0.7))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")

                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll — rotating IP and retrying...")
                    browser.close()
                    continue

                # SET SHIPPING TO POLAND — makes compliance section visible
                # on any exit node country (not just GB)
                set_shipping_to_poland(page)

                # EXTRACT TITLE
                title = extract_title_universal(page)

                # EXTRACT STORE INFO
                store_info = extract_store_info_universal(page)

                # EXTRACT COMPLIANCE INFO
                # Works because we set Ship To = Poland above (EU = compliance visible)
                compliance_info = extract_compliance_info(page)

                # EXTRACT DESCRIPTION (text + images)
                description_text, description_images = extract_description(page)

                # SUCCESS
                browser.close()

                result = {
                    "title":            title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images":           description_images if isinstance(description_images, list) else [],
                    "store_info":       store_info if isinstance(store_info, dict) else {},
                    "compliance_info":  compliance_info if isinstance(compliance_info, dict) else {},
                }

                print(f"\n🔍 DEBUG RETURN VALUES:")
                print(f"   title:            {len(result['title'])} chars")
                print(f"   description_text: {len(result['description_text'])} chars")
                print(f"   images:           {len(result['images'])} images")
                print(f"   store_info:       {result['store_info']}")
                print(f"   compliance_info:  {result['compliance_info']}")
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
