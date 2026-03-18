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
    """Rotate Tor circuit to get new exit IP"""
    try:
        with Controller.from_port(port=9051) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            time.sleep(5)
        print("✅ Tor circuit rotated - new IP acquired")
        return True
    except Exception as e:
        print(f"⚠️ Could not rotate Tor circuit: {e}")
        return False


def is_captcha_page(page) -> bool:
    """Detect if page is a CAPTCHA/block page"""
    page_url = page.url.lower()
    page_title = page.title().lower()
    
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
        print("❌ CAPTCHA detected in URL")
        return True
    
    captcha_selectors = [
        "iframe[src*='recaptcha']",
        ".baxia-punish",
        "#captcha-verify",
        "[id*='captcha']",
    ]
    
    for selector in captcha_selectors:
        try:
            if page.locator(selector).count() > 0:
                print(f"❌ CAPTCHA detected: {selector}")
                return True
        except:
            continue
    
    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    if not is_product_page and any(kw in page_title for kw in ["verify", "access", "denied", "blocked"]):
        print("❌ Block page detected from title")
        return True
    
    return False


def extract_store_info_universal(page) -> dict:
    """Extract store info using exact selectors"""
    store_info = {}
    
    print("📦 Extracting store info...")
    
    # Exact selectors provided
    store_selectors = [
        '[data-spm*="store"]',
        '[class*="store"]',
        'a[href*="store"]',
        '[class*="seller"]',
        'span:has-text("Store info")',
        'div.store-detail--storeTitle--isySny7'
    ]
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Try each selector
        for selector in store_selectors:
            try:
                # Use Playwright locator first
                elem = page.locator(selector).first
                if elem.count() > 0:
                    elem_html = elem.inner_html()
                    # Look for table in or around this element
                    elem_soup = BeautifulSoup(elem_html, "html.parser")
                    table = elem_soup.find("table")
                    if table:
                        for row in table.find_all("tr"):
                            cols = row.find_all("td")
                            if len(cols) == 2:
                                key = clean_text(cols[0].get_text()).replace(":", "").strip()
                                value = clean_text(cols[1].get_text()).strip()
                                if key:
                                    store_info[key] = value
                        if store_info:
                            print(f"✅ Store info found with selector: {selector}")
                            return store_info
            except:
                continue
        
        # Fallback: Look for any table with store data in HTML
        if not store_info:
            for table in soup.find_all('table'):
                temp_info = {}
                for row in table.find_all("tr"):
                    cols = row.find_all("td")
                    if len(cols) == 2:
                        key = clean_text(cols[0].get_text()).replace(":", "").strip()
                        value = clean_text(cols[1].get_text()).strip()
                        if key and value:
                            temp_info[key] = value
                
                if temp_info and any(k.lower() in str(temp_info).lower() 
                                    for k in ['store', 'location', 'name', 'seller']):
                    store_info = temp_info
                    print(f"✅ Store info found from table search")
                    break
        
        if store_info:
            print(f"   Data: {store_info}")
            
    except Exception as e:
        print(f"⚠️ Store info extraction error: {e}")
    
    return store_info


