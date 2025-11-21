# Auto LOI Emails - Single Family Rental (SFR) Scraper

A comprehensive Python scraper system that collects contact information for single-family rentals from **Apartments.com** and **Zillow**. Uses Playwright for browser automation, SQLite for persistent checkpointing, and multi-fallback extraction strategies for maximum accuracy.

## Features

- ✅ **Dual Source Scraping**: Collects data from both Apartments.com and Zillow
- ✅ **Playwright Navigation**: Handles JavaScript-rendered pages reliably
- ✅ **Multi-Fallback Extraction**: JSON-LD → Selectors → Regex fallbacks
- ✅ **SQLite Persistence**: Resume capability with checkpointing
- ✅ **Phone-Based Deduplication**: Keyed by normalized phone number
- ✅ **Address Aggregation**: Groups multiple addresses per phone
- ✅ **Rate Limiting**: Configurable delays with random jitter
- ✅ **Human-like Behavior**: Slow scrolling, random delays, stealth techniques
- ✅ **Master CSV Combiner**: Merges data from both sources

## Project Structure

```
auto_loi_emails/
├── scrapers/                 # Scraper scripts
│   ├── __init__.py
│   ├── apartments/          # Apartments.com scraper
│   │   ├── __init__.py
│   │   ├── main.py          # Entry point
│   │   └── scraper.py       # Scraping logic
│   └── zillow/              # Zillow scraper
│       ├── __init__.py
│       ├── collect_urls.py  # Step 1: Collect property URLs
│       └── scrape_from_urls.py  # Step 2: Scrape contact info
├── src/                      # Shared utilities
│   ├── __init__.py
│   ├── store.py            # SQLite database utilities
│   └── combine.py           # Master CSV combiner
├── data/                     # Data files (CSVs, databases)
│   ├── apartments_sfr.csv
│   ├── zillow_urls.csv
│   ├── zillow_sfr.csv
│   ├── master_sfr.csv
│   ├── data.db              # Apartments.com database
│   └── zillow_data.db       # Zillow database
├── requirements.txt          # Python dependencies
├── README.md                 # This file
├── .gitignore               # Git ignore rules
└── venv/                     # Virtual environment (not in git)
```

## Requirements

- Python 3.9+
- Internet connection

## Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd auto_loi_emails
   ```

2. **Create and activate virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On macOS/Linux
   # or
   venv\Scripts\activate     # On Windows
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Install Playwright browsers**:
   ```bash
   python -m playwright install chromium
   ```
   
   This installs the Chromium browser needed for headless scraping.

## Usage

### 1. Apartments.com Scraper

Scrapes single-family rental listings from Apartments.com.

**Basic usage**:
```bash
python -m scrapers.apartments.main --city "Atlanta" --state "GA"
```

**Full example**:
```bash
python -m scrapers.apartments.main \
  --city "Atlanta" \
  --state "GA" \
  --max_pages 40 \
  --target_phones 200 \
  --delay 3.0 \
  --headless true
```

**Arguments**:
- `--city` (required): City name (e.g., "Atlanta")
- `--state` (required): State abbreviation (e.g., "GA")
- `--max_pages` (optional): Maximum pages to scrape (default: 50)
- `--target_phones` (optional): Stop when this many unique phones are found (default: 200)
- `--delay` (optional): Base delay between navigations in seconds (default: 3.0)
- `--headless` (optional): Run browser in headless mode: true|false (default: true)
- `--output` (optional): Output CSV file path (default: data/apartments_sfr.csv)

**Output**: `data/apartments_sfr.csv` with columns: `phone`, `manager_name`, `addresses`, `units`

### 2. Zillow Scraper

The Zillow scraper works in two steps:

#### Step 1: Collect Property URLs

Collects property URLs from Zillow search pages. Runs indefinitely until no more pages are available.

```bash
python scrapers/zillow/collect_urls.py \
  --city "Atlanta" \
  --state "GA" \
  --delay 3.0 \
  --output data/zillow_urls.csv
```

**Arguments**:
- `--city` (required): City name (e.g., "Atlanta")
- `--state` (required): State abbreviation (e.g., "GA")
- `--max_pages` (optional): Maximum pages to scrape (default: unlimited, runs until no more pages)
- `--delay` (optional): Delay between pages in seconds (default: 3.0)
- `--output` (optional): Output CSV file for URLs (default: data/zillow_urls.csv)
- `--headless` (optional): Run browser in headless mode (add flag)

**Output**: `data/zillow_urls.csv` with column: `url`

#### Step 2: Scrape Contact Info from URLs

Scrapes contact information (phone, address, agent name, business name) from collected URLs.

```bash
python scrapers/zillow/scrape_from_urls.py \
  --input data/zillow_urls.csv \
  --output data/zillow_sfr.csv \
  --delay 3.0
