# Apartments.com Single-Family Rental (SFR) Scraper

A robust Python scraper that collects contact information for single-family rentals from Apartments.com, focused on accuracy over speed. Uses Playwright for JavaScript-rendered pages, SQLite for persistent checkpointing, and multi-fallback extraction strategies.

## Features

- ✅ **Playwright Navigation**: Handles JavaScript-rendered pages reliably
- ✅ **Multi-Fallback Extraction**: JSON-LD → Selectors → Regex fallbacks for maximum accuracy
- ✅ **SQLite Persistence**: Resume capability with checkpointing
- ✅ **Phone-Based Deduplication**: Keyed by normalized phone number
- ✅ **Address Aggregation**: Groups multiple addresses per phone
- ✅ **Rate Limiting**: Configurable delays with ±0.6s jitter
- ✅ **Retry Logic**: 3 attempts with incremental backoff (1s, 2s, 4s)
- ✅ **Graceful Error Handling**: Comprehensive logging and error recovery

## Requirements

- Python 3.11+
- Internet connection

## Setup

1. **Activate virtual environment** (if not already active):
   ```bash
   source venv/bin/activate  # On macOS/Linux
   # or
   venv\Scripts\activate  # On Windows
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browsers**:
   ```bash
   python -m playwright install chromium
   ```
   
   This will install the Chromium browser needed for headless scraping.

## Usage

### Basic Example

```bash
python -m src.main --city "Atlanta" --state "GA"
```

### Full Example (Matching Spec)

```bash
python -m src.main --city "Atlanta" --state "GA" --max_pages 40 --target_phones 200 --delay 3.0 --headless true --output apartments_sfr.csv
```

### Command-Line Arguments

- `--city` (required): City name (e.g., "Atlanta")
- `--state` (required): State abbreviation (e.g., "GA")
- `--max_pages` (optional): Maximum number of search result pages to scrape (default: 50)
- `--target_phones` (optional): Stop when this many unique phone keys are stored (default: 200)
- `--delay` (optional): Base seconds between navigations; adds ±0.6s jitter (default: 3.0)
- `--headless` (optional): Run browser in headless mode: true|false (default: true)
- `--proxy` (optional): Optional HTTP proxy string; if empty, no proxy (default: empty)
- `--output` (optional): Output CSV file path (default: apartments_sfr.csv)

## Output

The scraper generates a CSV file with the following schema:

| Column | Type | Description |
|--------|------|-------------|
| `phone` | string | Phone number (normalized, unique key) |
| `manager_name` | string | Property manager/community name (best-effort) |
| `addresses` | string | Semicolon-separated list of unique addresses |
| `units` | int | Count of unique addresses for that phone |

### Example Output

```csv
phone,manager_name,addresses,units
4045551234,"ABC Property Management","123 Main St; 456 Oak Ave",2
4045559876,"","789 Pine Rd",1
```

## How It Works

### 1. Search & Discovery
- Constructs search URLs using `/houses/<city>-<state>` path
- Paginates through results up to `--max_pages`
- Uses one browser + one context for the whole run
- Reuses `resultsPage` for search pagination and `detailPage` for property links

### 2. Data Extraction (Multi-Fallback Strategy)

For each listing detail page, extraction follows this order:

#### Phone Extraction:
1. **JSON-LD**: Parse `<script type="application/ld+json">` blocks for `telephone`
2. **Selectors**: Look for `a[href^="tel:"]` and phone patterns in likely elements
3. **Regex**: Fallback to regex pattern `(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}`

#### Address Extraction:
1. **JSON-LD**: Extract `address` from JSON-LD (builds full address from components)
2. **Selectors**: `meta[itemprop=streetAddress]`, `address` tags, elements with `data-testid/class` containing "Address"
3. **Regex**: Fallback to regex for US street lines (street number + common suffix)

#### Manager Name Extraction:
1. **JSON-LD**: Extract `name` from JSON-LD
2. **Selectors**: Look for labels "Managed by", "Leasing Office", "Property Management", "Community"; else H1/H2 near address
3. **Regex**: Fallback to page title patterns

### 3. Normalization
- **Phone**: Strip non-digits, accept 10 or 11 digits (11 if starts with 1)
- **Address**: Title case, collapse whitespace
- **Skip**: Listings without a valid phone (phone is the unique key)

### 4. Persistence & Checkpointing

Uses SQLite database (`data.db`) with three tables:

- **phones**: `phone TEXT PRIMARY KEY, manager_name TEXT`
- **addresses**: `phone TEXT, address TEXT, UNIQUE(phone, address)`
- **crawled_urls**: `url TEXT PRIMARY KEY`

On each successful extraction:
- Upsert phone + optional manager_name (only overwrite if empty)
- Insert address if new
- Mark URL as crawled

Derive units per phone as `COUNT(addresses WHERE phone = ?)`.

### 5. Resume Capability

- On start, reads progress from database
- Skips URLs that have already been crawled
- Can be re-run to resume without duplicating work
- Exports partial results on interruption (Ctrl+C)

### 6. Export
- Materializes to pandas: one row per phone
- Sorts by phone ascending (deterministic order)
- Writes to CSV with columns: `phone`, `manager_name`, `addresses`, `units`
- Prints summary: unique phones found, total addresses, preview of first 5 rows

## Navigation & Robustness

- Uses one browser + one context for the whole run
- Reuses pages: `resultsPage` for search pagination, `detailPage` for property links
- For each results page:
  - `goto(url, timeout=60000, wait_until="domcontentloaded")`
  - `wait_for_selector` for listing links (10-15s)
  - Collect unique detail URLs; normalize them (remove query params)
- For each detail URL:
  - `goto(detail, timeout=60000, wait_until="domcontentloaded")`
  - Wait for body
  - Extract via JSON-LD → selectors → regex
- Retries: Wrap each `goto` in 3 attempts (1s, 2s, 4s backoff), then skip
- Pacing: Sleep `delay ± 0.6s` between page loads

## Project Structure

```
auto_loi_emails/
├── src/
│   ├── __init__.py
│   ├── main.py          # Entry point with CLI parsing
│   ├── scraper.py       # Playwright navigation and extraction
│   └── store.py         # SQLite database helpers
├── requirements.txt     # Python dependencies
├── README.md           # This file
├── data.db             # SQLite database (generated)
└── apartments_sfr.csv  # Output CSV (generated)
```

## Dependencies

- `playwright`: Browser automation with Chromium
- `beautifulsoup4`: HTML parsing
- `pandas`: CSV export
- `lxml`: Fast HTML parser

**Note**: After installing dependencies with `pip install -r requirements.txt`, you must also install the Playwright browsers:
```bash
python -m playwright install chromium
```

## Error Handling

The scraper includes:
- Graceful handling of network errors
- Timeout protection (60 seconds)
- Retry logic with incremental backoff
- Logging at INFO level for progress tracking
- Saves partial results if interrupted (Ctrl+C)
- Continues processing even if individual pages fail

## Troubleshooting

### No listings found
- Verify the city and state are correct
- Check if Apartments.com has listings for that area
- The search URL pattern uses `/houses/<city>-<state>/` which may vary

### Missing phone numbers
Some listings may not have phone numbers publicly available. These are skipped as phone is the required unique key.

### Rate limiting issues
If you encounter rate limiting:
- Increase the `--delay` parameter (e.g., `--delay 5.0`)
- The random jitter (±0.6 seconds) is automatically added to delays

### Resume capability
The scraper uses SQLite (`data.db`) to track progress. If interrupted:
- Re-run the same command to resume
- Already crawled URLs will be skipped
- Progress is saved incrementally

## Acceptance Criteria

The implementation satisfies the following requirements:

1. ✅ **Targets SFR**: Uses `/houses/<city>-<state>` path
2. ✅ **Extraction Order**: JSON-LD → Selectors → Regex fallbacks
3. ✅ **Phone Normalization**: 10 or 11 digits (11 if starts with 1)
4. ✅ **Address Normalization**: Title case, collapse whitespace
5. ✅ **Deduplication**: Keyed by phone, aggregates addresses
6. ✅ **Units Calculation**: Count of unique addresses per phone
7. ✅ **Navigation**: One browser + context, reusable pages
8. ✅ **Retries**: 3 attempts with 1s, 2s, 4s backoff
9. ✅ **Pacing**: Delay ± 0.6s jitter between navigations
10. ✅ **SQLite Persistence**: phones, addresses, crawled_urls tables
11. ✅ **Resume**: Can re-run without duplicating work
12. ✅ **CSV Output**: phone, manager_name, addresses, units columns
13. ✅ **Summary**: Prints unique phones, total addresses, preview

## License

This project is for educational/research purposes. Use responsibly and in compliance with all applicable laws and Terms of Service.
