# AX-Scraper

A comprehensive **AliExpress product scraper** built with FastAPI, featuring advanced data extraction, LLM-powered content refinement, and intelligent product categorization.

## 🎯 Overview

AX-Scraper is a production-ready scraping solution that:
- **Extracts product data** from AliExpress (titles, descriptions, images, ratings, delivery dates)
- **Refines content** using OpenAI LLM for SEO optimization and marketing descriptions
- **Categorizes products** intelligently using embeddings and similarity matching
- **Manages store information** and scrapes store metadata
- **Handles anti-bot protection** with Tor IP rotation and CAPTCHA detection
- **Stores data** in SQLite with relational models

---

## ✨ Key Features

### 📊 Data Extraction
- Extract product IDs, titles, descriptions, and images from AliExpress search results
- Scrape product ratings, star ratings, and delivery dates
- Support for bulk product scraping with retry logic
- Store item counts from AliExpress seller shops

### 🤖 LLM-Powered Content Refinement
- **Enhanced titles** for better SEO and clarity
- **Optimized descriptions** with structured benefits and bullet points
- **Marketing descriptions** (up to 5000 chars HTML)
- Powered by OpenAI's GPT-4o-mini

### 🏷️ Intelligent Categorization
- LLM-predicted category assignment
- Embedding-based similarity matching
- Category confidence scoring
- Support for custom category hierarchies

### 🛡️ Anti-Bot Protection
- Tor proxy rotation for IP anonymization
- Automatic CAPTCHA detection and handling
- Random delays and user-agent rotation
- Playwright headless browser for dynamic content
- Configurable retry logic with exponential backoff

### 💾 Data Management
- SQLite database with relational models
- Automatic schema migrations
- Product relationship tracking (fetched → refined → categorized)
- Manufacturer and store information storage

---

## 🚀 Quick Start

### Prerequisites
```bash
# Python 3.8+
# Tor service (optional but recommended)
# OpenAI API key

sudo apt install tor
sudo service tor start
```

### Installation

```bash
# Clone repository
git clone https://github.com/HamnaMohsin/ax-scraper.git
cd ax-scraper

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### Environment Setup

Create a `.env` file or export environment variables:
```bash
export OPENAI_API_KEY="your-api-key-here"
```

### Running the Server

```bash
# Start FastAPI server
uvicorn main:app --host 0.0.0.0 --port 8001

# Server will be available at: http://localhost:8001
# API docs: http://localhost:8001/docs
```

---

## 📚 API Endpoints

### Scraping Endpoints

#### 1. **Scrape Product by Category**
```http
POST /scrape/category
```
Scrape products from a specific category with pagination.

**Request:**
```json
{
  "category": "phone case",
  "max_pages": 3
}
```

#### 2. **Fetch Single Product**
```http
POST /scrape/product
```
Extract detailed data from a single AliExpress product URL.

**Request:**
```json
{
  "url": "https://www.aliexpress.com/item/1005001234567.html"
}
```

#### 3. **Bulk Product Details**
```http
POST /scrape/product-details/bulk
```
Fetch ratings and delivery dates for multiple products.

**Request:**
```json
{
  "product_ids": [1005001234567, 1005001234568]
}
```

#### 4. **Store Item Count**
```http
POST /scrape/store/item-count
```
Get the total number of items in an AliExpress store.

**Request:**
```json
{
  "store_id": "911431006"
}
```

#### 5. **Scrape by Range**
```http
POST /scrape/store/by-range
```
Scrape multiple stores by range.

### Content Refinement

#### 6. **Refine Product Content**
```http
POST /refine/product
```
Enhance product title and description using LLM.

**Request:**
```json
{
  "title": "Original title",
  "description": "Original description"
}
```

### Categorization

#### 7. **Categorize Product**
```http
POST /categorize/product
```
Assign category and predict product type using embeddings.

**Request:**
```json
{
  "product_id": 1005001234567,
  "title": "Product Title",
  "description": "Product Description"
}
```

### Data Export

#### 8. **Export Products**
```http
GET /export/products
```
Export all products with refinements and categories to Excel templates.

---

## 📦 Project Structure

```
ax-scraper/
├── main.py                    # FastAPI application & endpoints
├── models.py                  # SQLAlchemy database models
├── database.py                # Database configuration & migrations
├── schemas.py                 # Pydantic request/response schemas
│
├── scraper4.py               # Core product scraper (Playwright + Tor)
├── scraper3.py               # Product data extraction
├── scr1.py                   # Category-based scraper
├── scr04.py                  # Product details (ratings, delivery)
├── scrape_items.py           # Store item count scraper
├── scr_item_count.py         # Bulk store scraper
│
├── llm_refiner.py            # LLM content refinement
├── llm_refiner2.py           # Enhanced refinement with marketing desc
├── assign_embeddings2.py     # Embedding-based categorization
│
├── data/
│   ├── products.db           # SQLite database
│   ├── output_templates/     # Export templates
│   └── export_to_template.py # Excel export utilities
│
├── requirements.txt          # Python dependencies
└── README.md                # This file
```

---

## 🗄️ Database Models

### ProductFetched
Raw product data extracted from AliExpress.
```python
- product_id (BigInteger, PK)
- url (String, unique)
- title (String)
- description (Text)
- images (JSON)
- exported_at (DateTime)
```

### ProductRefined
LLM-enhanced product content.
```python
- id (Integer, PK)
- product_id (BigInteger, FK)
- enhanced_title (String)
- enhanced_description (Text)
- description_marketing (Text)
```

### CategoryAssignment
Product categorization results.
```python
- id (Integer, PK)
- product_id (BigInteger, FK)
- llm_predicted_category (String)
- assigned_category (String)
- category_id (String)
- similarity_score (Float)
```

### ManufacturerInfo
Store and manufacturer details.
```python
- store_name (String, PK)
- store_id (String, PK)
- name (String)
- address (Text)
- email (String)
- phone (String)
```

---

## 🔧 Configuration

### Tor Configuration
Ensure Tor is running on port 9050 (SOCKS5):
```bash
# Check Tor service
sudo service tor status

