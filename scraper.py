import re
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


def normalize_img_url(src: str) -> str:
    """Convert relative or protocol-relative image URLs to absolute HTTPS URLs."""
    if not src:
        return ""
    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://www.aliexpress.com" + src
    return src


def clean_text(text: str) -> str:
    """Strip HTML tags and normalize whitespace from extracted text."""
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_aliexpress_product(url: str, max_retries: int = 3) -> dict:
    print("Opening browser...")

    # Ensure URL has https:// prefix and strip any fragment (#)
    base_url = url.split('#')[0].strip()
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    with sync_playwright() as p:

        # Launch Chromium in headless mode, routing traffic through
        # local Tor SOCKS5 proxy to get a non-datacenter IP.
        # This prevents AliExpress from blocking GCP's IP range.
        browser = p.chromium.launch(
            headless=True,
            proxy={"server": "socks5://127.0.0.1:9050"}
        )

        # Create browser context with US locale and language headers.
        # This tells AliExpress to serve the English US version instead
        # of redirecting to a regional site (de., nl., it. etc.)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        page = context.new_page()

        # Navigate to the product page with retries.
        # We only wait for domcontentloaded (not networkidle) because
        # AliExpress continuously makes background requests that would
        # cause networkidle to never trigger.
        for attempt in range(max_retries):
            try:
                page.goto(base_url, timeout=120000, wait_until="domcontentloaded")
                # Fixed wait for initial JS framework to hydrate
                page.wait_for_timeout(8000)
                break
            except Exception as e:
                print(f"Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    browser.close()
                    return {"title": "", "description_text": "", "images": []}
                page.wait_for_timeout(5000)

        # Extra wait for React components to fully render after page load
        page.wait_for_timeout(10000)

        # Scroll down to trigger lazy-loaded content (images, description)
        for _ in range(10):
            page.mouse.wheel(0, 200)
            page.wait_for_timeout(300)

        # Try clicking the "Description" tab if it exists.
        # AliExpress loads description content dynamically on tab click.
        try:
            desc_tab = (
                page.query_selector('a:has-text("Description")') or
                page.query_selector('a:has-text("description")')
            )
            if desc_tab:
                print("Clicking Description tab...")
                desc_tab.click()
                page.wait_for_timeout(5000)  # Wait for description content to load

                # Scroll inside the description container to load all images
                desc_container = page.query_selector("#product-description")
                if desc_container:
                    desc_container.scroll_into_view_if_needed()
                    for _ in range(5):
                        page.mouse.wheel(0, 300)
                        page.wait_for_timeout(500)
        except Exception as e:
            print(f"Could not click Description tab: {e}")

        def safe_query_text(selector: str) -> str:
            """Safely query a selector and return its text, or empty string."""
            el = page.query_selector(selector)
            return el.text_content().strip() if el else ""

        # Try multiple title selectors in order of specificity.
        # Skip any result that is just "aliexpress" (the site header h1).
        # AliExpress frequently changes their CSS class names so we
        # try several fallbacks.
        title = ""
        title_selectors = [
            "[data-pl='product-title']",
            ".product-title-text",
            ".title--wrap--UUHae_g h1",
            "h1.pdp-title",
            "#root h1",
            "h1",
        ]
        for sel in title_selectors:
            candidate = safe_query_text(sel)
            if candidate and candidate.lower().strip() != "aliexpress":
                title = candidate
                print(f"Title found with selector '{sel}': {title[:50]}")
                break
        if not title:
            print("Title not found")

        # Extract description text and images from the description container
        description_text = ""
        images = []
        try:
            container = page.query_selector("#product-description")
            if container:
                print("Found description container...")

                # First try the specific AliExpress description paragraph class
                text_elements = container.query_selector_all(
                    "p.detail-desc-decorate-content"
                )
                for el in text_elements:
                    text = el.text_content().strip()
                    if text:
                        description_text += text + " "

                # Fallback: grab all paragraph tags inside the container
                if not description_text:
                    all_p = container.query_selector_all("p")
                    for el in all_p:
                        text = el.text_content().strip()
                        if text:
                            description_text += text + " "

                # Extract images, filtering to only AliExpress CDN URLs
                # to avoid picking up tracking pixels or external images
                img_elements = container.query_selector_all("img")
                for img in img_elements:
                    src = img.get_attribute("src") or img.get_attribute("data-src")
                    if src:
                        src = normalize_img_url(src)
                        if "alicdn" in src:
                            images.append(src)

                # Remove duplicate image URLs while preserving order
                images = list(dict.fromkeys(images))
                print(f"Extracted {len(images)} images, description length: {len(description_text)}")
            else:
                print("Description container not found.")
        except Exception as e:
            print(f"Error extracting description and images: {e}")

        browser.close()

        return {
            "title": clean_text(title),
            "description_text": clean_text(description_text),
            "images": images
        }