def extract_title_universal(page) -> str:
    """Extract title using exact selector [data-pl='product-title']"""
    
    print("📌 Extracting title...")
    
    # EXACT selector provided
    selector = '[data-pl="product-title"]'
    try:
        elem = page.locator(selector).first
        if elem.count() > 0:
            title = elem.inner_text().strip()
            if title and len(title) > 10:
                print(f"✅ Title from {selector}: {title}")
                # DEBUG: Show what we got
                print(f"🔍 DEBUG: Raw title length: {len(title)}, ends with: ...{title[-50:]}")
                return title
    except Exception as e:
        print(f"⚠️ Selector {selector} failed: {e}")
    
    # Fallback selectors
    fallback_selectors = [
        ('h1', "h1 heading"),
        ('[class*="product-name"]', "product-name"),
        ('[class*="ProductTitle"]', "ProductTitle"),
    ]
    
    for selector, description in fallback_selectors:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 10:
                    print(f"✅ Title ({description}): {title}")
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
            random_delay(8, 15)
        
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
                
                # CAPTCHA CHECK (early)
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # SIMPLE WAIT - don't poll content repeatedly!
                print("⏳ Waiting for page to render...")
                time.sleep(8)
                
                # NATURAL SCROLLING
                print("⏳ Scrolling to load images...")
                try:
                    for _ in range(3):  # 3 scrolls (human-like)
                        page.mouse.wheel(0, random.randint(150, 300))
                        time.sleep(random.uniform(0.2, 0.6))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")
                
                # SECOND CAPTCHA CHECK
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
                description_element = None
                
                try:
                    # Try to click description tab to ensure it's active
                    print("   Clicking description tab...")
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                        
                        # Look for description tab button
                        desc_tab_selectors = [
                            'button:has-text("Description")',
                            'div[role="tab"]:has-text("Description")',
                            'a:has-text("Description")',
                        ]
                        
                        for tab_selector in desc_tab_selectors:
                            try:
                                tab = page.locator(tab_selector).first
                                if tab.count() > 0:
                                    tab.click(force=True, timeout=2000)
                                    page.wait_for_timeout(1500)
                                    print("   ✓ Clicked description tab")
                                    break
                            except:
                                continue
                    except:
                        pass
                    
                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    # Look for description content ONLY (not entire page)
                    # Description content is usually in a smaller container with specific patterns
                    desc_selectors = [
                        "#product-description",
                        '[class*="product-description"]',
                        '[class*="detail-content"]',
                        '.product-detail__description',
                        '[class*="description-content"]',
                        'div[data-spm*="description"]',
                    ]
                    
                    for selector in desc_selectors:
                        try:
                            elements = soup.select(selector)
                            
                            for elem in elements:
                                text = elem.get_text(" ", strip=True)
                                text_len = len(text)
                                
                                # Description should be 200-5000 chars (not 50000+)
                                # Avoid if contains review/cart keywords
                                if (200 < text_len < 5000 and 
                                    not any(x in text.lower() for x in ['add to cart', 'add to compare', 'buy now', 'reviews', 'ratings'])):
                                    print(f"🔍 Found potential description ({selector}): {text_len} chars")
                                    print(f"   Preview: {text[:100]}...")
                                    description_text = text
                                    description_element = elem
                                    break
                        except:
                            continue
                        
                        if description_text:
                            break
                    
                    # If still no description, look for actual description paragraphs
                    if not description_text:
                        print("🔍 Searching for description paragraphs...")
                        for div in soup.find_all('div'):
                            text = div.get_text(" ", strip=True)
                            # Description is usually 300-3000 chars, contains product info words, doesn't have review/cart text
                            if (300 < len(text) < 3000 and
                                any(x in text.lower() for x in ['feature', 'material', 'size', 'color', 'weight', 'waterproof', 'battery']) and
                                not any(x in text.lower() for x in ['add to cart', 'buy now', 'reviews (', 'ratings', 'seller info', 'shipping'])):
                                print(f"✅ Description found: {len(text)} chars")
                                print(f"   Preview: {text[:80]}...")
                                description_text = text
                                description_element = div
                                break
                            
                except Exception as e:
                    print(f"⚠️ Description extraction error: {e}")
                
                # EXTRACT IMAGES FROM DESCRIPTION ONLY
                print("🖼️ Extracting images from description...")
                
                try:
                    if description_element is not None:
                        # Find images only within the description element
                        for img in description_element.find_all('img'):
                            src = img.get('src') or img.get('data-src') or img.get('data-original')
                            if src and isinstance(src, str) and "alicdn" in src and len(src) > 50:
                                # Skip tiny thumbnails
                                if not any(x in src.lower() for x in ["20x20", "50x50", "icon", "logo", "avatar", "100x100"]):
                                    description_images.append(src)
                                    print(f"   Found image in description: {src[:80]}...")
                    else:
                        print("   ⚠️ No description element found, cannot extract images from description")
                    
                except Exception as e:
                    print(f"⚠️ Description image extraction error: {e}")
                
                description_images = list(set(description_images))[:20]
                print(f"✅ Images from description: {len(description_images)} found")
                
                # SUCCESS
                browser.close()
                
                # DEBUG: Show what we're returning
                print(f"\n🔍 DEBUG RETURN VALUES:")
                print(f"   title: {len(title)} chars - {title[:50] if title else 'EMPTY'}")
                print(f"   description_text: {len(description_text)} chars - {description_text[:50] if description_text else 'EMPTY'}")
                print(f"   description_images: {len(description_images)} images")
                print(f"   store_info: {store_info}")
                
                result = {
                    "title": title,  # Don't clean title
                    "description_text": description_text,  # Don't clean, already extracted as text
                    "images": description_images,
                    "store_info": store_info
                }
                
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