```

**Arguments**:
- `--input` (required): Input CSV file with URLs (e.g., `data/zillow_urls.csv`)
- `--output` (optional): Output CSV file (default: data/zillow_sfr.csv)
- `--delay` (optional): Delay between URLs in seconds (default: 3.0)
- `--headless` (optional): Run browser in headless mode (add flag)

**Output**: `data/zillow_sfr.csv` with columns: `phone`, `agent_name`, `business_name`, `addresses`, `units`

### 3. Combine Data from Both Sources

Combines data from Apartments.com and Zillow into a master CSV with source tracking.

```bash
python -m src.combine \
  --apartments data/apartments_sfr.csv \
  --zillow data/zillow_sfr.csv \
  --output data/master_sfr.csv
```

**Arguments**:
- `--apartments` (required): Apartments.com CSV file path
- `--zillow` (required): Zillow CSV file path
- `--output` (optional): Output master CSV file (default: data/master_sfr.csv)

**Output**: `data/master_sfr.csv` with columns: `phone`, `manager_name`, `addresses`, `units`, `source`

## Data Extraction Details

### Apartments.com

Extracts from listing detail pages:
- **Phone**: JSON-LD → tel: links → selectors → regex
- **Address**: JSON-LD → meta tags → selectors → regex
- **Manager Name**: JSON-LD → labels → selectors → regex

### Zillow

Extracts from property detail pages:
- **Phone**: `ds-listing-agent-info` container → JSON-LD → selectors → regex
- **Agent Name**: `ds-listing-agent-display-name` class
- **Business Name**: `ds-listing-agent-business-name` class
- **Address**: `Text-c11n-8-109-3__sc-aiai24-0 cEHZrB` class → meta tags → selectors → regex

## Output Format

### Apartments.com CSV
```csv
phone,manager_name,addresses,units
4045551234,"ABC Property Management","123 Main St; 456 Oak Ave",2
4045559876,"","789 Pine Rd",1
```

### Zillow CSV
```csv
phone,agent_name,business_name,addresses,units
4045551234,"John Smith","ABC Properties LLC","123 Main St",1
4045559876,"","","789 Pine Rd",1
```

### Master CSV
```csv
phone,manager_name,addresses,units,source
4045551234,"ABC Property Management","123 Main St; 456 Oak Ave",2,apartments
4045555678,"John Smith","789 Pine Rd",1,zillow
4045559999,"XYZ Properties","111 Oak St",1,both
```

## Dependencies

- `playwright>=1.40.0`: Browser automation
- `beautifulsoup4>=4.12.2`: HTML parsing
- `pandas>=2.1.4`: CSV handling
- `lxml>=4.9.3`: Fast HTML parser

## Database Files

The scrapers use SQLite databases for persistence:
- `data/data.db`: Apartments.com progress tracking
- `data/zillow_data.db`: Zillow progress tracking

These databases track:
- Crawled URLs (to avoid duplicates)
- Phone numbers and associated data
- Addresses per phone number

You can safely delete these files to start fresh, but you'll lose progress tracking.

## Resume Capability

Both scrapers support resume functionality:
- **Apartments.com**: Re-run the same command to resume from where you left off
- **Zillow URL Collection**: Automatically skips URLs already in the CSV
- **Zillow Scraping**: Uses database to track crawled URLs

## Rate Limiting & Stealth

The scrapers include:
- Configurable delays with random jitter
- Human-like scrolling and mouse movements
- Stealth techniques to avoid bot detection
- Random delays between actions

**Recommendation**: Use `--delay 3.0` or higher to avoid rate limiting.

## Troubleshooting

### No listings found
- Verify city and state are correct
- Check if the source website has listings for that area
- Try running with `--headless false` to see what's happening

### Missing phone numbers
Some listings may not have phone numbers publicly available. These are skipped as phone is the required unique key.

### Rate limiting / CAPTCHAs
- Increase the `--delay` parameter (e.g., `--delay 5.0`)
- Run with `--headless false` to manually solve CAPTCHAs if needed
- The scrapers include stealth techniques, but aggressive scraping may still trigger protections

### Import errors
If you get import errors, make sure you're running from the project root directory:
```bash
cd /path/to/auto_loi_emails
python -m scrapers.apartments.main ...
```

## Workflow Example

Complete workflow to scrape both sources and combine:

```bash
# 1. Scrape Apartments.com
python -m scrapers.apartments.main \
  --city "Atlanta" \
  --state "GA" \
  --max_pages 40 \
  --target_phones 200 \
  --output data/apartments_sfr.csv

# 2. Collect Zillow URLs
python scrapers/zillow/collect_urls.py \
  --city "Atlanta" \
  --state "GA" \
  --output data/zillow_urls.csv

# 3. Scrape Zillow contact info
python scrapers/zillow/scrape_from_urls.py \
  --input data/zillow_urls.csv \
  --output data/zillow_sfr.csv

# 4. Combine both sources
python -m src.combine \
  --apartments data/apartments_sfr.csv \
  --zillow data/zillow_sfr.csv \
  --output data/master_sfr.csv
```

## License

This project is for educational/research purposes. Use responsibly and in compliance with all applicable laws and Terms of Service of the websites being scraped.
