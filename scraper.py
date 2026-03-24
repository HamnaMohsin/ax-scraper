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
    page_title = page.title().lower()
    
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


def analyze_html_structure(html: str) -> dict:
    """
    Analyze the actual HTML structure to understand what we're working with
    Returns info about what containers exist on this page
    """
    soup = BeautifulSoup(html, "html.parser")
    
    structure = {
        'richTextContainer_count': 0,
        'product_description_count': 0,
        'detail_desc_count': 0,
        'spec_table_count': 0,
        'description_container_count': 0,
        'has_shadow_dom': False,
        'common_description_classes': [],
        'all_divs_with_text': []
    }
    
    # Count known structures
    structure['richTextContainer_count'] = len(soup.find_all('div', class_=lambda x: x and 'richTextContainer' in x))
    structure['product_description_count'] = len(soup.find_all('div', id='product-description'))
    structure['detail_desc_count'] = len(soup.find_all('div', class_=lambda x: x and 'detail-desc-decorate' in x))
    structure['spec_table_count'] = len(soup.find_all('table'))
    
    # Check for shadow DOM
    structure['has_shadow_dom'] = bool(soup.find('template', attrs={'shadowrootmode': 'open'}))
    
    # Find divs with description-related classes
    for div in soup.find_all('div', class_=True):
        classes = div.get('class', [])
        class_str = ' '.join(classes) if isinstance(classes, list) else str(classes)
        
        if any(kw in class_str.lower() for kw in ['description', 'detail', 'product', 'spec', 'info']):
            structure['common_description_classes'].append(class_str[:80])
    
    # Get all substantial text containers
    for div in soup.find_all('div'):
        text = div.get_text(strip=True)
        if len(text) > 100 and len(text) < 5000:  # Reasonable size for description
            div_classes = div.get('class', [])
            div_id = div.get('id', '')
            structure['all_divs_with_text'].append({
                'classes': ' '.join(div_classes) if div_classes else 'none',
                'id': div_id if div_id else 'none',
                'text_length': len(text)
            })
    
    return structure


def extract_description_adaptive(page) -> tuple:
    """
    Adaptively extract description by inspecting HTML structure first
    Returns (description_text, found_sources)
    """
    print("📝 Extracting description (adaptive mode)...")
    
    all_descriptions = []
    sources_found = []
    
    try:
        # Wait for content
        print("   ⏳ Waiting for page content...")
        page.wait_for_timeout(3000)
        
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Analyze structure
        print("   🔍 Analyzing HTML structure...")
        structure = analyze_html_structure(html)
        
        print(f"   📊 Structure found:")
        print(f"      richTextContainer: {structure['richTextContainer_count']}")
        print(f"      product-description: {structure['product_description_count']}")
        print(f"      detail-desc: {structure['detail_desc_count']}")
        print(f"      tables: {structure['spec_table_count']}")
        print(f"      shadow DOM: {structure['has_shadow_dom']}")
        print(f"      divs with substantial text: {len(structure['all_divs_with_text'])}")
        
        # Strategy 1: Extract from known containers
        print("   📌 Strategy 1: Known containers...")
        
        # richTextContainer
        if structure['richTextContainer_count'] > 0:
            print(f"      Found {structure['richTextContainer_count']} richTextContainer(s)")
            for container in soup.find_all('div', class_=lambda x: x and 'richTextContainer' in x):
                text = container.get_text(separator='\n', strip=True)
                if text and len(text) > 50:
                    all_descriptions.append(text)
                    sources_found.append('richTextContainer')
        
        # product-description
        if structure['product_description_count'] > 0:
            print(f"      Found {structure['product_description_count']} product-description(s)")
            for desc in soup.find_all('div', id='product-description'):
                text = desc.get_text(separator='\n', strip=True)
                if text and len(text) > 50:
                    # Only add if not already in all_descriptions
                    if not any(text in existing for existing in all_descriptions):
                        all_descriptions.append(text)
                        sources_found.append('product-description')
        
        # detail-desc
        if structure['detail_desc_count'] > 0:
            print(f"      Found {structure['detail_desc_count']} detail-desc container(s)")
            for desc in soup.find_all('div', class_=lambda x: x and 'detail-desc-decorate' in x):
                text = desc.get_text(separator='\n', strip=True)
                if text and len(text) > 50:
                    if not any(text in existing for existing in all_descriptions):
                        all_descriptions.append(text)
                        sources_found.append('detail-desc')
        
        # Strategy 2: If nothing found, try description/detail in class names
        if not all_descriptions:
            print("   📌 Strategy 2: Class name matching...")
            
            # Look for any div with 'description' or 'detail' or 'product' in class
            for div in soup.find_all('div', class_=True):
                classes = div.get('class', [])
                class_str = ' '.join(classes) if isinstance(classes, list) else str(classes)
                
                if any(kw in class_str.lower() for kw in ['description', 'detail', 'product-info', 'spec']):
                    text = div.get_text(separator='\n', strip=True)
                    if len(text) > 100 and len(text) < 10000:
                        if not any(text in existing for existing in all_descriptions):
                            all_descriptions.append(text)
                            sources_found.append(f'div[class*={class_str[:30]}]')
                            print(f"      Found: {class_str[:50]}...")
        
        # Strategy 3: Look for any div with substantial text content
        if not all_descriptions:
            print("   📌 Strategy 3: Content-based detection...")
            
            # Look for divs with description-like content
            for div in soup.find_all('div'):
                text = div.get_text(separator='\n', strip=True)
                
                # Look for content that looks like descriptions
                if (len(text) > 200 and len(text) < 10000 and
                    any(kw in text.lower() for kw in ['material', 'product', 'design', 'features', 'specification', 'quality'])):
                    
                    if not any(text in existing for existing in all_descriptions):
                        all_descriptions.append(text)
                        sources_found.append('content-detected')
                        print(f"      Found content: {text[:60]}...")
        
        # Combine all
        if all_descriptions:
            print(f"   ✅ Found {len(all_descriptions)} description section(s)")
            combined = "\n\n---\n\n".join(all_descriptions)
            
            # Clean up
            combined = re.sub(r'\n\s*\n+', '\n\n', combined)
            combined = re.sub(r'[ \t]+', ' ', combined)
            combined = combined.strip()
            
            return combined, sources_found
        else:
            print("   ⚠️ No descriptions found with any strategy")
            
            # Last resort: show what was found
            if structure['all_divs_with_text']:
                print(f"   💡 Found {len(structure['all_divs_with_text'])} divs with text content")
                print("      Largest ones:")
                sorted_divs = sorted(structure['all_divs_with_text'], key=lambda x: x['text_length'], reverse=True)
                for div_info in sorted_divs[:3]:
                    print(f"         {div_info['text_length']} chars - id:{div_info['id']}, class:{div_info['classes'][:40]}")
            
            return "", sources_found
    
    except Exception as e:
        print(f"❌ Description extraction error: {e}")
        import traceback
        traceback.print_exc()
        return "", sources_found


