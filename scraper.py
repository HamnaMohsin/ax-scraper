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
    """Rotate Tor circuit"""
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
    """Detect CAPTCHA"""
    page_url = page.url.lower()
    captcha_url_keywords = ["baxia", "punish", "captcha", "verify"]
    if any(kw in page_url for kw in captcha_url_keywords):
        return True
    
    captcha_selectors = [
        "iframe[src*='recaptcha']",
        ".baxia-punish",
        "#captcha-verify",
        "iframe[src*='geetest']",
    ]
    
    for selector in captcha_selectors:
        try:
            if page.locator(selector).count() > 0:
                return True
        except:
            continue
    
    return False


def extract_shadow_dom_text(html_str: str) -> str:
    """
    Extract text from template/shadow DOM elements
    This gets the detailed specs that are hidden in template tags
    """
    print("   🔍 Checking for Shadow DOM content...")
    
    # Parse the HTML
    soup = BeautifulSoup(html_str, "html.parser")
    
    # Find template elements with shadowrootmode
    templates = soup.find_all('template', attrs={'shadowrootmode': 'open'})
    print(f"      Found {len(templates)} shadow DOM template(s)")
    
    shadow_content = []
    
    for i, template in enumerate(templates):
        # Get the content inside the template
        template_html = str(template)
        
        # Parse the template content
        # The content is inside the <template> tag
        if template.string:
            # Extract text from template
            pass
        
        # Try to find content within the template tag
        # BeautifulSoup includes template contents as children
        for child in template.children:
            if isinstance(child, str):
                continue
            
            # Get text from elements inside template
            text = child.get_text(separator='\n', strip=True)
            if len(text) > 50:
                shadow_content.append(text)
                print(f"      Template #{i+1}: {len(text)} chars - {text[:60]}...")
    
    return "\n\n".join(shadow_content) if shadow_content else ""


def extract_description_sections(html_str: str) -> dict:
    """
    Extract description from different sections, filtering out irrelevant content
    Returns dict with different content types
    """
    print("📝 Extracting description sections...")
    
    soup = BeautifulSoup(html_str, "html.parser")
    
    sections = {
        'shadow_dom': "",
        'rich_text': "",
        'detail_specs': ""
    }
    
    # SECTION 1: Shadow DOM content (detailed specs)
    print("   Section 1: Shadow DOM / Template content...")
    templates = soup.find_all('template', attrs={'shadowrootmode': 'open'})
    
    for template in templates:
        # Find the inner product-description div
        inner_desc = template.find('div', id='product-description')
        if inner_desc:
            text = inner_desc.get_text(separator='\n', strip=True)
            if len(text) > 50:
                sections['shadow_dom'] = text
                print(f"      ✓ Got {len(text)} chars from shadow DOM")
    
    # SECTION 2: Detail specs (detail-desc-decorate-richtext)
    print("   Section 2: Detail specs...")
    detail_descs = soup.find_all('div', class_=lambda x: x and 'detail-desc-decorate' in x)
    
    for detail in detail_descs:
        # Only get from the first level, not nested
        text = detail.get_text(separator='\n', strip=True)
        if len(text) > 50:
            sections['detail_specs'] = text
            print(f"      ✓ Got {len(text)} chars from detail specs")
            break
    
    # SECTION 3: Rich text container (but NOT including page junk)
    print("   Section 3: Rich text container...")
    
    # Find richTextContainer divs
    rich_containers = soup.find_all('div', class_=lambda x: x and 'richTextContainer' in x)
    print(f"      Found {len(rich_containers)} richTextContainer(s)")
    
    for container in rich_containers:
        text = container.get_text(separator='\n', strip=True)
        
        # Filter out page junk
        # Remove if it contains too much pricing/review info
        lines = text.split('\n')
        
        # Check if this looks like junk
        is_junk = False
        junk_indicators = [
            'Positive Feedback',
            'Free shipping',
            'Delivery:',
            'Refund if',
            'Safe payments',
            'Quantity',
            'Buy now',
            'Add to cart',
            'Similar items',
            'People also searched',
            'Help Center',
            'Return&refund',
            'Alibaba Group'
        ]
        
        for indicator in junk_indicators:
            if indicator in text:
                is_junk = True
                break
        
        if not is_junk and len(text) > 200:
            sections['rich_text'] = text
            print(f"      ✓ Got {len(text)} chars (filtered)")
            break
        elif len(text) > 200:
            print(f"      ✗ Skipped {len(text)} chars (detected as junk/page content)")
    
    return sections


