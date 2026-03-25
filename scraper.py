import re
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from stem import Signal
from stem.control import Controller


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
    if any(kw in page_url for kw in ["baxia", "punish", "captcha", "verify"]):
        return True
    try:
        if page.locator("iframe[src*='recaptcha']").count() > 0:
            return True
    except:
        pass
    return False


def is_css_garbage(text: str) -> bool:
    """Check if text is CSS or other garbage"""
    # CSS indicators
    css_keywords = [
        '.product-description {',
        'overflow: hidden',
        'margin: 0;',
        'line-height: inherit',
        'word-break: break-word',
        'box-sizing: content-box',
        'list-style-type',
        'padding-inline-start',
        'margin-block-start',
        'margin-block-end',
        'margin-inline-start',
        'margin-inline-end',
        'vertical-align: middle',
        'max-width:',
        'font-family:',
        'font-size:',
        'font-weight:',
        'border-radius:',
        'background-color:',
        '@media',
        'display:',
        'position:',
        'white-space:',
    ]
    
    if any(kw in text for kw in css_keywords):
        return True
    
    if len(text.strip()) < 3:
        return True
    
    return False


def extract_title_universal(page) -> str:
    """Extract title"""
    print("📌 Extracting title...")
    
    title_selectors = [
        ('[data-pl="product-title"]', "data-pl"),
        ('h1', "h1"),
        ('[class*="product-title"]', "class"),
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


def extract_description_method0(page) -> str:
    """
    Method 0: Extract from regular <p> paragraphs
    Returns content if found, or empty string if not found
    """
    print("   🎯 Method 0: Extracting paragraph text...")
    
    try:
        all_paragraphs = page.locator('#product-description p').all()
        print(f"      Found {len(all_paragraphs)} <p> elements")
        
        all_text_parts = []
        
        for p in all_paragraphs:
            try:
                txt = p.inner_text(timeout=2000).strip()
                
                # Filter out garbage
                if txt and not is_css_garbage(txt):
                    all_text_parts.append(txt)
            except:
                pass
        
        if all_text_parts:
            combined = ' '.join(all_text_parts)
            combined = re.sub(r'\s+', ' ', combined).strip()
            print(f"      ✓ Extracted {len(combined)} chars")
            return combined
        else:
            print(f"      ⚠️ No content found in paragraphs")
            return ""
    
    except Exception as e:
        print(f"      ❌ Error: {e}")
        return ""


def extract_description_shadow_dom(page) -> str:
    """
    Fallback: Extract from Shadow DOM with CSS filtering
    Only used if Method 0 returns nothing
    """
    print("   🎯 Fallback: Shadow DOM extraction (with filtering)...")
    
    try:
        data = page.evaluate("""
        () => {
            const result = { texts: [] };

            const rootContainer = document.querySelector('#product-description > div');
            if (!rootContainer) return result;

            const shadow = rootContainer.shadowRoot;
            if (!shadow) return result;

            const walker = document.createTreeWalker(
                shadow,
                NodeFilter.SHOW_ELEMENT,
                null,
                false
            );

            let node;
            while (node = walker.nextNode()) {
                if (node.innerText) {
                    result.texts.push(node.innerText);
                }
            }

            return result;
        }
        """)

        if data.get("texts"):
            all_text = " ".join(data["texts"])
            
            # Filter CSS garbage
            lines = all_text.split('\n')
            filtered_lines = []
            
            for line in lines:
                line = line.strip()
                if line and not is_css_garbage(line):
                    filtered_lines.append(line)
            
            if filtered_lines:
                result = ' '.join(filtered_lines)
                result = re.sub(r'\s+', ' ', result).strip()
                print(f"      ✓ Extracted {len(result)} chars from Shadow DOM (filtered)")
                return result
        
        print(f"      ⚠️ No content in Shadow DOM")
        return ""
    
    except Exception as e:
        print(f"      ❌ Error: {e}")
        return ""


def extract_description_hybrid(page) -> str:
    """
    HYBRID approach:
    1. Try Method 0 (regular paragraphs)
    2. If nothing, fall back to Shadow DOM extraction
    3. Filter CSS garbage
    4. Deduplicate
    """
    print("📝 Extracting description (Hybrid method)...")
    
    # Method 0 first
    method0_result = extract_description_method0(page)
    
    # If Method 0 got good content, use it
    if len(method0_result) > 500:
        print(f"   ✅ Using Method 0 ({len(method0_result)} chars)")
        description_text = method0_result
    # Otherwise try Shadow DOM
    elif len(method0_result) == 0:
        print(f"   ⚠️ Method 0 got nothing, trying Shadow DOM...")
        shadow_result = extract_description_shadow_dom(page)
        description_text = shadow_result
    else:
        # Method 0 got something but small, combine both
        print(f"   ℹ️ Method 0 got {len(method0_result)} chars, adding Shadow DOM...")
        shadow_result = extract_description_shadow_dom(page)
        if shadow_result:
            combined = method0_result + " " + shadow_result
            description_text = re.sub(r'\s+', ' ', combined).strip()
        else:
            description_text = method0_result
    
    # Deduplicate sentences
    if description_text:
        print(f"   🔄 Removing duplicates...")
        sentences = description_text.split('. ')
        unique_sentences = []
        seen = set()
        dup_count = 0
        
        for sentence in sentences:
            sentence = sentence.strip()
            if sentence and sentence not in seen:
                unique_sentences.append(sentence)
                seen.add(sentence)
            elif sentence in seen:
                dup_count += 1
        
        if dup_count > 0:
            print(f"      ✓ Removed {dup_count} duplicate sentences")
        
        result = '. '.join(unique_sentences)
        if not result.endswith('.'):
            result += '.'
        
        print(f"   ✅ Final: {len(result)} chars")
        return result
    
    return ""


def extract_images_simple(page) -> list:
    """Extract images, deduplicate"""
    print("🖼️ Extracting images...")
    
    images = []
    
    try:
        desc_container = page.locator('#product-description').first
        
        if desc_container.count() > 0:
            imgs = desc_container.locator('img').all()
            print(f"   Found {len(imgs)} <img> tags")
            
            for img in imgs:
                try:
                    src = img.get_attribute('src') or img.get_attribute('data-src')
                    
                    if src and 'alicdn.com' in src:
                        clean_src = src.split('?')[0]
                        
                        if len(clean_src) > 50:
                            images.append(clean_src)
                except:
                    pass
            
            # Deduplicate
            unique_images = []
            seen = set()
            dup_count = 0
            
            for img in images:
                if img not in seen:
                    unique_images.append(img)
                    seen.add(img)
                else:
                    dup_count += 1
            
            if dup_count > 0:
                print(f"   ✓ Removed {dup_count} duplicate images")
            
            unique_images = unique_images[:20]
            
            print(f"   ✓ Final: {len(unique_images)} unique images")
            
            return unique_images
    
    except Exception as e:
        print(f"   ⚠️ Error: {e}")
    
    return []


def extract_aliexpress_product(url: str) -> dict:
    """Extract from AliExpress - HYBRID METHOD"""
    
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
            print("🔄 Rotating Tor...")
            rotate_tor_circuit()
            wait_time = 20 + (attempt * 5)
            print(f"   Waiting {wait_time}s...")
            time.sleep(wait_time)
        
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                proxy={"server": "socks5://127.0.0.1:9050"},
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )
            
            page = browser.new_page(
                viewport=random_viewport(),
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
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
                
                # WAIT & SCROLL
                print("⏳ Rendering...")
                time.sleep(8)
                
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
                title = extract_title_universal(page)
                original_title = title
                print(f"🔐 Title backup: {len(original_title)} chars")
                
                description_text = extract_description_hybrid(page)
                images = extract_images_simple(page)
                
                browser.close()
                
                # Restore title if needed
                if not title or len(title) == 0:
                    title = original_title
                
                result = {
                    "title": title if isinstance(title, str) and len(title) > 0 else original_title,
                    "description_text": description_text if isinstance(description_text, str) else "",
                    "images": images if isinstance(images, list) else [],
                    "store_info": {}
                }
                
                print(f"\n✅ RESULT:")
                print(f"   Title: {len(result['title'])} chars")
                print(f"   Description: {len(result['description_text'])} chars")
                print(f"   Images: {len(result['images'])}")
                
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