def extract_images_adaptive(page) -> list:
    """Adaptively extract images from any description section"""
    print("🖼️ Extracting images...")
    
    images = []
    
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Look for all images with alicdn
        print("   🔍 Looking for alicdn.com images...")
        
        all_imgs = soup.find_all('img')
        print(f"      Found {len(all_imgs)} total images")
        
        alicdn_count = 0
        for img in all_imgs:
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            
            if src and "alicdn.com" in src:
                clean_src = src.split('?')[0]
                
                # Quality filter
                if (len(clean_src) > 40 and 
                    not any(bad in clean_src.lower() for bad in ['icon', 'logo', '20x20', '50x50', '100x100', '/s.gif']) and
                    clean_src not in images):
                    images.append(clean_src)
                    alicdn_count += 1
        
        print(f"      ✓ Found {alicdn_count} quality images")
        
        # Limit to 20
        images = images[:20]
        
        return images
    
    except Exception as e:
        print(f"⚠️ Image extraction error: {e}")
        return []


def extract_title_universal(page) -> str:
    """Extract title"""
    
    print("📌 Extracting title...")
    
    title_selectors = [
        ('[data-pl="product-title"]', "data-pl product-title"),
        ('h1', "h1 heading"),
        ('[class*="product-title"]', "product-title class"),
        ('span[class*="title"]', "span title class"),
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
    
    print("⚠️ Could not extract title")
    return ""


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract from AliExpress with adaptive detection
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
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                timezone_id='America/New_York'
            )
            
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            try:
                # LOAD PAGE
                print("📡 Loading page...")
                page.goto(url, timeout=120000, wait_until="domcontentloaded")
                time.sleep(2)
                
                final_url = page.url
                if final_url != url:
                    print(f"⚠️ Redirected: {final_url}")
                
                # CHECK CAPTCHA
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA detected - retrying...")
                    browser.close()
                    continue
                
                # WAIT & SCROLL
                print("⏳ Waiting for page render...")
                time.sleep(8)
                
                print("⏳ Scrolling...")
                for _ in range(5):
                    page.mouse.wheel(0, random.randint(150, 300))
                    time.sleep(0.3)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)
                
                # CHECK CAPTCHA AGAIN
                if is_captcha_page(page):
                    print("⚠️ CAPTCHA after scroll - retrying...")
                    browser.close()
                    continue
                
                # EXTRACT
                title = extract_title_universal(page)
                description_text, sources = extract_description_adaptive(page)
                images = extract_images_adaptive(page)
                
                # RESULTS
                browser.close()
                
                result = {
                    "title": title,
                    "description_text": description_text,
                    "images": images,
                    "store_info": {}
                }
                
                print(f"\n✅ Results:")
                print(f"   Title: {len(title)} chars")
                print(f"   Description: {len(description_text)} chars (from {sources})")
                print(f"   Images: {len(images)}")
                print(f"\n✅ Success on attempt {attempt + 1}")
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
    
    print(f"❌ Failed after {max_retries} attempts")
    return empty_result