# Configure control port in /etc/tor/torrc if needed
ControlPort 9051
```

### Anti-CAPTCHA Settings
Customize retry behavior in `scr04.py`:
```python
MAX_CAPTCHA_ROTATIONS = 5          # Max IP rotations
ROTATE_WAIT_SECS = 14              # Wait between rotations
MAX_CAPTCHA_ROTATIONS_API = 8      # API-specific retries
ROTATE_WAIT_SECS_API = 25          # API-specific wait
```

### Categories
Modify default categories in `scr1.py`:
```python
CATEGORIES = [
    "lapdesks",
    "led strip lights",
    "phone case",
    "laptop stand",
    "smart watch",
]
```

---

## 📋 Dependencies

Key packages:
- **fastapi** - Web framework
- **uvicorn** - ASGI server
- **sqlalchemy** - ORM & database
- **playwright** - Browser automation
- **beautifulsoup4** - HTML parsing
- **stem** - Tor circuit control
- **openai** - LLM integration
- **pandas** - Data manipulation
- **openpyxl** - Excel export

See `requirements.txt` for complete list.

---

## 🔐 Security & Best Practices

1. **Never commit API keys** - Use environment variables
2. **Rate limiting** - AliExpress may block aggressive scraping
3. **Tor rotation** - Automatically rotates IP on blocks
4. **User-Agent rotation** - Randomizes browser identification
5. **Random delays** - Simulates human behavior
6. **Respectful scraping** - Follow AliExpress terms of service

---

## 🐛 Troubleshooting

### CAPTCHA Blocks
- Ensure Tor is running: `sudo service tor start`
- Check Tor control port: `sudo ss -tulpn | grep 9051`
- Increase retry counts in config

### Playwright Issues
```bash
playwright install chromium
export PLAYWRIGHT_BROWSERS_PATH=0
```

### Database Errors
```bash
# Reset database
rm data/products.db
# Restart server to reinitialize
```

### LLM Rate Limits
- Check OpenAI API quota
- Implement request throttling
- Monitor API usage

---

## 🤝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Submit a pull request

---

## 📄 License

This project is provided as-is. Please ensure compliance with AliExpress terms of service and local laws regarding web scraping.

---

## 📧 Support

For issues and questions:
- Open a GitHub issue
- Check existing issues for solutions
- Review API documentation at `/docs` endpoint

---

## ⚡ Performance Tips

1. **Use Tor wisely** - Only rotate when necessary
2. **Batch operations** - Scrape multiple products together
3. **Cache results** - Avoid re-scraping identical URLs
4. **Monitor database** - Clean up old entries periodically
5. **Parallel processing** - Use background tasks for bulk operations

---

**Happy Scraping! 🚀**