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
    """Detect if page is a CAPTCHA/block page"""
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
    """Extract store info"""
    store_info = {}
    print("📦 Extracting store info...")
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
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
        
        if not store_info:
            print("   ⚠️ Could not extract store information")
            
    except Exception as e:
        print(f"⚠️ Store extraction error: {e}")
    
    return store_info


def extract_title_universal(page) -> str:
    """Extract title"""
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


def wait_for_description_tab_and_click(page):
    """
    Find and click the Description tab to ensure content loads.
    Waits for the tab to be clickable and then clicks it.
    """
    print("   🔍 Looking for Description tab...")
    
    # Multiple possible selectors for description tab
    tab_selectors = [
        ('text=Description', 'text=Description'),
        ('a:has-text("Description")', 'a:has-text("Description")'),
        ('[role="tab"]:has-text("Description")', '[role="tab"]:has-text("Description")'),
        ('button:has-text("Description")', 'button:has-text("Description")'),
        ('.comet-v2-tabs-content-tab:has-text("Description")', 'comet-v2-tabs-content-tab'),
    ]
    
    for selector, desc in tab_selectors:
        try:
            tab = page.locator(selector).first
            if tab.count() > 0:
                print(f"   ✓ Found Description tab ({desc})")
                try:
                    tab.click(timeout=3000, force=True)
                    print(f"   ✓ Clicked Description tab")
                    # Wait for content to load
                    page.wait_for_timeout(2000)
                    return True
                except:
                    continue
        except:
            continue
    
    print("   ⚠️ Could not find/click Description tab (might already be active)")
    return False


def scroll_description_into_view(page):
    """Scroll description section into view to trigger lazy loading"""
    print("   ⏳ Scrolling description into view...")
    try:
        page.locator('#product-description').first.scroll_into_view()
        page.wait_for_timeout(2000)
        print("   ✓ Scrolled into view")
        return True
    except Exception as e:
        print(f"   ⚠️ Scroll error: {e}")
        return False


def extract_from_shadow_dom_js(page) -> tuple:
    """
    Extract description and images directly from shadow DOM using JavaScript.
    This is the MOST RELIABLE method.
    """
    print("   🔍 Method 1: Extracting from Shadow DOM (JavaScript)...")
    
    try:
        # JavaScript to extract shadow DOM content
        result = page.evaluate("""
            () => {
                try {
                    // Find template
                    const template = document.querySelector('#product-description > template');
                    if (!template) {
                        return { success: false, error: 'No template found' };
                    }
                    
                    // Get shadow root
                    const shadowRoot = template.shadowRoot;
                    if (!shadowRoot) {
                        return { success: false, error: 'No shadowRoot' };
                    }
                    
                    // Find product-description div in shadow
                    const descDiv = shadowRoot.querySelector('.product-description');
                    if (!descDiv) {
                        return { success: false, error: 'No .product-description in shadow' };
                    }
                    
                    // Extract text content
                    const textContent = descDiv.innerText || descDiv.textContent;
                    
                    // Extract all images
                    const images = [];
                    const imgElements = descDiv.querySelectorAll('img');
                    imgElements.forEach(img => {
                        const src = img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src');
                        if (src && (src.includes('alicdn.com') || src.includes('ae01.alicdn.com'))) {
                            const cleanSrc = src.split('?')[0];
                            if (!images.includes(cleanSrc)) {
                                images.push(cleanSrc);
                            }
                        }
                    });
                    
                    // Also get HTML for parsing
                    const htmlContent = descDiv.innerHTML;
                    
                    return { 
                        success: true, 
                        text: textContent, 
                        html: htmlContent,
                        images: images,
                        imgCount: imgElements.length
                    };
                } catch (e) {
                    return { success: false, error: e.toString() };
                }
            }
        """)
        
        if not result:
            print("   ⚠️ JavaScript returned null")
            return "", []
        
        if not result.get('success', False):
            print(f"   ⚠️ Shadow DOM extraction failed: {result.get('error', 'Unknown error')}")
            return "", []
        
        text = result.get('text', '').strip()
        images = result.get('images', [])
        html = result.get('html', '')
        img_count = result.get('imgCount', 0)
        
        print(f"   ✓ JavaScript successful:")
        print(f"      Text: {len(text)} chars")
        print(f"      Images found: {img_count} (extracted: {len(images)})")
        
        # Clean text
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Parse HTML for any missed images
        if html and len(html) > 100:
            soup = BeautifulSoup(html, 'html.parser')
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if src and ('alicdn.com' in src or 'ae01.alicdn.com' in src):
                    clean_src = src.split('?')[0]
                    if clean_src not in images and len(clean_src) > 50:
                        images.append(clean_src)
        
        # Filter images
        quality_images = [img for img in images if len(img) > 50]
        quality_images = quality_images[:20]  # Limit to 20
        
        print(f"   ✓ Final: {len(text)} chars text, {len(quality_images)} quality images")
        
        return text, quality_images
        
    except Exception as e:
        print(f"   ❌ Shadow DOM JS extraction failed: {e}")
        import traceback
        traceback.print_exc()
        return "", []