def extract_description_intelligent(page) -> str:
    """
    Intelligently extract description from all relevant sources
    Combines shadow DOM + rich text + detail specs
    Filters out page noise
    """
    print("📝 Extracting COMPLETE description...")
    
    all_content = []
    
    try:
        # Wait for content to render
        print("   ⏳ Waiting for page content...")
        page.wait_for_timeout(3000)
        
        # Get page source
        html = page.content()
        
        # Extract from different sections
        sections = extract_description_sections(html)
        
        # Combine in order of priority
        # 1. Shadow DOM content first (most detailed specs)
        if sections['shadow_dom']:
            all_content.append(sections['shadow_dom'])
            print(f"   ✓ Shadow DOM: {len(sections['shadow_dom'])} chars")
        
        # 2. Detail specs
        if sections['detail_specs']:
            all_content.append(sections['detail_specs'])
            print(f"   ✓ Detail specs: {len(sections['detail_specs'])} chars")
        
        # 3. Rich text (full descriptions)
        if sections['rich_text']:
            all_content.append(sections['rich_text'])
            print(f"   ✓ Rich text: {len(sections['rich_text'])} chars")
        
        if all_content:
            # Combine all sections
            combined = "\n\n".join(all_content)
            
            # Clean up
            combined = re.sub(r'\n\s*\n+', '\n\n', combined)
            combined = re.sub(r'[ \t]+', ' ', combined)
            combined = combined.strip()
            
            # Verify we got good content
            print(f"   📊 Combined: {len(combined)} chars")
            
            if 'keep hot' in combined.lower():
                print("   ✅ Includes temperature specs")
            if 'material' in combined.lower():
                print("   ✅ Includes material specs")
            
            return combined
        else:
            print("   ⚠️ No description content found")
            return ""
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return ""


def extract_description_images(page) -> list:
    """Extract ONLY description images (not product thumbnails)"""
    print("🖼️ Extracting description images...")
    
    images = []
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Look for images in product-description divs specifically
        print("   🔍 Looking for images in description sections...")
        
        # Images in templates
        templates = soup.find_all('template', attrs={'shadowrootmode': 'open'})
        for template in templates:
            inner_desc = template.find('div', id='product-description')
            if inner_desc:
                imgs = inner_desc.find_all('img')
                for img in imgs:
                    src = img.get('src') or img.get('data-src')
                    if src and 'alicdn.com' in src:
                        clean_src = src.split('?')[0]
                        if len(clean_src) > 50 and clean_src not in images:
                            images.append(clean_src)
        
        print(f"      Found {len(images)} from shadow DOM")
        
        # Images in richTextContainer
        rich_containers = soup.find_all('div', class_=lambda x: x and 'richTextContainer' in x)
        for container in rich_containers:
            imgs = container.find_all('img')
            for img in imgs:
                src = img.get('src') or img.get('data-src')
                if src and 'alicdn.com' in src:
                    clean_src = src.split('?')[0]
                    if len(clean_src) > 50 and clean_src not in images:
                        images.append(clean_src)
        
        print(f"      Total: {len(images)} description images")
        
        # Limit to 20
        images = images[:20]
        
        return images
    
    except Exception as e:
        print(f"⚠️ Image error: {e}")
        return []


def extract_title_universal(page) -> str:
    """Extract title"""
    
    print("📌 Extracting title...")
    
    title_selectors = [
        ('[data-pl="product-title"]', "data-pl"),
        ('h1', "h1"),
        ('[class*="product-title"]', "class"),
        ('span[class*="title"]', "span"),
    ]
    
    for selector, desc in title_selectors:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 5:
                    print(f"✅ Title: {title[:80]}...")
                    return title
        except:
            continue
    
    return ""


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product - Shadow DOM aware
    Separates description content from page noise
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
            print(f"   Waiting {wait_time}s...")
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
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                timezone_id='America/New_York'
            )
            
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            try:
                # LOAD
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")
                time.sleep(2)
                
                # CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA - retrying...")
                    browser.close()
                    continue
                
                # WAIT
                print("⏳ Rendering...")
                time.sleep(8)
                
                # SCROLL
                print("⏳ Scrolling...")
                for _ in range(5):
                    page.mouse.wheel(0, random.randint(150, 300))
                    time.sleep(0.3)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)
                
                # SECOND CAPTCHA CHECK
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA - retrying...")
                    browser.close()
                    continue
                
                # EXTRACT
                print("\n--- EXTRACTING DATA ---")
                title = extract_title_universal(page)
                description_text = extract_description_intelligent(page)
                images = extract_description_images(page)
                
                browser.close()
                
                # RESULTS
                result = {
                    "title": title,
                    "description_text": description_text,
                    "images": images,
                    "store_info": {}
                }
                
                print(f"\n✅ SUCCESS:")
                print(f"   Title: {len(title)} chars")
                print(f"   Description: {len(description_text)} chars")
                print(f"   Images: {len(images)}")
                
                return result
                
            except PlaywrightTimeoutError as e:
                print(f"⚠️ Timeout: {e}")
                browser.close()
                continue
                
            except Exception as e:
                print(f"❌ Error: {e}")
                import traceback
                traceback.print_exc()
                try:
                    browser.close()
                except:
                    pass
                continue
    
    print("❌ Failed")
    return empty_result
