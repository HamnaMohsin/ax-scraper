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
            time.sleep(5)  # Give Tor time to build new circuit
        print("✅ Tor circuit rotated - new IP acquired")
        return True
    except Exception as e:
        print(f"⚠️ Could not rotate Tor circuit: {e}")
        return False


def is_captcha_page(page) -> bool:
    """Detect if page is a CAPTCHA/block page"""
    page_url = page.url.lower()
    page_title = page.title().lower()
    
    # Check URL for block page indicators
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
        print("❌ CAPTCHA detected in URL")
        return True
    
    # Check for CAPTCHA iframes
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
    
    # Check title for block page pattern (short + generic)
    is_product_page = "aliexpress" in page_title and len(page_title) > 40
    if not is_product_page and any(kw in page_title for kw in ["verify", "access", "denied", "blocked"]):
        print("❌ Block page detected from title")
        return True
    
    return False


def extract_store_info_universal(page) -> dict:
    """Extract store info - works for both .com and .us domains"""
    store_info = {}
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Method 1: Popover
        popover = soup.find('div', class_='comet-v2-popover-wrap')
        if popover:
            table = popover.find("table")
            if table:
                for row in table.find_all("tr"):
                    cols = row.find_all("td")
                    if len(cols) == 2:
                        key = clean_text(cols[0].get_text()).replace(":", "").strip()
                        value = clean_text(cols[1].get_text()).strip()
                        if key:
                            store_info[key] = value
        
        # Method 2: Direct search in HTML for store data
        if not store_info:
            # Look for store name patterns
            store_patterns = [
                r'Store no[.:]?\s*(\d+)',
                r'store-num["\']?\s*:\s*["\']?(\d+)',
                r'"storeNum":\s*(\d+)',
            ]
            
            for pattern in store_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    store_info['Store no.'] = match.group(1)
        
        # Method 3: Any table with store-like content
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
                
                # Check if looks like store info
                if temp_info and any(k.lower() in str(temp_info).lower() 
                                    for k in ['store', 'location', 'name', 'seller']):
                    store_info = temp_info
                    break
        
        if store_info:
            print(f"✅ Store info: {store_info}")
            
    except Exception as e:
        print(f"⚠️ Store info extraction error: {e}")
    
    return store_info


