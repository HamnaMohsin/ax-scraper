import re
import time
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def extract_store_info_universal(page) -> dict:
    """Extract store info - works for both .com and .us domains"""
    store_info = {}
    
    print("📦 Extracting store info...")
    
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
        
        # Method 2: Any store-detail div
        if not store_info:
            for div in soup.find_all('div', class_=lambda x: x and 'store' in str(x).lower()):
                table = div.find("table")
                if table:
                    for row in table.find_all("tr"):
                        cols = row.find_all("td")
                        if len(cols) == 2:
                            key = clean_text(cols[0].get_text()).replace(":", "").strip()
                            value = clean_text(cols[1].get_text()).strip()
                            if key:
                                store_info[key] = value
                    
                    if store_info:
                        break
        
        # Method 3: Search for seller/store name directly
        if not store_info:
            for elem in soup.find_all(['span', 'div', 'a']):
                text = elem.get_text().strip()
                if 'store' in text.lower() and len(text) > 5:
                    parent = elem.find_parent('div')
                    if parent:
                        table = parent.find("table")
                        if table:
                            for row in table.find_all("tr"):
                                cols = row.find_all("td")
                                if len(cols) == 2:
                                    key = clean_text(cols[0].get_text()).replace(":", "").strip()
                                    value = clean_text(cols[1].get_text()).strip()
                                    if key:
                                        store_info[key] = value
                    
                    if store_info:
                        break
        
        if store_info:
            print(f"✅ Store info: {store_info}")
        else:
            print("⚠️ Could not extract store info")
            
    except Exception as e:
        print(f"⚠️ Store info extraction error: {e}")
    
    return store_info


def extract_title_universal(page) -> str:
    """Extract title - works for both .com and .us domains"""
    
    print("📌 Extracting title...")
    
    # Try multiple selectors (different for each domain)
    selectors_to_try = [
        ('[data-pl="product-title"]', "product-title selector"),
        ('h1', "h1 heading"),
        ('[class*="product-name"]', "product-name class"),
        ('[class*="title"]', "generic title class"),
        ('span[class*="title"]', "span with title class"),
    ]
    
    for selector, description in selectors_to_try:
        try:
            elem = page.locator(selector).first
            if elem.count() > 0:
                title = elem.inner_text().strip()
                if title and len(title) > 5:  # Valid title
                    print(f"✅ Title ({description}): {title[:80]}...")
                    return title
        except:
            continue
    
    # Fallback: Extract from HTML
    try:
        soup = BeautifulSoup(page.content(), "html.parser")
        
        # Try to find any reasonable title
        for tag in soup.find_all(['h1', 'h2', 'span', 'div']):
            text = tag.get_text().strip()
            if text and 15 < len(text) < 200:  # Reasonable title length
                print(f"✅ Title (HTML fallback): {text[:80]}...")
                return text
    except:
        pass
    
    print("⚠️ Could not extract title")
    return ""


def extract_aliexpress_product(url: str) -> dict:
    """
    Extract AliExpress product data.
    Handles redirects and works with both .com and .us domains.
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
            # NAVIGATION
            # =====================
            print("📡 Loading page...")
            
            # Stop at domcontentloaded (before redirect completes)
            page.goto(url, timeout=120000, wait_until="domcontentloaded")
            time.sleep(2)
            
            current_url = page.url
            if current_url == url:
                print(f"✅ No redirect")
            else:
                print(f"⚠️ Redirected to: {current_url}")
            
            # Try to wait for network if possible
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except:
                print("   Network didn't go idle, continuing...")

            # =====================
            # SCROLL BEFORE EXTRACTING (trigger lazy loading)
            # =====================
            print("⏳ Scrolling to trigger lazy loading...")
            try:
                for i in range(3):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    time.sleep(0.5)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)
            except Exception as e:
                print(f"   Scroll error: {e}")

            # =====================
            # TITLE (Universal selectors)
            # =====================
            title = extract_title_universal(page)
            
            # =====================
            # STORE INFO (Universal extraction)
            # =====================
            store_info = extract_store_info_universal(page)
            
            # =====================
            # DESCRIPTION
            # =====================
            print("📝 Extracting description...")
            description_text = ""
            
            desc_selectors = [
                "#product-description",
                '[class*="description"]',
                '[id*="description"]',
                '[class*="detail"]',
            ]
            
            for selector in desc_selectors:
                try:
                    elem = page.locator(selector).first
                    if elem.count() > 0:
                        html = elem.inner_html()
                        soup = BeautifulSoup(html, "html.parser")
                        
                        for tag in soup(["script", "style", "iframe"]):
                            tag.decompose()
                        
                        text = soup.get_text(" ", strip=True)
                        
                        if len(text) > 100:  # Reasonable description
                            print(f"✅ Description: {len(text)} chars")
                            description_text = text
                            break
                except:
                    continue

            # =====================
            # IMAGES
            # =====================
            print("🖼️ Extracting images...")
            description_images = []
            
            try:
                # Look for any img with alicdn source
                imgs = page.locator('img').all(max_items=50)
                for img in imgs:
                    src = img.get_attribute("src") or img.get_attribute("data-src")
                    if src and "alicdn" in src and len(src) > 50:
                        # Filter out thumbnails
                        if not any(x in src for x in ["50x50", "icon", "logo"]):
                            description_images.append(src)
            except:
                pass

            description_images = list(set(description_images))[:20]
            print(f"✅ Images: {len(description_images)}")

            browser.close()

            return {
                "title": clean_text(title),
                "description_text": clean_text(description_text),
                "images": description_images,
                "store_info": store_info
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
