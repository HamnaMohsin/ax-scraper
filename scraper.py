import re
import time
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def extract_store_info_popover(page) -> dict:
    """Extract store info from hidden popover element."""
    store_info = {}
    
    print("📦 Extracting store info from popover...")
    
    # STRATEGY 1: Extract from page HTML directly
    print("🔍 Strategy 1: Extract from page HTML...")
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Find popover
        popover = soup.find('div', class_='comet-v2-popover-wrap')
        if popover:
            print("   Found popover in page HTML")
            table = popover.find("table")
            
            if table:
                rows = table.find_all("tr")
                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) == 2:
                        key = clean_text(cols[0].get_text()).replace(":", "").strip()
                        value = clean_text(cols[1].get_text()).strip()
                        if key:
                            store_info[key] = value
                
                if store_info:
                    print(f"✅ Store info from HTML: {store_info}")
                    return store_info
                        
    except Exception as e:
        print(f"⚠️ HTML extraction failed: {e}")
 
    # STRATEGY 2: Try hover to trigger popover
    print("🔍 Strategy 2: Trigger popover via hover...")
    try:
        store_elem = page.locator('[class*="store"]').first
        if store_elem.count() > 0:
            store_elem.hover()
            time.sleep(1)
            
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            
            popover = soup.find('div', class_='comet-v2-popover-wrap')
            if popover and popover.find("table"):
                table = popover.find("table")
                for row in table.find_all("tr"):
                    cols = row.find_all("td")
                    if len(cols) == 2:
                        key = clean_text(cols[0].get_text()).replace(":", "").strip()
                        value = clean_text(cols[1].get_text()).strip()
                        if key:
                            store_info[key] = value
                
                if store_info:
                    print(f"✅ Store info from hover: {store_info}")
                    return store_info
    except Exception as e:
        print(f"⚠️ Hover strategy failed: {e}")
 
    # STRATEGY 3: Search all store-detail divs
    print("🔍 Strategy 3: Search all store-detail divs...")
    try:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        store_divs = soup.find_all('div', class_=lambda x: x and 'store-detail' in x)
        
        for store_div in store_divs:
            table = store_div.find("table")
            if table:
                temp_info = {}
                for row in table.find_all("tr"):
                    cols = row.find_all("td")
                    if len(cols) == 2:
                        key = clean_text(cols[0].get_text()).replace(":", "").strip()
                        value = clean_text(cols[1].get_text()).strip()
                        if key:
                            temp_info[key] = value
                
                if temp_info and any(k in str(temp_info).lower() 
                                    for k in ['store', 'location', 'name']):
                    print(f"✅ Store info found: {temp_info}")
                    return temp_info
                    
    except Exception as e:
        print(f"⚠️ Div search failed: {e}")
 
    print("⚠️ Could not extract store info")
    return store_info