def extract_title_universal(page) -> str:
    """Extract title - works for both .com and .us domains"""
    
    # Try multiple selectors
    selectors_to_try = [
        ('[data-pl="product-title"]', "product-title"),
        ('h1[class*="title"]', "h1 with title class"),
        ('h1', "generic h1"),
        ('[class*="product-name"]', "product-name"),
        ('[class*="ProductTitle"]', "ProductTitle"),
        ('span[class*="Title"]', "span Title"),
        ('[class*="title"]', "generic title"),
    ]
    
    for selector, description in selectors_to_try:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 10 and title.lower() != "aliexpress":
                    print(f"✅ Title ({description}): {title[:80]}...")
                    return title
        except:
            continue
    
    # Fallback: Parse HTML for any reasonable title
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Look for title tag first
        title_elem = soup.find('title')
        if title_elem:
            text = title_elem.get_text().strip()
            if text and len(text) > 10 and 'aliexpress' in text.lower():
                print(f"✅ Title (from <title>): {text[:80]}...")
                return text
        
        # Look for og:title meta tag
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title:
            text = og_title.get('content', '').strip()
            if text and len(text) > 10:
                print(f"✅ Title (from og:title): {text[:80]}...")
                return text
        
        # Find by searching for elements with product-like text
        for tag in soup.find_all(['h1', 'h2', 'span', 'div']):
            text = tag.get_text().strip()
            if (text and 
                20 < len(text) < 300 and 
                text.lower() != "aliexpress" and
                not text.startswith("http")):
                # Check if looks like a product title (has some special chars or multiple words)
                if any(char in text for char in ['$', '€', '¥', '"', "'"]) or len(text.split()) > 3:
                    print(f"✅ Title (HTML scan): {text[:80]}...")
                    return text
    except:
        pass
    
    print("⚠️ Could not extract title")
    return ""


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data with Tor routing and anti-detection.
    Handles redirects, CAPTCHAs, and both .com and .us domains.
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
        
        # Rotate Tor circuit on retry
        if attempt > 0:
            print("🔄 Rotating Tor circuit...")
            rotate_tor_circuit()
            random_delay(8, 15)
        
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},  # Tor SOCKS5
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
            
            # Hide automation signals
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]})")
            
            try:
                # =====================
                # NAVIGATION
                # =====================
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")
                time.sleep(2)
                
                current_url = page.url
                if current_url != url:
                    print(f"⚠️ Redirected to: {current_url}")
                
                # =====================
                # CAPTCHA CHECK (early)
                # =====================
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # Wait for page to load with explicit content checks
                print("⏳ Waiting for content to render...")
                max_wait = 15
                start_time = time.time()
                content_loaded = False
                
                while time.time() - start_time < max_wait:
                    try:
                        # Check if product content has loaded
                        html = page.content()
                        if "alicdn" in html and len(html) > 100000:  # Product page is large
                            content_loaded = True
                            break
                    except:
                        pass
                    time.sleep(0.5)
                
                if not content_loaded:
                    print("⚠️ Content may not have fully loaded, continuing anyway...")
                
                # =====================
                # SCROLL TO TRIGGER LAZY LOADING
                # =====================
                print("⏳ Scrolling to load images...")
                try:
                    for _ in range(5):
                        page.mouse.wheel(0, random.randint(200, 400))
                        page.wait_for_timeout(random.randint(300, 800))
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"⚠️ Scroll error: {e}")
                
                # =====================
                # SECOND CAPTCHA CHECK
                # =====================
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # =====================
                # EXTRACT TITLE
                # =====================
                title = extract_title_universal(page)
                
                # =====================
                # EXTRACT STORE INFO
                # =====================
                store_info = extract_store_info_universal(page)
                
                # =====================
                # EXTRACT DESCRIPTION
                # =====================
                print("📝 Loading description...")
                description_text = ""
                
                try:
                    # Try to click description tab if it exists
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                    
                    # Look for description tab and click
                    desc_tab_selectors = [
                        'div[class*="description"]',
                        'button[class*="description"]',
                        '[data-spm*="description"]',
                    ]
                    
                    for selector in desc_tab_selectors:
                        try:
                            elem = page.locator(selector).first
                            if elem.count() > 0:
                                elem.click(force=True, timeout=2000)
                                page.wait_for_timeout(2000)
                                break
                        except:
                            continue
                    
                    # Extract description from multiple places
                    desc_selectors = [
                        "#product-description",
                        '[class*="description"]',
                        '[id*="description"]',
                        '[class*="detail"]',
                        '[class*="Description"]',
                    ]
                    
                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    # Method 1: Find description divs
                    for selector in desc_selectors:
                        for elem in soup.select(selector):
                            text = elem.get_text(" ", strip=True)
                            if len(text) > 200:  # Real description
                                print(f"✅ Description: {len(text)} chars")
                                description_text = text
                                break
                        if description_text:
                            break
                    
                    # Method 2: Look for text blocks with product keywords
                    if not description_text:
                        for div in soup.find_all('div'):
                            text = div.get_text(" ", strip=True)
                            if (len(text) > 300 and 
                                any(kw in text.lower() for kw in 
                                    ['feature', 'spec', 'parameter', 'include', 'material', 'size'])):
                                print(f"✅ Description (keyword match): {len(text)} chars")
                                description_text = text
                                break
                            
                except Exception as e:
                    print(f"⚠️ Description extraction: {e}")
                
                # =====================
                # EXTRACT IMAGES (comprehensive)
                # =====================
                print("🖼️ Extracting images...")
                description_images = []
                
                try:
                    # From page content HTML
                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    # Method 1: Direct img tags
                    for img in soup.find_all('img'):
                        src = img.get('src') or img.get('data-src') or img.get('data-original')
                        if src and isinstance(src, str) and "alicdn" in src and len(src) > 50:
                            if not any(x in src.lower() for x in ["50x50", "icon", "logo", "avatar"]):
                                description_images.append(src)
                    
                    # Method 2: Picture tags (responsive images)
                    for picture in soup.find_all('picture'):
                        for source in picture.find_all('source'):
                            srcset = source.get('srcset')
                            if srcset and "alicdn" in srcset:
                                # Extract first image from srcset
                                img_url = srcset.split()[0].strip(',')
                                if img_url and len(img_url) > 50:
                                    description_images.append(img_url)
                        # Also check img inside picture
                        img = picture.find('img')
                        if img:
                            src = img.get('src') or img.get('data-src')
                            if src and "alicdn" in src and len(src) > 50:
                                description_images.append(src)
                    
                    # Method 3: Background images in style
                    for elem in soup.find_all(style=True):
                        style = elem.get('style', '')
                        urls = re.findall(r'url\([\'"]?(.*?)[\'"]?\)', style)
                        for url in urls:
                            if "alicdn" in url and len(url) > 50:
                                description_images.append(url)
                    
                    # Method 4: Data attributes
                    for elem in soup.find_all(attrs={"data-src": True}):
                        src = elem.get('data-src')
                        if src and "alicdn" in src and len(src) > 50:
                            description_images.append(src)
                    
                except Exception as e:
                    print(f"⚠️ Image extraction error: {e}")
                
                # Remove duplicates and limit
                description_images = list(set(description_images))[:20]
                print(f"✅ Images: {len(description_images)}")
                
                # =====================
                # SUCCESS - Return result
                # =====================
                browser.close()
                
                result = {
                    "title": clean_text(title),
                    "description_text": clean_text(description_text),
                    "images": description_images,
                    "store_info": store_info
                }
                
                print(f"✅ Extraction successful on attempt {attempt + 1}")
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
