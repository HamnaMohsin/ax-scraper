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
    # ... (keep existing setup code the same until description extraction)
    
    # REPLACE the entire description extraction section with this:
    
    # EXTRACT DESCRIPTION - IMPROVED VERSION
    print("📝 Loading description...")
    description_text = ""
    description_images = []
    description_element = None
    
    try:
        # 1. FIRST: Try to click description tab (existing code)
        print("   Clicking description tab...")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        
        desc_tab_selectors = [
            'button:has-text("Description")',
            'div[role="tab"]:has-text("Description")',
            'a:has-text("Description")',
            '[class*="tab"]:has-text("Description")',
            '.product-tab:has-text("Description")',
        ]
        
        tab_clicked = False
        for tab_selector in desc_tab_selectors:
            try:
                tab = page.locator(tab_selector).first
                if tab.count() > 0:
                    tab.click(force=True, timeout=3000)
                    page.wait_for_timeout(2000)
                    print(f"   ✓ Clicked description tab: {tab_selector}")
                    tab_clicked = True
                    break
            except:
                continue
        
        # 2. WAIT LONGER for description to load
        print("   ⏳ Waiting for description content...")
        page.wait_for_timeout(3000)
        
        # 3. NEW: Scroll to description area
        try:
            page.evaluate("""
                window.scrollTo(0, document.body.scrollHeight * 0.4);
            """)
            page.wait_for_timeout(1000)
        except:
            pass
        
        # 4. NEW: Use Playwright to find description (more reliable than soup)
        print("   🔍 Searching for description content...")
        desc_selectors_playwright = [
            '[data-spm*="description"]',
            '.product-detail__description',
            '[class*="description-content"]',
            '[class*="product-description"]',
            '#product-description',
            '.detail-content',
            '.product-detail-tab__content',
            'div[role="tabpanel"]:has-text("Description")',
        ]
        
        for selector in desc_selectors_playwright:
            try:
                elements = page.locator(selector).all()
                for elem in elements:
                    if elem.count() > 0:
                        # Get text content
                        text = elem.inner_text().strip()
                        text_len = len(text)
                        
                        # Better filtering: longer text, product keywords, no nav/UI text
                        if (text_len > 500 and text_len < 10000 and 
                            any(kw in text.lower() for kw in 
                                ['material', 'size', 'color', 'feature', 'package', 
                                 'specification', 'weight', 'battery', 'waterproof', 
                                 'dimension', 'function', 'usage']) and
                            not any(bad in text.lower() for bad in 
                                   ['aliexpress', 'shopping cart', 'add to cart', 
                                    'reviews', 'ratings', 'seller info', 'shipping', 
                                    'welcome', 'sign in', 'USD', 'other/en/'])):
                            
                            print(f"✅ DESCRIPTION FOUND ({selector}): {text_len} chars")
                            print(f"   Preview: {text[:150]}...")
                            description_text = clean_text(text)
                            description_element = elem
                            break
                            
            except Exception as e:
                print(f"   ⚠️ Selector {selector} failed: {e}")
                continue
            
            if description_text:
                break
        
        # 5. FALLBACK: Look for largest text block with product keywords
        if not description_text:
            print("   🔍 Fallback: Finding largest product text block...")
            page_content = page.content()
            soup = BeautifulSoup(page_content, "html.parser")
            
            candidates = []
            for div in soup.find_all(['div', 'section'], limit=50):
                text = clean_text(div.get_text())
                if (len(text) > 800 and len(text) < 15000 and
                    any(kw in text.lower() for kw in 
                        ['material', 'size', 'color', 'feature', 'package', 
                         'specification', 'weight', 'function'])):
                    candidates.append((len(text), text, div))
            
            if candidates:
                candidates.sort(reverse=True)
                description_text = candidates[0][1]
                description_element = candidates[0][2]
                print(f"✅ Fallback description: {len(description_text)} chars")
                print(f"   Preview: {description_text[:150]}...")
        
        # 6. IMPROVED IMAGE EXTRACTION
        print("🖼️ Extracting product images...")
        
        # Get ALL product images (not just description)
        product_image_selectors = [
            'img[data-src*="alicdn"]',
            'img[src*="alicdn"]',
            '.product-image img',
            '[class*="gallery"] img',
            '.swiper img',
        ]
        
        all_images = []
        for img_selector in product_image_selectors:
            try:
                imgs = page.locator(img_selector).all()
                for img in imgs:
                    if img.count() > 0:
                        src = img.get_attribute('src') or img.get_attribute('data-src')
                        if src and 'alicdn' in src and len(src) > 50:
                            # Replace small thumbnails with full size
                            src = src.replace('_60x60', '_800x800').replace('_100x100', '_800x800')
                            if not any(x in src.lower() for x in ['icon', 'logo', 'avatar']):
                                all_images.append(src)
            except:
                continue
        
        # Also get images from description if we have it
        if description_element:
            try:
                desc_html = description_element.inner_html()
                desc_soup = BeautifulSoup(desc_html, "html.parser")
                for img in desc_soup.find_all('img'):
                    src = img.get('src') or img.get('data-src')
                    if src and 'alicdn' in src:
                        src = src.replace('_60x60', '_800x800').replace('_100x100', '_800x800')
                        all_images.append(src)
            except:
                pass
        
        # Dedupe and limit
        description_images = list(set(all_images))[:15]
        print(f"✅ Found {len(description_images)} product images")
        
    except Exception as e:
        print(f"⚠️ Description extraction error: {e}")
    
    # ... rest of function stays the same

