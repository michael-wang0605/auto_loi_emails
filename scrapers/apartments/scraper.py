"""
Apartments.com scraper with Playwright navigation and multi-fallback extraction.
"""
import json
import logging
import random
import re
import time
from typing import Optional, Dict, List
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from src.store import Store

logger = logging.getLogger(__name__)

BASE_URL = "https://www.apartments.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def retry_goto(page: Page, url: str, max_retries: int = 5) -> bool:
    """
    Retry page.goto() with incremental backoff (2s, 4s, 8s, 16s).
    Returns True if successful, False if all retries failed.
    """
    backoff_delays = [2, 4, 8, 16]
    
    for attempt in range(max_retries):
        try:
            # Try networkidle first (most reliable), then load, then domcontentloaded
            wait_strategies = ["networkidle", "load", "domcontentloaded"]
            last_error = None
            
            for wait_strategy in wait_strategies:
                try:
                    # Add longer timeout for HTTP2 issues
                    page.goto(url, wait_until=wait_strategy, timeout=120000)
                    # Wait a bit more for any dynamic content
                    page.wait_for_timeout(3000)
                    return True
                except Exception as e:
                    last_error = e
                    error_str = str(e).lower()
                    # If it's an HTTP2 error, try with networkidle disabled
                    if 'http2' in error_str or 'protocol' in error_str:
                        try:
                            # Try with commit wait strategy (less strict)
                            page.goto(url, wait_until='commit', timeout=120000)
                            page.wait_for_timeout(5000)  # Longer wait for content
                            return True
                        except Exception:
                            pass
                    if wait_strategy == wait_strategies[-1]:
                        # Last strategy failed, raise the error
                        raise
                    # Try next strategy
                    continue
        except Exception as e:
            if attempt < max_retries - 1:
                delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                logger.warning(f"Failed to load {url} (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"Failed to load {url} after {max_retries} attempts: {e}")
                return False
    
    return False


def normalize_phone(phone: str) -> Optional[str]:
    """
    Normalize phone number: strip non-digits, keep 10 or 11 digits (11 if starts with 1).
    Returns None if invalid.
    """
    if not phone:
        return None
    
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', phone)
    
    # Accept 10 or 11 digits (11 if starts with 1)
    if len(digits) == 10:
        return digits
    elif len(digits) == 11 and digits[0] == '1':
        return digits
    
    return None


def parse_json_ld(soup: BeautifulSoup) -> Dict:
    """
    Parse all <script type="application/ld+json"> blocks.
    Returns dict with address, telephone, and name if found.
    """
    result = {
        'address': None,
        'telephone': None,
        'name': None
    }
    
    json_ld_scripts = soup.find_all('script', type='application/ld+json')
    
    for script in json_ld_scripts:
        try:
            content = script.string
            if not content:
                continue
            
            # Parse JSON-LD
            data = json.loads(content)
            
            # Handle both single objects and arrays
            if isinstance(data, list):
                data_list = data
            else:
                data_list = [data]
            
            for item in data_list:
                # Extract address
                if 'address' in item:
                    addr = item['address']
                    if isinstance(addr, dict):
                        # Build full address from components
                        parts = []
                        if 'streetAddress' in addr:
                            parts.append(addr['streetAddress'])
                        if 'addressLocality' in addr:
                            parts.append(addr['addressLocality'])
                        if 'addressRegion' in addr:
                            parts.append(addr['addressRegion'])
                        if 'postalCode' in addr:
                            parts.append(addr['postalCode'])
                        if parts:
                            result['address'] = ', '.join(parts)
                    elif isinstance(addr, str):
                        result['address'] = addr
                
                # Extract telephone
                if 'telephone' in item:
                    tel = item['telephone']
                    if isinstance(tel, str):
                        result['telephone'] = tel
                    elif isinstance(tel, list) and tel:
                        result['telephone'] = tel[0]
                
                # Extract name
                if 'name' in item:
                    result['name'] = item['name']
                
                # Also check for nested objects (e.g., RealEstateAgent)
                if 'realEstateAgent' in item:
                    agent = item['realEstateAgent']
                    if isinstance(agent, dict):
                        if 'telephone' in agent and not result['telephone']:
                            tel = agent['telephone']
                            if isinstance(tel, str):
                                result['telephone'] = tel
                        if 'name' in agent and not result['name']:
                            result['name'] = agent['name']
                
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Error parsing JSON-LD: {e}")
            continue
    
    return result


def extract_phone_from_selectors(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract phone using multiple selector fallbacks."""
    # Method 1: tel: links
    try:
        tel_links = page.query_selector_all('a[href^="tel:"]')
        for link in tel_links:
            href = link.get_attribute('href')
            if href:
                phone = href.replace('tel:', '').replace('+1', '').strip()
                normalized = normalize_phone(phone)
                if normalized:
                    return normalized
    except Exception:
        pass
    
    # Also try with BeautifulSoup
    if soup:
        tel_links = soup.find_all('a', href=re.compile(r'^tel:'))
        for link in tel_links:
            href = link.get('href', '')
            phone = href.replace('tel:', '').replace('+1', '').strip()
            normalized = normalize_phone(phone)
            if normalized:
                return normalized
    
    # Method 2: Look for phone patterns in likely elements
    try:
        likely_selectors = [
            'a[href*="phone"]',
            'a[href*="call"]',
            '[class*="contact"]',
            '[class*="phone"]',
            '[class*="call"]',
            '[id*="contact"]',
            '[id*="phone"]',
            '[data-testid*="phone"]',
            '[data-testid*="contact"]',
        ]
        
        for selector in likely_selectors:
            try:
                elements = page.query_selector_all(selector)
                for elem in elements:
                    try:
                        if elem.is_visible():
                            text = elem.inner_text()
                            if text:
                                # Try regex pattern
                                phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
                                matches = re.findall(phone_pattern, text)
                                for match in matches:
                                    normalized = normalize_phone(match)
                                    if normalized:
                                        return normalized
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass
    
    return None


def extract_phone_from_regex(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract phone using regex fallback on page text."""
    page_text = ""
    
    try:
        page_text = page.inner_text('body')
    except Exception:
        if soup:
            page_text = soup.get_text()
    
    if not page_text:
        return None
    
    # Phone regex pattern
    phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
    matches = re.findall(phone_pattern, page_text)
    
    for match in matches:
        normalized = normalize_phone(match)
        if normalized:
            return normalized
    
    return None


def extract_phone(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract phone using JSON-LD → selectors → regex fallback order."""
    # Method 1: JSON-LD
    json_ld_data = parse_json_ld(soup)
    if json_ld_data.get('telephone'):
        normalized = normalize_phone(json_ld_data['telephone'])
        if normalized:
            return normalized
    
    # Method 2: Selectors
    phone = extract_phone_from_selectors(page, soup)
    if phone:
        return phone
    
    # Method 3: Regex fallback
    phone = extract_phone_from_regex(page, soup)
    if phone:
        return phone
    
    return None


def extract_address_from_selectors(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract address using multiple selector fallbacks."""
    # Method 1: meta itemprop=streetAddress
    if soup:
        street_elem = soup.find('meta', itemprop='streetAddress')
        if street_elem and street_elem.get('content'):
            addr = street_elem.get('content').strip()
            if addr:
                return addr
    
    try:
        meta_elem = page.query_selector('meta[itemprop="streetAddress"]')
        if meta_elem:
            content = meta_elem.get_attribute('content')
            if content and content.strip():
                return content.strip()
    except Exception:
        pass
    
    # Method 2: address tag
    try:
        address_tags = page.query_selector_all('address')
        for tag in address_tags:
            try:
                text = tag.inner_text()
                if text and len(text) > 10:
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    if lines:
                        # Take first line, split by comma and take first part (street address)
                        addr = lines[0].split(',')[0].strip()
                        if addr:
                            return addr
            except Exception:
                continue
    except Exception:
        pass
    
    if soup:
        address_tags = soup.find_all('address')
        for tag in address_tags:
            text = tag.get_text(strip=True)
            if text and len(text) > 10:
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                if lines:
                    addr = lines[0].split(',')[0].strip()
                    if addr:
                        return addr
    
    # Method 3: data-testid/class containing "Address"
    try:
        selectors = [
            '[data-testid*="address" i]',
            '[data-testid*="Address"]',
            '[class*="address" i]',
            '[class*="Address"]',
        ]
        for selector in selectors:
            try:
                elem = page.query_selector(selector)
                if elem:
                    text = elem.inner_text()
                    if text and len(text) > 10 and len(text) < 200:
                        # Check if it looks like an address (has street number)
                        if re.search(r'^\d+', text):
                            return text.strip()
            except Exception:
                continue
    except Exception:
        pass
    
    return None


def extract_address_from_regex(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract address using regex fallback."""
    page_text = ""
    
    try:
        page_text = page.inner_text('body')
    except Exception:
        if soup:
            page_text = soup.get_text()
    
    if not page_text:
        return None
    
    # Address regex: street number + common suffix
    street_pattern = r'\d+\s+[A-Za-z0-9\s]+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|Ln|Lane|Ct|Court|Way|Pl|Place|Pkwy|Parkway)'
    matches = re.findall(street_pattern, page_text, re.IGNORECASE)
    if matches:
        # Take first match and normalize
        addr = matches[0].strip()
        # Title case and collapse whitespace
        addr = ' '.join(addr.split())
        return addr
    
    return None


def normalize_address(address: str) -> str:
    """Normalize address: title case, collapse whitespace."""
    if not address:
        return ""
    
    # Collapse whitespace
    address = ' '.join(address.split())
    
    # Title case (but preserve common abbreviations)
    words = address.split()
    normalized_words = []
    for word in words:
        # Don't capitalize common abbreviations
        if word.upper() in ['ST', 'AVE', 'RD', 'BLVD', 'LN', 'CT', 'DR', 'WAY', 'PL', 'PKWY']:
            normalized_words.append(word.upper())
        elif word.upper() in ['N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW']:
            normalized_words.append(word.upper())
        else:
            normalized_words.append(word.title())
    
    return ' '.join(normalized_words)


def extract_address(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract address using JSON-LD → selectors → regex fallback order."""
    # Method 1: JSON-LD
    json_ld_data = parse_json_ld(soup)
    if json_ld_data.get('address'):
        addr = json_ld_data['address']
        if addr:
            normalized = normalize_address(addr)
            return normalized
    
    # Method 2: Selectors
    address = extract_address_from_selectors(page, soup)
    if address:
        return normalize_address(address)
    
    # Method 3: Regex fallback
    address = extract_address_from_regex(page, soup)
    if address:
        return normalize_address(address)
    
    return None


def extract_manager_name_from_selectors(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract manager name using selector fallbacks."""
    page_text = ""
    
    try:
        page_text = page.inner_text('body')
    except Exception:
        if soup:
            page_text = soup.get_text()
    
    # Method 1: Look for labels "Managed by", "Leasing Office", etc.
    manager_keywords = [
        r'Managed by[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Leasing Office[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Property Management[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
        r'Community[:\s]+([A-Z][a-zA-Z\s&,.-]+)',
    ]
    
    for pattern in manager_keywords:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            # Clean up common suffixes
            name = re.sub(r'\s+(LLC|Inc|Corp|Management|Properties).*$', '', name, flags=re.IGNORECASE)
            if len(name) > 2 and len(name) < 80:
                return name
    
    # Method 2: H1/H2 near address
    try:
        h1_elements = page.query_selector_all('h1')
        for h1 in h1_elements:
            try:
                if h1.is_visible():
                    text = h1.inner_text().strip()
                    # If it looks like a name (not an address, not too long)
                    if (text and len(text) > 2 and len(text) < 80 and
                        not re.search(r'\d{5}', text) and  # Not a zip code
                        'apartments.com' not in text.lower() and
                        not re.search(r'^\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue)', text)):  # Not an address
                        return text
            except Exception:
                continue
    except Exception:
        pass
    
    if soup:
        headers = soup.find_all(['h1', 'h2'])
        for header in headers:
            text = header.get_text(strip=True)
            if (text and len(text) > 2 and len(text) < 80 and
                not re.search(r'\d{5}', text) and
                'apartments.com' not in text.lower() and
                not re.search(r'^\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue)', text)):
                return text
    
    return None


def extract_manager_name_from_regex(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract manager name using regex fallback."""
    # Get page title
    title_text = None
    try:
        title_text = page.title()
    except Exception:
        if soup:
            title = soup.find('title')
            if title:
                title_text = title.get_text()
    
    if title_text:
        # Look for patterns like "Name - Apartments.com" or "Name | Apartments"
        match = re.search(r'^([^-|]+)', title_text)
        if match:
            name = match.group(1).strip()
            # Remove common prefixes
            name = re.sub(r'^(Apartments?|Rentals?|Homes?|Properties?)\s+', '', name, flags=re.IGNORECASE)
            if len(name) > 2 and len(name) < 80 and 'apartments.com' not in name.lower():
                return name
    
    return None


def extract_manager_name(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract manager name using JSON-LD → selectors → regex fallback order."""
    # Method 1: JSON-LD
    json_ld_data = parse_json_ld(soup)
    if json_ld_data.get('name'):
        name = json_ld_data['name']
        if name and len(name) > 2 and len(name) < 80:
            return name
    
    # Method 2: Selectors
    name = extract_manager_name_from_selectors(page, soup)
    if name:
        return name
    
    # Method 3: Regex fallback
    name = extract_manager_name_from_regex(page, soup)
    if name:
        return name
    
    return None


def normalize_url(url: str) -> str:
    """Normalize URL by removing query parameters."""
    parsed = urlparse(url)
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        '',  # params
        '',  # query
        ''   # fragment
    ))
    return normalized.rstrip('/')


def get_listing_urls_from_search_page(page: Page, url: str) -> List[str]:
    """
    Extract listing detail page URLs from a search results page.
    First tries to extract from JSON-LD structured data, then falls back to HTML parsing.
    Returns empty list if all retries fail.
    """
    listing_urls = []
    
    if not retry_goto(page, url, max_retries=3):
        logger.warning(f"Failed to load search page {url} after all retries, continuing to next page")
        return listing_urls
    
    try:
        # Wait for DOM to render - try multiple selectors
        search_selectors = [
            ".placard",
            "[data-testid*='placard']",
            'article.placard',
            'a.property-link',
        ]
        
        waited = False
        for selector in search_selectors:
            try:
                page.wait_for_selector(selector, timeout=15000, state='visible')
                waited = True
                break
            except PlaywrightTimeoutError:
                continue
        
        if not waited:
            logger.warning(f"None of the search selectors found on {url}, but continuing anyway")
        
        # Additional wait for dynamic content
        page.wait_for_timeout(2000)
        
        # Method 1: Extract from JSON-LD structured data (most reliable)
        try:
            json_ld_scripts = page.query_selector_all('script[type="application/ld+json"]')
            for script in json_ld_scripts:
                try:
                    script_text = script.inner_text()
                    if not script_text:
                        continue
                    
                    data = json.loads(script_text)
                    
                    # Handle array of JSON-LD objects
                    json_ld_items = data if isinstance(data, list) else [data]
                    
                    for item in json_ld_items:
                        # Look for ItemList with itemListElement
                        if item.get('@type') == 'CollectionPage' and 'mainEntity' in item:
                            main_entity = item['mainEntity']
                            if isinstance(main_entity, dict) and main_entity.get('@type') == 'ItemList':
                                items = main_entity.get('itemListElement', [])
                                for list_item in items:
                                    if isinstance(list_item, dict) and 'item' in list_item:
                                        item_data = list_item['item']
                                        if isinstance(item_data, dict) and 'url' in item_data:
                                            listing_url = item_data['url']
                                            if listing_url and listing_url.startswith(BASE_URL):
                                                normalized = normalize_url(listing_url)
                                                if normalized and normalized not in listing_urls:
                                                    listing_urls.append(normalized)
                except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
                    logger.debug(f"Error parsing JSON-LD script: {e}")
                    continue
        except Exception as e:
            logger.debug(f"Error extracting from JSON-LD: {e}")
        
        # Method 2: Fall back to HTML parsing if JSON-LD didn't yield results
        if not listing_urls:
            html = page.content()
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find listing links - multiple selector patterns
            link_selectors = [
                'article.placard a.property-link',
                'a.property-link',
                'article.placard a[href*="/"]',
            ]
            
            seen_urls = set()
            for selector in link_selectors:
                links = soup.select(selector)
                for link in links:
                    href = link.get('href')
                    if href:
                        full_url = urljoin(BASE_URL, href)
                        normalized = normalize_url(full_url)
                        
                        # Check if it's a detail page URL (not a search/filter page)
                        # Detail pages have pattern: https://www.apartments.com/<address-slug>/<id>/
                        path = normalized.replace(BASE_URL, '').strip('/')
                        path_parts = [p for p in path.split('/') if p]
                        
                        # Valid detail page: has at least 2 path segments (address + ID)
                        # Exclude: /houses/, /blog/, /local-guide/, etc.
                        excluded_paths = ['houses', 'blog', 'local-guide', 'sitemap', 'grow', 'about', 'parks-and-recreation']
                        
                        if (normalized not in seen_urls and
                            normalized.startswith(BASE_URL) and
                            len(path_parts) >= 2 and
                            not any(excluded in path for excluded in excluded_paths) and
                            path_parts[0] != 'houses'):
                            seen_urls.add(normalized)
                            listing_urls.append(normalized)
        
        logger.info(f"Found {len(listing_urls)} listings on search page")
        
    except Exception as e:
        logger.error(f"Error extracting listing URLs from search page {url}: {e}")
    
    return listing_urls


def get_next_page_url(page: Page) -> Optional[str]:
    """
    Extract the next page URL from pagination on the current page.
    Returns None if no next page is found.
    """
    try:
        # Look for "Next" button/link using multiple methods
        # Method 1: Look for aria-label containing "Next"
        try:
            next_link = page.query_selector('a[aria-label*="Next" i]')
            if next_link:
                href = next_link.get_attribute('href')
                if href and href != '#':
                    full_url = urljoin(BASE_URL, href)
                    return normalize_url(full_url)
        except Exception:
            pass
        
        # Method 2: Look for link with text "Next" in pagination nav
        try:
            nav = page.query_selector('nav[aria-label*="Search Results" i], nav.paging, nav#paging')
            if nav:
                # Find all links in nav
                links = nav.query_selector_all('a')
                for link in links:
                    text = link.inner_text().strip()
                    if text.lower() == 'next':
                        href = link.get_attribute('href')
                        if href and href != '#':
                            full_url = urljoin(BASE_URL, href)
                            return normalize_url(full_url)
        except Exception:
            pass
        
        # Method 3: Extract current page number and find next page link
        try:
            nav = page.query_selector('nav[aria-label*="Search Results" i], nav.paging, nav#paging')
            if nav:
                # Find the current page (active link or aria-label="Current Page")
                current_page_elem = nav.query_selector('a.active, a[aria-label*="Current" i]')
                if current_page_elem:
                    current_text = current_page_elem.inner_text().strip()
                    try:
                        current_page = int(current_text)
                        # Find link with next page number
                        next_page = current_page + 1
                        links = nav.query_selector_all('a')
                        for link in links:
                            link_text = link.inner_text().strip()
                            link_data_page = link.get_attribute('data-page')
                            if (link_text == str(next_page) or 
                                (link_data_page and int(link_data_page) == next_page)):
                                href = link.get_attribute('href')
                                if href and href != '#':
                                    full_url = urljoin(BASE_URL, href)
                                    return normalize_url(full_url)
                    except (ValueError, AttributeError):
                        pass
        except Exception:
            pass
        
    except Exception as e:
        logger.debug(f"Error finding next page URL: {e}")
    
    return None


def scrape_listing_detail(page: Page, url: str, store: Store) -> Optional[Dict]:
    """
    Scrape a single listing detail page and extract data.
    Returns None if all retries fail or no phone found.
    """
    # Normalize URL before checking
    normalized_url = normalize_url(url)
    
    # Check if already crawled
    if store.is_url_crawled(normalized_url):
        logger.debug(f"URL already crawled: {normalized_url}, skipping")
        return None
    
    if not retry_goto(page, url, max_retries=3):
        logger.warning(f"Failed to load listing detail page {url} after all retries, skipping")
        return None
    
    try:
        # Wait for page to load
        detail_selectors = [
            'address',
            'h1',
            '[data-testid*="address"]',
            '.property-header',
            '.listing-details',
            'body',
        ]
        
        waited = False
        for selector in detail_selectors:
            try:
                page.wait_for_selector(selector, timeout=10000, state='visible')
                waited = True
                break
            except PlaywrightTimeoutError:
                continue
        
        if not waited:
            logger.warning(f"None of the detail selectors found on {url}, but continuing anyway")
        
        # Additional wait for dynamic content
        page.wait_for_timeout(1000)
        
        # Get page content for BeautifulSoup
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract phone (required)
        phone = extract_phone(page, soup)
        if not phone:
            logger.debug(f"No phone found for {normalized_url}, skipping")
            store.mark_url_crawled(normalized_url)  # Mark as crawled even if no phone
            return None
        
        # Extract address (best-effort)
        address = extract_address(page, soup)
        
        # Extract manager name (best-effort)
        manager_name = extract_manager_name(page, soup)
        
        logger.info(f"Extracted: {phone} - {address or 'N/A'} - {manager_name or 'N/A'}")
        
        # Mark URL as crawled (using normalized URL)
        store.mark_url_crawled(normalized_url)
        
        return {
            'phone': phone,
            'address': address or '',
            'manager_name': manager_name or ''
        }
        
    except Exception as e:
        logger.error(f"Error scraping listing {url}: {e}")
        return None


def export_to_csv_incremental(store: Store, output_path: str):
    """
    Export aggregated data to CSV incrementally.
    This is a standalone function to avoid circular imports.
    """
    import pandas as pd
    
    # Get all phones with their data
    phones_data = store.get_all_phones()
    
    if not phones_data:
        return
    
    # Prepare data for CSV
    records = []
    for data in phones_data:
        # Join addresses with semicolon, deterministic order (sorted)
        addresses_str = '; '.join(sorted(data['addresses'])) if data['addresses'] else ''
        
        # Use agent_name (fallback to manager_name for old data)
        manager_name = data.get('agent_name') or data.get('manager_name') or ''
        
        records.append({
            'phone': data['phone'],
            'manager_name': manager_name,
            'addresses': addresses_str,
            'units': data['units']
        })
    
    # Create DataFrame and sort by phone ascending
    df = pd.DataFrame(records)
    df = df.sort_values(by='phone').reset_index(drop=True)
    
    # Write to CSV
    df.to_csv(output_path, index=False)


def scrape_city(
    city: str,
    state: str,
    max_pages: int,
    delay: float,
    target_phones: int,
    headless: bool,
    proxy: Optional[str],
    store: Store,
    output_path: str = "apartments_sfr.csv"
) -> None:
    """
    Main scraping function using Playwright.
    Uses one browser + one context for the whole run.
    Reuses resultsPage for search pagination and detailPage for visiting property links.
    """
    logger.info(f"Starting scrape for {city}, {state} (max {max_pages} pages, target {target_phones} phones)")
    
    # Use one browser + one context for the whole run
    with sync_playwright() as p:
        # Launch Chromium browser with options to reduce detection
        browser_options = {
            'headless': headless,
            'args': [
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-site-isolation-trials',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
            ]
        }
        if proxy:
            browser_options['proxy'] = {'server': proxy}
        
        # Launch one browser instance for the entire run
        # Use Chrome instead of Chromium for better compatibility (like Zillow scraper)
        try:
            browser = p.chromium.launch(channel="chrome", headless=headless, args=browser_options['args'])
            logger.info("Chrome browser launched")
        except Exception:
            # Fallback to Chromium if Chrome not available
            browser = p.chromium.launch(**browser_options)
            logger.info("Chromium browser launched")
        
        # Create one context for the entire run with enhanced headers
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            timezone_id='America/New_York',
            # Add stealth options
            java_script_enabled=True,
            bypass_csp=True,
            ignore_https_errors=False,
            extra_http_headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate',  # Removed 'br' (Brotli) which requires HTTP/2
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            },
        )
        logger.info("Browser context created")
        
        # Add stealth script to hide automation
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        # Reuse two pages for the entire run:
        # - results_page: for navigating search result pages
        # - detail_page: for visiting individual listing detail pages
        results_page = context.new_page()
        detail_page = context.new_page()
        logger.info("Pages created - reusing for entire run")
        
        try:
            # Collect listing URLs from search pages
            all_listing_urls = []
            city_normalized = city.lower().replace(' ', '-').replace(',', '').replace("'", "")
            state_normalized = state.lower()  # Apartments.com uses lowercase state
            
            # Start with page 1 URL
            current_url = f"{BASE_URL}/houses/{city_normalized}-{state_normalized}"
            page_num = 0
            
            while page_num < max_pages:
                # Stop if we've reached target phones
                if store.get_unique_phones_count() >= target_phones:
                    logger.info(f"Reached target of {target_phones} phones, stopping")
                    break
                
                page_num += 1
                logger.info(f"Fetching page {page_num}: {current_url}")
                
                listing_urls = get_listing_urls_from_search_page(results_page, current_url)
                
                if listing_urls:
                    all_listing_urls.extend(listing_urls)
                    logger.info(f"Found {len(listing_urls)} listings on page {page_num}, total so far: {len(all_listing_urls)}")
                else:
                    logger.warning(f"No listings found on page {page_num}, but continuing to next page")
                
                # Get next page URL from pagination on the current page
                if page_num < max_pages:
                    next_url = get_next_page_url(results_page)
                    if next_url:
                        current_url = next_url
                        # Rate limiting with jitter
                        jitter = random.uniform(-0.6, 0.6)
                        wait_time = max(0.1, delay + jitter)
                        time.sleep(wait_time)
                    else:
                        logger.info("No next page found, stopping pagination")
                        break
                else:
                    break
            
            logger.info(f"Total listing URLs collected: {len(all_listing_urls)}")
            
            # Scrape each listing detail page
            for i, listing_url in enumerate(all_listing_urls, 1):
                # Stop if we've reached target phones
                if store.get_unique_phones_count() >= target_phones:
                    logger.info(f"Reached target of {target_phones} phones, stopping")
                    break
                
                # Skip if already crawled (normalize first)
                normalized_listing_url = normalize_url(listing_url)
                if store.is_url_crawled(normalized_listing_url):
                    logger.debug(f"Skipping already crawled URL: {normalized_listing_url}")
                    continue
                
                logger.info(f"Scraping listing {i}/{len(all_listing_urls)}: {listing_url}")
                
                listing_data = scrape_listing_detail(detail_page, listing_url, store)
                
                if listing_data:
                    phone = listing_data['phone']
                    address = listing_data['address']
                    manager_name = listing_data['manager_name']
                    
                    # Upsert phone and manager name
                    store.upsert_phone(phone, manager_name)
                    
                    # Add address if present
                    if address:
                        store.add_address(phone, address)
                    
                    logger.info(f"Progress: {store.get_unique_phones_count()}/{target_phones} unique phones")
                    
                    # Export to CSV incrementally after each successful extraction
                    # This ensures we have constant updates even if interrupted
                    try:
                        export_to_csv_incremental(store, output_path)
                        logger.debug(f"CSV updated with {store.get_unique_phones_count()} phones")
                    except Exception as e:
                        logger.debug(f"Could not export CSV incrementally: {e}")
                
                # Rate limiting with jitter
                if i < len(all_listing_urls):
                    jitter = random.uniform(-0.6, 0.6)
                    wait_time = max(0.1, delay + jitter)
                    time.sleep(wait_time)
        
        finally:
            # Clean up pages and browser
            results_page.close()
            detail_page.close()
            browser.close()
    
    logger.info("Scraping completed")