def extract_aliexpress_product(url: str) -> dict:
    """
    FINAL FIXED VERSION:
    - Handles URL redirects
    - Waits for Angular rendering
    - Handles lazy-loaded content
    - No duplicate wait_for_selector
    """
    print(f"🔍 Scraping: {url}")

    empty_result = {
        "title": "",
        "description_text": "",
        "images": [],
        "store_info": {}
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
            ]
        )
        page = browser.new_page(
            viewport={'width': 1366, 'height': 768},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        try:
            # =====================
            # NAVIGATION (Handle redirects)
            # =====================
            print("📡 Loading page...")
            page.goto(url, timeout=120000, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(3)
            
            # ✅ KEY: Get actual URL after potential redirect
            actual_url = page.url
            if actual_url != url:
                print(f"⚠️ URL redirected:")
                print(f"   Original: {url}")
                print(f"   Actual:   {actual_url}")
            else:
                print(f"✅ No redirect")

            # =====================
            # WAIT FOR ANGULAR RENDERING
            # =====================
            print("⏳ Waiting for Angular to render...")
            try:
                # Wait for specific element that indicates page is ready
                page.wait_for_selector('[data-pl="product-title"]', timeout=20000)
                print("✅ Page ready")
            except Exception as e:
                print(f"⚠️ Timeout waiting for title selector: {e}")
                # Continue anyway - try to extract what we can

            # =====================
            # TITLE
            # =====================
            title = ""
            print("📌 Extracting title...")
            try:
                # Try main selector first
                title_elem = page.locator('[data-pl="product-title"]').first
                if title_elem.count() > 0:
                    title = title_elem.inner_text().strip()
                    print(f"✅ Title: {title[:80]}...")
                else:
                    # Fallback to h1
                    title = page.locator('h1').first.inner_text().strip()
                    print(f"✅ Title (h1): {title[:80]}...")
                    
            except Exception as e:
                print(f"⚠️ Title extraction: {e}")
                # Try from HTML
                try:
                    soup = BeautifulSoup(page.content(), "html.parser")
                    h1 = soup.find("h1")
                    if h1:
                        title = h1.get_text().strip()
                        print(f"✅ Title (HTML): {title[:80]}...")
                except:
                    pass

            # =====================
            # STORE INFO
            # =====================
            store_info = extract_store_info_popover(page)

            # =====================
            # SCROLL FOR LAZY-LOADED CONTENT
            # =====================
            print("⏳ Scrolling to load lazy content...")
            try:
                for i in range(5):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    time.sleep(0.8)
                
                # Scroll back to top
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)
            except Exception as e:
                print(f"⚠️ Scroll error: {e}")

            # =====================
            # DESCRIPTION
            # =====================
            print("📝 Extracting description...")
            description_text = ""
            description_html = ""

            desc_selectors = [
                "#product-description",
                '[class*="product-description"]',
                '[class*="detail-content"]',
                '.product-detail__description'
            ]
            
            for selector in desc_selectors:
                try:
                    # Don't wait too long - might not exist
                    locators = page.locator(selector)
                    count = locators.count()
                    
                    if count > 0:
                        print(f"🔍 Found {count} desc blocks for {selector}")
                        
                        for i in range(min(count, 3)):
                            block = locators.nth(i)
                            html = block.inner_html()
                            soup = BeautifulSoup(html, "html.parser")
                            
                            for tag in soup(["script", "style", "iframe"]):
                                tag.decompose()
                            
                            text = soup.get_text(" ", strip=True)
                            
                            if (len(text) > 300 and 
                                any(word in text.lower() for word in 
                                    ['feature', 'spec', 'parameter', 'function', 'include'])):
                                print(f"✅ Description: {len(text)} chars")
                                description_text = text
                                description_html = html
                                break
                        
                        if description_text:
                            break
                except:
                    continue

            # =====================
            # IMAGES
            # =====================
            print("🖼️ Extracting images...")
            description_images = []
            
            try:
                # Try multiple image selectors
                for selector in ['img[src*="alicdn"]', 'img[data-src*="alicdn"]', 'picture img']:
                    try:
                        imgs = page.locator(selector).all(max_items=20)
                        for img in imgs:
                            src = img.get_attribute("src") or img.get_attribute("data-src")
                            if src and "alicdn" in src and len(src) > 50:
                                description_images.append(src)
                    except:
                        continue
                
                # Also from description HTML
                if description_html:
                    soup = BeautifulSoup(description_html, "html.parser")
                    for img in soup.find_all("img"):
                        src = img.get("src") or img.get("data-src")
                        if src and "alicdn" in src:
                            description_images.append(src)
                
            except Exception as e:
                print(f"⚠️ Image extraction error: {e}")

            description_images = list(set(description_images))[:20]
            print(f"✅ Images: {len(description_images)}")

            browser.close()

            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": description_images,
                "store_info": store_info,
                "actual_url": actual_url  # Include actual URL for debugging
            }

        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            try:
                browser.close()
            except:
                pass
            return empty_result


if __name__ == "__main__":
    # Test with original URL (it will redirect)
    url = "https://www.aliexpress.com/item/1005010735189221.html"
    result = extract_aliexpress_product(url)
    print("\n🎉 FINAL RESULT:")
    print(f"Title: {result.get('title', '')[:80]}")
    print(f"Store: {result.get('store_info', {})}")
    print(f"Images: {len(result.get('images', []))} found")
    print(f"Description: {len(result.get('description_text', ''))} chars")