def extract_from_richtext_dom(page) -> tuple:
    """
    Extract from richTextContainer (fallback).
    """
    print("   🔍 Method 2: Extracting from richTextContainer (fallback)...")
    
    try:
        # Get full page HTML
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        # Find all richTextContainer divs
        rich_elems = soup.find_all('div', class_='richTextContainer')
        print(f"   Found {len(rich_elems)} richTextContainer elements")
        
        if not rich_elems:
            print("   ⚠️ No richTextContainer found")
            return "", []
        
        text_parts = []
        images = []
        
        for idx, elem in enumerate(rich_elems):
            # Extract text
            text = elem.get_text(' ', strip=True)
            if text and len(text) > 20:
                text_parts.append(text)
                print(f"      Container {idx}: {len(text)} chars")
            
            # Extract images
            for img in elem.find_all('img'):
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if src and ('alicdn.com' in src or 'ae01.alicdn.com' in src):
                    clean_src = src.split('?')[0]
                    if clean_src not in images:
                        images.append(clean_src)
        
        # Combine text
        full_text = ' '.join(text_parts)
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        
        # Filter images
        quality_images = [img for img in images if len(img) > 50]
        quality_images = quality_images[:20]
        
        print(f"   ✓ richTextContainer: {len(full_text)} chars text, {len(quality_images)} images")
        
        return full_text, quality_images
        
    except Exception as e:
        print(f"   ❌ richTextContainer extraction failed: {e}")
        return "", []


def extract_description_universal(page) -> tuple:
    """
    Extract description using dual strategy:
    1. Shadow DOM via JavaScript (primary - most complete)
    2. richTextContainer (fallback)
    """
    print("📝 Extracting description and images...")
    
    try:
        # Step 1: Try to click description tab if exists
        wait_for_description_tab_and_click(page)
        
        # Step 2: Scroll into view
        scroll_description_into_view(page)
        
        # Step 3: Wait for shadow DOM to load
        print("   ⏳ Waiting for shadow DOM to render...")
        page.wait_for_timeout(3000)
        
        # Step 4: Try Shadow DOM first
        shadow_text, shadow_images = extract_from_shadow_dom_js(page)
        
        if shadow_text and len(shadow_text) > 100:
            print("   ✅ Using Shadow DOM content")
            return shadow_text, shadow_images
        
        if shadow_text:
            print(f"   ⚠️ Shadow text too short ({len(shadow_text)} chars), trying fallback...")
        
        # Step 5: Fallback to richTextContainer
        rich_text, rich_images = extract_from_richtext_dom(page)
        
        if rich_text and len(rich_text) > 100:
            print("   ✅ Using richTextContainer content")
            return rich_text, rich_images
        
        # Step 6: Combine if both have partial content
        if shadow_text or rich_text:
            print("   ⚠️ Combining partial content from both methods")
            combined_text = (shadow_text + ' ' + rich_text).strip()
            combined_text = re.sub(r'\s+', ' ', combined_text)
            combined_images = list(set(shadow_images + rich_images))
            return combined_text, combined_images
        
        print("   ❌ Could not extract description from either method")
        return "", []
        
    except Exception as e:
        print(f"⚠️ Description extraction error: {e}")
        import traceback
        traceback.print_exc()
        return "", []


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
                
                # CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - rotating IP and retrying...")
                    browser.close()
                    continue
                
                # Wait for page to load
                print("⏳ Waiting for page to render...")
                time.sleep(8)
                
                # SCROLL
                print("⏳ Scrolling to load content...")
                try:
                    for _ in range(3):
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
                description_text, description_images = extract_description_universal(page)
                
                # SUCCESS
                browser.close()
                
                result = {
                    "title": title if isinstance(title, str) else "",
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images": description_images if isinstance(description_images, list) else [],
                    "store_info": store_info if isinstance(store_info, dict) else {}
                }
                
                print(f"\n✅ EXTRACTION COMPLETE:")
                print(f"   Title: {len(result['title'])} chars")
                print(f"   Description: {len(result['description_text'])} chars")
                print(f"   Images: {len(result['images'])}")
                print(f"   Store Info: {len(result['store_info'])} fields")
                print(f"✅ Success on attempt {attempt + 1}\n")
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
