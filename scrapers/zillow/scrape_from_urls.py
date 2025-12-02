#!/usr/bin/env python3
"""
Scrape data from Zillow property URLs stored in a CSV file.
Reads URLs from CSV, navigates to each, and extracts phone, address, manager_name.
"""
import argparse
import csv
import logging
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional, Dict, List
from urllib.parse import urlparse, urlunparse

import pandas as pd
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup

# Import store from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.store import Store

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.zillow.com"
# Use a more recent Chrome user agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def normalize_url(url: str) -> str:
    """Normalize URL by removing query parameters and fragments."""
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


def normalize_phone(phone: str) -> Optional[str]:
    """Normalize phone number: strip non-digits, keep 10 or 11 digits (11 if starts with 1)."""
    if not phone:
        return None
    
    digits = re.sub(r'\D', '', phone)
    
    if len(digits) == 10:
        return digits
    elif len(digits) == 11 and digits[0] == '1':
        return digits
    
    return None


def parse_json_ld(soup: BeautifulSoup) -> Dict:
    """Parse all <script type="application/ld+json"> blocks."""
    result = {
        'address': None,
        'telephone': None,
        'name': None
    }
    
    json_ld_scripts = soup.find_all('script', type='application/ld+json')
    
    for script in json_ld_scripts:
        try:
            import json
            content = script.string
            if not content:
                continue
            
            data = json.loads(content)
            
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        data = item
                        break
            
            if not isinstance(data, dict):
                continue
            
            # Extract address
            if 'address' in data:
                addr_data = data['address']
                if isinstance(addr_data, dict):
                    parts = []
                    if addr_data.get('streetAddress'):
                        parts.append(addr_data['streetAddress'])
                    if addr_data.get('addressLocality'):
                        parts.append(addr_data['addressLocality'])
                    if addr_data.get('addressRegion'):
                        parts.append(addr_data['addressRegion'])
                    if addr_data.get('postalCode'):
                        parts.append(addr_data['postalCode'])
                    if parts:
                        result['address'] = ', '.join(parts)
                elif isinstance(addr_data, str):
                    result['address'] = addr_data
            
            # Extract telephone
            if 'telephone' in data:
                result['telephone'] = data['telephone']
            
            # Extract name
            if 'name' in data:
                result['name'] = data['name']
                
        except Exception as e:
            logger.debug(f"Error parsing JSON-LD: {e}")
            continue
    
    return result


def extract_phone_from_selectors(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract phone using selector fallbacks."""
    # Method 1: ds-listing-agent-info container (highest priority - contains both name and phone)
    try:
        agent_info_containers = page.query_selector_all('.ds-listing-agent-info, [class*="ds-listing-agent-info"]')
        for container in agent_info_containers:
            try:
                if container.is_visible():
                    text = container.inner_text().strip()
                    if text:
                        # Extract phone from container
                        phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
                        matches = re.findall(phone_pattern, text)
                        for match in matches:
                            normalized = normalize_phone(match)
                            if normalized:
                                logger.debug(f"Found phone via ds-listing-agent-info container: {normalized}")
                                return normalized
            except Exception:
                continue
    except Exception:
        pass
    
    # Also try with BeautifulSoup
    if soup:
        agent_info_containers = soup.find_all(class_=re.compile(r'ds-listing-agent-info'))
        for container in agent_info_containers:
            text = container.get_text(strip=True)
            if text:
                phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
                matches = re.findall(phone_pattern, text)
                for match in matches:
                    normalized = normalize_phone(match)
                    if normalized:
                        logger.debug(f"Found phone via ds-listing-agent-info container (soup): {normalized}")
                        return normalized
    
    # Method 2: ds-listing-agent-info-text (fallback - specific text element)
    try:
        agent_info_elements = page.query_selector_all('li.ds-listing-agent-info-text, .ds-listing-agent-info-text')
        for elem in agent_info_elements:
            try:
                if elem.is_visible():
                    text = elem.inner_text().strip()
                    if text:
                        # Extract phone from text
                        phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
                        matches = re.findall(phone_pattern, text)
                        for match in matches:
                            normalized = normalize_phone(match)
                            if normalized:
                                logger.debug(f"Found phone via ds-listing-agent-info-text: {normalized}")
                                return normalized
            except Exception:
                continue
    except Exception:
        pass
    
    # Also try with BeautifulSoup
    if soup:
        agent_info_elements = soup.find_all('li', class_='ds-listing-agent-info-text')
        for elem in agent_info_elements:
            text = elem.get_text(strip=True)
            if text:
                phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
                matches = re.findall(phone_pattern, text)
                for match in matches:
                    normalized = normalize_phone(match)
                    if normalized:
                        logger.debug(f"Found phone via ds-listing-agent-info-text (soup): {normalized}")
                        return normalized
    
    # Method 2: tel: links
    try:
        tel_links = page.query_selector_all('a[href^="tel:"]')
        for link in tel_links:
            try:
                href = link.get_attribute('href')
                if href:
                    phone = href.replace('tel:', '').replace('+1', '').strip()
                    normalized = normalize_phone(phone)
                    if normalized:
                        return normalized
            except Exception:
                continue
    except Exception:
        pass
    
    if soup:
        tel_links = soup.find_all('a', href=re.compile(r'^tel:'))
        for link in tel_links:
            href = link.get('href', '')
            phone = href.replace('tel:', '').replace('+1', '').strip()
            normalized = normalize_phone(phone)
            if normalized:
                return normalized
    
    # Method 2: Elements with phone-like text
    try:
        selectors = [
            'a[href*="phone"]',
            'a[href*="call"]',
            '[class*="phone"]',
            '[class*="contact"]',
            '[data-testid*="phone"]',
            '[data-testid*="contact"]',
        ]
        for selector in selectors:
            try:
                elements = page.query_selector_all(selector)
                for elem in elements:
                    try:
                        if elem.is_visible():
                            text = elem.inner_text()
                            if text:
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
    
    phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
    matches = re.findall(phone_pattern, page_text)
    
    for match in matches:
        normalized = normalize_phone(match)
        if normalized:
            return normalized
    
    return None


def extract_agent_business_phone_from_card(page: Page, soup: BeautifulSoup) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract agent name, business name, and phone from the ds-listing-agent-info container.
    Simply checks for specific classes: ds-listing-agent-display-name and ds-listing-agent-business-name.
    Returns (agent_name, business_name, phone) tuple.
    """
    # Find ds-listing-agent-info container
    try:
        agent_info_containers = page.query_selector_all('.ds-listing-agent-info, [class*="ds-listing-agent-info"]')
        for container in agent_info_containers:
            try:
                if container.is_visible():
                    # Extract phone from container text
                    container_text = container.inner_text()
                    phone_pattern = r'(?:\+?1[\s\-\.]?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}'
                    phone_matches = re.findall(phone_pattern, container_text)
                    
                    phone = None
                    for match in phone_matches:
                        normalized = normalize_phone(match)
                        if normalized:
                            phone = normalized
                            break
                    
                    # Extract agent name from ds-listing-agent-display-name
                    agent_name = None
                    try:
                        display_name_elem = container.query_selector('.ds-listing-agent-display-name, [class*="ds-listing-agent-display-name"]')
                        if display_name_elem:
                            agent_name = display_name_elem.inner_text().strip()
                            if agent_name:
                                agent_name = clean_manager_name(agent_name)
                    except Exception:
                        pass
                    
                    # Extract business name from ds-listing-agent-business-name
                    business_name = None
                    try:
                        business_name_elem = container.query_selector('.ds-listing-agent-business-name, [class*="ds-listing-agent-business-name"]')
                        if business_name_elem:
                            business_name = business_name_elem.inner_text().strip()
                            if business_name:
                                business_name = clean_manager_name(business_name)
                    except Exception:
                        pass
                    
                    # Return if we found at least a phone (names are optional, both can exist)
                    if phone:
                        logger.debug(f"Found from ds-listing-agent-info: phone={phone}, agent={agent_name}, business={business_name}")
                        return (agent_name, business_name, phone)
            except Exception:
                continue
    except Exception:
        pass
    
    # Fallback: Just return None for names, phone extraction will be handled by other methods
    return (None, None, None)


def extract_phone(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract phone using property card → JSON-LD → selectors → regex fallback order."""
    # Method 1: Try to get phone from property card (along with agent/business name)
    _, _, phone = extract_agent_business_phone_from_card(page, soup)
    if phone:
        return phone
    
    # Method 2: JSON-LD
    json_ld_data = parse_json_ld(soup)
    if json_ld_data.get('telephone'):
        normalized = normalize_phone(json_ld_data['telephone'])
        if normalized:
            return normalized
    
    # Method 3: Selectors
    phone = extract_phone_from_selectors(page, soup)
    if phone:
        return phone
    
    # Method 4: Regex fallback
    phone = extract_phone_from_regex(page, soup)
    if phone:
        return phone
    
    return None


def normalize_address(address: str) -> str:
    """Normalize address: title case, collapse whitespace."""
    if not address:
        return ""
    
    address = ' '.join(address.split())
    
    words = address.split()
    normalized_words = []
    for word in words:
        if word.upper() in ['ST', 'AVE', 'RD', 'BLVD', 'LN', 'CT', 'DR', 'WAY', 'PL', 'PKWY']:
            normalized_words.append(word.upper())
        elif word.upper() in ['N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW']:
            normalized_words.append(word.upper())
        else:
            normalized_words.append(word.title())
    
    return ' '.join(normalized_words)


def extract_address_from_selectors(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract address using multiple selector fallbacks."""
    # Method 1: meta itemprop=streetAddress
    if soup:
        street_elem = soup.find('meta', itemprop='streetAddress')
        if street_elem and street_elem.get('content'):
            addr = street_elem.get('content').strip()
            if addr:
                return addr
        
        # Also check for Text-c11n class in soup
        text_elem = soup.find(class_=re.compile(r'Text-c11n.*sc-aiai24.*cEHZrB|cEHZrB'))
        if not text_elem:
            text_elem = soup.find(class_=re.compile(r'cEHZrB'))
        if text_elem:
            text = text_elem.get_text(strip=True)
            if text and len(text) > 10 and len(text) < 200:
                if re.search(r'^\d+', text):
                    lines = text.split('\n')
                    addr = lines[0].split(',')[0].strip()
                    if addr and not any(word in addr.lower() for word in ['photos', 'accepts', 'zillow', 'appl']):
                        logger.debug(f"Found address via Text-c11n class (soup): {addr}")
                        return addr
    
    try:
        meta_elem = page.query_selector('meta[itemprop="streetAddress"]')
        if meta_elem:
            content = meta_elem.get_attribute('content')
            if content and content.strip():
                return content.strip()
    except Exception:
        pass
    
    # Method 1.5: Check for Text-c11n-8-109-3__sc-aiai24-0 cEHZrB class (specific Zillow address class)
    try:
        # Try exact class match first
        text_elem = page.query_selector('.Text-c11n-8-109-3__sc-aiai24-0.cEHZrB, [class*="Text-c11n"][class*="sc-aiai24"][class*="cEHZrB"]')
        if not text_elem:
            # Try with just the pattern parts
            text_elem = page.query_selector('[class*="Text-c11n"][class*="cEHZrB"]')
        if not text_elem:
            # Try with just sc-aiai24 pattern
            text_elem = page.query_selector('[class*="sc-aiai24"][class*="cEHZrB"]')
        if not text_elem:
            # Try with just cEHZrB
            text_elem = page.query_selector('[class*="cEHZrB"]')
        
        if text_elem:
            text = text_elem.inner_text().strip()
            if text and len(text) > 10 and len(text) < 200:
                # Must start with a number (street address)
                if re.search(r'^\d+', text):
                    lines = text.split('\n')
                    addr = lines[0].split(',')[0].strip()
                    # Exclude if it contains UI text
                    if addr and not any(word in addr.lower() for word in ['photos', 'accepts', 'zillow', 'appl']):
                        logger.debug(f"Found address via Text-c11n class: {addr}")
                        return addr
    except Exception:
        pass
    
    # Method 2: Zillow-specific address selectors
    address_selectors = [
        # Specific Zillow address class (Text-c11n-8-109-3__sc-aiai24-0 cEHZrB)
        '.Text-c11n-8-109-3__sc-aiai24-0.cEHZrB',
        '[class*="Text-c11n"][class*="sc-aiai24"][class*="cEHZrB"]',
        '[class*="Text-c11n"][class*="cEHZrB"]',
        '[class*="sc-aiai24"][class*="cEHZrB"]',
        '[class*="cEHZrB"]',
        # Other address selectors
        'h1[data-test="property-card-addr"]',
        '[data-test="property-card-addr"]',
        '.PropertyHeaderContainer h1',
        'h1.address',
        '[data-testid="address"]',
        '[class*="ds-address"]',  # Zillow data science class
        '[class*="AddressHeader"]',
    ]
    
    for selector in address_selectors:
        try:
            elem = page.query_selector(selector)
            if elem:
                text = elem.inner_text().strip()
                if text and len(text) > 10 and len(text) < 200:
                    # Must start with a number (street address)
                    if re.search(r'^\d+', text):
                        lines = text.split('\n')
                        addr = lines[0].split(',')[0].strip()
                        # Exclude if it contains UI text
                        if addr and not any(word in addr.lower() for word in ['photos', 'accepts', 'zillow', 'appl']):
                            return addr
        except Exception:
            continue
    
    # Method 2b: Try h1 but be more careful
    try:
        h1_elem = page.query_selector('h1')
        if h1_elem:
            text = h1_elem.inner_text().strip()
            if text and len(text) > 10 and len(text) < 200:
                # Must start with a number and not contain UI words
                if (re.search(r'^\d+', text) and 
                    not any(word in text.lower() for word in ['photos', 'accepts', 'zillow', 'appl', 'verified'])):
                    lines = text.split('\n')
                    addr = lines[0].split(',')[0].strip()
                    if addr:
                        return addr
    except Exception:
        pass
    
    # Method 3: address tag
    ui_words = ['photos', 'accepts', 'zillow', 'appl', 'verified', 'source']
    try:
        address_tags = page.query_selector_all('address')
        for tag in address_tags:
            try:
                text = tag.inner_text()
                if text and len(text) > 10:
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    if lines:
                        addr = lines[0].split(',')[0].strip()
                        # Must start with number and not contain UI words
                        if (addr and re.search(r'^\d+', addr) and 
                            not any(word in addr.lower() for word in ui_words)):
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
                    # Must start with number and not contain UI words
                    if (addr and re.search(r'^\d+', addr) and 
                        not any(word in addr.lower() for word in ui_words)):
                        return addr
    
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
    
    # UI words to exclude from addresses
    ui_words = ['photos', 'accepts', 'zillow', 'appl', 'verified', 'source', 'manage', 'rentals', 
                'advertise', 'contacts', 'list', 'criteria', 'sets', 'property manager']
    
    street_pattern = r'\d+\s+[A-Za-z0-9\s]+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|Ln|Lane|Ct|Court|Way|Pl|Place|Pkwy|Parkway)'
    matches = re.findall(street_pattern, page_text, re.IGNORECASE)
    for match in matches:
        addr = match.strip()
        addr = ' '.join(addr.split())
        # Exclude if it contains UI words
        if not any(word in addr.lower() for word in ui_words):
            # Must be reasonable length (not too short, not too long)
            # Must look like a real address (has street suffix)
            if 10 <= len(addr) <= 100 and re.search(r'(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|Ln|Lane|Ct|Court|Way|Pl|Place|Pkwy|Parkway)', addr, re.IGNORECASE):
                return addr
    
    return None


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
    """Extract manager name using selector fallbacks. Excludes city/state names."""
    # Method 1: Check for business name element (ds-listing-agent-business-name) - highest priority
    try:
        business_name_elems = page.query_selector_all('.ds-listing-agent-business-name, [class*="ds-listing-agent-business-name"]')
        for elem in business_name_elems:
            try:
                if elem.is_visible():
                    business_name = elem.inner_text().strip()
                    if business_name and len(business_name) >= 3 and len(business_name) <= 80:
                        # Validate it's not a city/state/address
                        if not re.match(r'^[A-Z][a-z]+\s+[A-Z]{2}$', business_name) and not re.match(r'^\d+', business_name):
                            logger.debug(f"Found manager name via ds-listing-agent-business-name: {business_name}")
                            return clean_manager_name(business_name)
            except Exception:
                continue
    except Exception:
        pass
    
    # Also try with BeautifulSoup
    if soup:
        business_name_elems = soup.find_all(class_=re.compile(r'ds-listing-agent-business-name'))
        for elem in business_name_elems:
            business_name = elem.get_text(strip=True)
            if business_name and len(business_name) >= 3 and len(business_name) <= 80:
                if not re.match(r'^[A-Z][a-z]+\s+[A-Z]{2}$', business_name) and not re.match(r'^\d+', business_name):
                    logger.debug(f"Found manager name via ds-listing-agent-business-name (soup): {business_name}")
                    return clean_manager_name(business_name)
    
    page_text = ""
    
    try:
        page_text = page.inner_text('body')
    except Exception:
        if soup:
            page_text = soup.get_text()
    
    # Common city/state patterns to exclude
    city_state_patterns = [
        r'^[A-Z][a-z]+\s+[A-Z]{2}$',  # "Atlanta GA"
        r'^[A-Z][a-z]+,\s+[A-Z]{2}$',  # "Atlanta, GA"
        r'^\d{5}$',  # Zip codes
        r'^\d+\s+[A-Z]',  # Addresses starting with numbers
    ]
    
    def is_valid_name(text: str) -> bool:
        """Check if text looks like a valid manager/agent name (not city/state/address)."""
        if not text or len(text) < 2 or len(text) > 80:
            return False
        
        text_clean = text.strip()
        
        # Exclude common non-name patterns
        if any(re.match(pattern, text_clean) for pattern in city_state_patterns):
            return False
        
        # Exclude if it contains zip code
        if re.search(r'\d{5}', text_clean):
            return False
        
        # Exclude if it's an address (starts with number)
        if re.search(r'^\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive)', text_clean):
            return False
        
        # Exclude if it contains zillow.com
        if 'zillow.com' in text_clean.lower():
            return False
        
        # Exclude if it's just a city name (common cities)
        common_cities = ['atlanta', 'decatur', 'sandy springs', 'roswell', 'alpharetta']
        if text_clean.lower() in common_cities:
            return False
        
        # Exclude if it's just state abbreviation
        if text_clean.upper() in ['GA', 'AL', 'FL', 'NC', 'SC', 'TN']:
            return False
        
        # Exclude if it's too short or looks like location
        if len(text_clean.split()) <= 1 and len(text_clean) < 5:
            return False
        
        # Exclude generic words that aren't names
        generic_words = ['manager', 'agent', 'owner', 'contact', 'details', 'more', 'about', 'this', 'home', 
                        'features', 'exterior', 'interior', 'photos', 'zillow', 'appl', 'accepts']
        if text_clean.lower() in generic_words:
            return False
        
        # Exclude if it contains generic phrases
        generic_phrases = ['for more', 'details about', 'this home', 'exterior features', 'manager features',
                          'property owner', 'rentals advertise', 'get help', 'sign in', 'back to search',
                          'listed by property', 'accepts zillow']
        if any(phrase in text_clean.lower() for phrase in generic_phrases):
            return False
        
        # Exclude if it's just "manager Features" or similar
        if re.match(r'^(manager|agent|owner|contact)\s+(features|details|photos)', text_clean, re.IGNORECASE):
            return False
        
        # Exclude phrases that don't look like names (e.g., "is responsible for", "pays for")
        non_name_phrases = ['is responsible', 'pays for', 'responsible for', 'management company', 
                           'listed by management', 'for lawn care', 'pest control']
        if any(phrase in text_clean.lower() for phrase in non_name_phrases):
            return False
        
        # Exclude if it's a sentence fragment (contains verbs like "is", "pays", "responsible")
        if re.search(r'\b(is|pays|responsible|for|lawn|care|pest|control)\b', text_clean.lower()):
            # But allow if it's clearly a name (e.g., "John Smith" doesn't match this pattern well)
            # Only exclude if it's clearly a sentence
            if len(text_clean.split()) > 3:  # Long phrases are likely sentences
                return False
        
        return True
    
    # Method 1: Look for labels with manager/agent keywords
    # Extract the name that comes AFTER the label
    manager_keywords = [
        r'Managed by[:\s]+([A-Z][a-zA-Z\s&,.-]{2,60})(?:\s|$|,|\.)',
        r'Leasing Office[:\s]+([A-Z][a-zA-Z\s&,.-]{2,60})(?:\s|$|,|\.)',
        r'Property Management[:\s]+([A-Z][a-zA-Z\s&,.-]{2,60})(?:\s|$|,|\.)',
        r'Listing Agent[:\s]+([A-Z][a-zA-Z\s&,.-]{2,60})(?:\s|$|,|\.)',
        r'Contact[:\s]+([A-Z][a-zA-Z\s&,.-]{2,60})(?:\s|$|,|\.)',
        r'Agent[:\s]+([A-Z][a-zA-Z\s&,.-]{2,60})(?:\s|$|,|\.)',
        r'Landlord[:\s]+([A-Z][a-zA-Z\s&,.-]{2,60})(?:\s|$|,|\.)',
        r'Owner[:\s]+([A-Z][a-zA-Z\s&,.-]{2,60})(?:\s|$|,|\.)',
        r'Listed by[:\s]+([A-Z][a-zA-Z\s&,.-]{2,60})(?:\s|$|,|\.)',
    ]
    
    for pattern in manager_keywords:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            
            # Stop at common stop words that indicate it's not a name
            stop_words = ['for more', 'details', 'about', 'this home', 'features', 'exterior', 'interior', 
                         'property owner', 'rentals', 'advertise', 'get help', 'sign in', 'back to search']
            for stop in stop_words:
                if stop in name.lower():
                    name = name.split(stop)[0].strip()
                    break
            
            # If name is just "manager" or "agent" or similar, it's not valid
            if name.lower().strip() in ['manager', 'agent', 'owner', 'contact', 'features', 'manage', 'listed', 'by']:
                continue  # Skip this match, try next pattern
            
            # First, split on newlines and take only the first line (names are usually on first line)
            if '\n' in name:
                name = name.split('\n')[0].strip()
            
            # Remove phone numbers (e.g., "(404) 334-2532" or "404-334-2532")
            name = re.sub(r'\(?\d{3}\)?\s*-?\s*\d{3}\s*-?\s*\d{4}.*$', '', name, flags=re.MULTILINE)
            # Remove "Verified Source" and similar phrases
            name = re.sub(r'\s*(Verified Source|Source|Verified).*$', '', name, flags=re.IGNORECASE)
            # Clean up common suffixes
            name = re.sub(r'\s+(LLC|Inc|Corp|Management|Properties|Real Estate).*$', '', name, flags=re.IGNORECASE)
            # Remove trailing punctuation, newlines, and anything after
            name = re.sub(r'[,;:\.\n]+.*$', '', name).strip()
            # Remove any remaining newlines or extra whitespace
            name = ' '.join(name.split())
            # Final cleanup: remove any remaining phone number patterns
            name = re.sub(r'\s*\(?\d{3}\)?\s*-?\s*\d{3}\s*-?\s*\d{4}.*$', '', name).strip()
            
            # Must be a valid name AND not be generic
            if (is_valid_name(name) and 
                name.lower() not in ['manager', 'agent', 'owner', 'contact', 'manage', 'listed', 'by', 'features'] and
                not name.lower().startswith(('manage', 'listed', 'property owner'))):
                # Final check: must look like a real name
                words = name.split()
                # Must be at least 2 words (e.g., "John Smith") OR a single word that's 8+ chars (company name)
                # But exclude single short words like "John" alone (unless it's clearly a company name)
                if len(words) >= 2:
                    # Check it's not a sentence fragment
                    if not any(verb in name.lower() for verb in ['is', 'pays', 'responsible', 'for']):
                        # Exclude single common first names that are too short
                        if len(words) == 1 and len(name) < 8:
                            return None  # Single word too short
                        return name
                elif len(words) == 1 and len(name) >= 8:
                    # Single word must be 8+ chars (company name like "Properties")
                    return name
                else:
                    return None  # Doesn't meet criteria
    
    # Method 2: Zillow-specific selectors for agent/manager
    # Look for actual name elements, not generic text
    manager_selectors = [
        '[data-test="agent-name"]',
        '[data-testid="agent-name"]',
        '[class*="agent-name" i]',
        '[class*="AgentName" i]',
        '[class*="listing-agent" i]',
        '[class*="ListingAgent" i]',
        '[class*="contact-name" i]',
        '[class*="ContactName" i]',
        '[class*="ds-agent-name"]',  # Zillow data science class
        '[class*="ds-listing-agent"]',
        'a[href*="/profile/"]',  # Agent profile links often contain names
    ]
    
    for selector in manager_selectors:
        try:
            elems = page.query_selector_all(selector)
            for elem in elems:
                try:
                    if elem.is_visible():
                        text = elem.inner_text().strip()
                        # For links, get the text or the href text
                        if selector.startswith('a['):
                            # Extract name from link text or URL
                            link_text = text
                            href = elem.get_attribute('href') or ''
                            # Sometimes name is in URL: /profile/john-smith/
                            if '/profile/' in href:
                                name_from_url = href.split('/profile/')[-1].split('/')[0]
                                name_from_url = name_from_url.replace('-', ' ').title()
                                if is_valid_name(name_from_url):
                                    return name_from_url
                            
                            if is_valid_name(link_text):
                                return link_text
                        else:
                            if is_valid_name(text):
                                return text
                except Exception:
                    continue
        except Exception:
            continue
    
    # Method 3: Look for "Contact" or "Agent" sections and extract names
    try:
        # More specific selectors for contact sections
        contact_sections = page.query_selector_all(
            '[class*="ds-agent-card"], [class*="agent-card"], [class*="contact-card"], '
            '[data-test*="agent-card"], [data-test*="contact-card"], '
            '[class*="ds-listing-agent"], [class*="listing-agent-info"]'
        )
        for section in contact_sections:
            try:
                if section.is_visible():
                    # Look for name-like text in the section
                    text = section.inner_text()
                    # Try to find a name pattern (First Last or Company Name)
                    # Look for capitalized words that look like names
                    name_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b'  # "John Smith" or "ABC Properties LLC"
                    matches = re.findall(name_pattern, text)
                    for match in matches:
                        # Filter out common non-name patterns
                        if (is_valid_name(match) and 
                            not any(word.lower() in match.lower() for word in ['Features', 'Exterior', 'Interior', 'Details', 'Photos', 'Zillow'])):
                            return match
            except Exception:
                continue
    except Exception:
        pass
    
    # Method 4: If we found "Listed by property owner" or similar, return empty
    # (better to have no name than wrong name)
    if re.search(r'listed by property owner', page_text, re.IGNORECASE):
        return None
    
    return None


def clean_manager_name(name: str) -> str:
    """Clean extracted manager name by removing phone numbers, newlines, and extra text."""
    if not name:
        return ""
    
    # Split on newlines and take first line
    if '\n' in name:
        name = name.split('\n')[0].strip()
    
    # Remove phone numbers in various formats
    name = re.sub(r'\(?\d{3}\)?\s*-?\s*\d{3}\s*-?\s*\d{4}.*$', '', name)
    name = re.sub(r'\s*\(?\d{3}\)?\s*-?\s*\d{3}\s*-?\s*\d{4}.*$', '', name)
    
    # Remove "Verified Source" and similar
    name = re.sub(r'\s*(Verified Source|Source|Verified).*$', '', name, flags=re.IGNORECASE)
    
    # Remove trailing punctuation and extra text
    name = re.sub(r'[,;:\.\n]+.*$', '', name).strip()
    
    # Normalize whitespace
    name = ' '.join(name.split())
    
    return name.strip()


def extract_manager_name(page: Page, soup: BeautifulSoup) -> Optional[str]:
    """Extract manager name using property card → selectors → JSON-LD fallback order. Excludes city/state."""
    # Method 1: Try to get agent/business name from property card (along with phone) - MOST RELIABLE
    agent_name, business_name, _ = extract_agent_business_phone_from_card(page, soup)
    # Prefer agent name, fallback to business name
    manager_name = agent_name or business_name
    if manager_name:
        return manager_name
    
    # Method 2: Selectors (fallback)
    name = extract_manager_name_from_selectors(page, soup)
    if name:
        name = clean_manager_name(name)
        # Final validation: must be at least 2 words or 8+ chars
        words = name.split()
        if len(words) >= 2 or (len(words) == 1 and len(name) >= 8):
            return name if name else None
        return None
    
    # Method 3: JSON-LD (but filter aggressively)
    json_ld_data = parse_json_ld(soup)
    if json_ld_data.get('name'):
        name = json_ld_data['name']
        if name:
            name = clean_manager_name(name)
            name_clean = name.strip()
            
            # Aggressively filter out city/state patterns
            city_state_pattern = r'^[A-Z][a-z]+(?:\s+[A-Z]{2})?$'  # "Atlanta GA" or "Atlanta"
            if re.match(city_state_pattern, name_clean):
                return None  # Skip city/state names
            
            # Check against common city names
            common_cities = ['atlanta', 'decatur', 'sandy springs', 'roswell', 'alpharetta', 'marietta']
            if name_clean.lower() in common_cities:
                return None
            
            # Check if it's just a state abbreviation
            if name_clean.upper() in ['GA', 'AL', 'FL', 'NC', 'SC', 'TN', 'TX', 'CA', 'NY']:
                return None
            
            # Must have at least 2 words or be a company name
            words = name_clean.split()
            if len(words) >= 2 or (len(words) == 1 and len(name_clean) >= 8):
                if len(name_clean) > 2 and len(name_clean) < 80:
                    return name_clean
    
    return None


def scrape_property_url(page: Page, url: str, store: Store) -> Optional[Dict]:
    """
    Scrape a single Zillow property URL and extract data.
    Returns dict with phone, address, manager_name, or None if failed.
    """
    normalized_url = normalize_url(url)
    
    # Skip if already crawled
    if store.is_url_crawled(normalized_url):
        logger.debug(f"Skipping already crawled URL: {normalized_url}")
        return None
    
    try:
        logger.info(f"Navigating to: {url}")
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        time.sleep(random.uniform(2.0, 3.5))
        
        # Check if blocked
        page_title = page.title()
        if 'denied' in page_title.lower() or 'blocked' in page_title.lower():
            logger.warning(f"  ⚠️  Page blocked: {url}")
            store.mark_url_crawled(normalized_url)
            return None
        
        # Wait for page to load
        try:
            page.wait_for_selector('body', timeout=10000)
        except Exception:
            pass
        
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract phone, agent name, and business name from card (most reliable)
        agent_name, business_name, phone = extract_agent_business_phone_from_card(page, soup)
        
        # If no phone from card, try other methods
        if not phone:
            phone = extract_phone(page, soup)
            if not phone:
                logger.warning(f"  ❌ No phone found for {url}")
                store.mark_url_crawled(normalized_url)
                return None
        
        # If no agent/business name from card, try other methods
        if not agent_name and not business_name:
            # Try extract_manager_name as fallback (returns combined name)
            fallback_name = extract_manager_name(page, soup)
            if fallback_name:
                # Try to determine if it's an agent name or business name
                words = fallback_name.split()
                if 2 <= len(words) <= 3 and all(word[0].isupper() for word in words if word):
                    agent_name = fallback_name
                elif any(marker in fallback_name for marker in ['LLC', 'Inc', 'Corp', '&', 'Properties', 'Property', 'Management']):
                    business_name = fallback_name
                else:
                    # Default to agent name if unclear
                    agent_name = fallback_name
        
        # Extract address (best-effort)
        address = extract_address(page, soup)
        
        # Mark URL as crawled
        store.mark_url_crawled(normalized_url)
        
        # Log extraction results
        agent_display = agent_name if agent_name else 'None'
        business_display = business_name if business_name else 'None'
        logger.info(f"  ✅ Extracted: phone={phone}, address={address or 'N/A'}, agent={agent_display}, business={business_display}")
        
        return {
            'phone': phone,
            'address': address or '',
            'agent_name': agent_name or '',  # Empty string if no agent name found
            'business_name': business_name or ''  # Empty string if no business name found
        }
        
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        store.mark_url_crawled(normalized_url)
        return None


def export_to_csv(store: Store, output_path: str):
    """Export aggregated data to CSV."""
    logger.info("Exporting data to CSV...")
    
    phones_data = store.get_all_phones()
    
    if not phones_data:
        logger.warning("No data to export")
        return
    
    records = []
    for data in phones_data:
        addresses_str = '; '.join(sorted(data['addresses'])) if data['addresses'] else ''
        
        records.append({
            'phone': data['phone'],
            'agent_name': data.get('agent_name', '') or '',
            'business_name': data.get('business_name', '') or '',
            'addresses': addresses_str,
            'units': data['units']
        })
    
    df = pd.DataFrame(records)
    df = df.sort_values(by='phone').reset_index(drop=True)
    
    df.to_csv(output_path, index=False)
    logger.info(f"Exported {len(records)} records to {output_path}")
    
    print("\n" + "=" * 80)
    print("SCRAPING SUMMARY")
    print("=" * 80)
    print(f"Unique phones found: {len(records)}")
    
    total_addresses = sum(len(data['addresses']) for data in phones_data)
    print(f"Total addresses aggregated: {total_addresses}")
    
    print("\nPreview (first 5 rows):")
    print("-" * 80)
    print(df.head(5).to_string(index=False))
    print("=" * 80)


def scrape_from_urls(input_csv: str, output_csv: str, delay: float, headless: bool = False):
    """Read URLs from CSV and scrape data from each."""
    # Read URLs from input CSV
    urls = []
    try:
        with open(input_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get('url', '').strip()
                if url and url.startswith('http'):
                    urls.append(url)
    except Exception as e:
        logger.error(f"Error reading input CSV {input_csv}: {e}")
        return
    
    logger.info("=" * 80)
    logger.info("ZILLOW URL SCRAPER")
    logger.info("=" * 80)
    logger.info(f"Input CSV: {input_csv}")
    logger.info(f"Output CSV: {output_csv}")
    logger.info(f"Total URLs to scrape: {len(urls)}")
    logger.info(f"Delay: {delay}s (±0.6s jitter)")
    logger.info(f"Headless: {headless}")
    logger.info("=" * 80)
    
    # Initialize store
    db_path = "data/zillow_data.db"
    store = Store(db_path)
    
    try:
        existing_phones = store.get_unique_phones_count()
        if existing_phones > 0:
            logger.info(f"Resuming: Found {existing_phones} phones in database")
        
        with sync_playwright() as p:
            # Use installed Chrome instead of Chromium for better anti-bot evasion
            browser = p.chromium.launch(
                headless=headless,
                channel="chrome",  # Use installed Chrome browser instead of bundled Chromium
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                ]
            )
            
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                timezone_id='America/New_York',
                permissions=['geolocation'],
                geolocation={'latitude': 33.7490, 'longitude': -84.3880},  # Atlanta coordinates
                color_scheme='light',
            )
            
            # Add comprehensive stealth scripts to avoid detection
            context.add_init_script("""
                // Remove webdriver property
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                
                // Override plugins to look like real Chrome
                Object.defineProperty(navigator, 'plugins', {
                    get: () => {
                        const plugins = [];
                        for (let i = 0; i < 5; i++) {
                            plugins.push({
                                0: {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format'},
                                description: 'Portable Document Format',
                                filename: 'internal-pdf-viewer',
                                length: 1,
                                name: 'Chrome PDF Plugin'
                            });
                        }
                        return plugins;
                    }
                });
                
                // Override languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
                
                // Chrome runtime (make it look like real Chrome)
                window.chrome = {
                    runtime: {},
                    loadTimes: function() {},
                    csi: function() {},
                    app: {}
                };
                
                // Override permissions API
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
                
                // Override getBattery to return realistic values
                if (navigator.getBattery) {
                    navigator.getBattery = () => Promise.resolve({
                        charging: true,
                        chargingTime: 0,
                        dischargingTime: Infinity,
                        level: 1
                    });
                }
                
                // Override platform
                Object.defineProperty(navigator, 'platform', {
                    get: () => 'Win32'
                });
                
                // Add missing properties that real Chrome has
                Object.defineProperty(navigator, 'hardwareConcurrency', {
                    get: () => 8
                });
                
                Object.defineProperty(navigator, 'deviceMemory', {
                    get: () => 8
                });
            """)
            
            page = context.new_page()
            
            try:
                for i, url in enumerate(urls, 1):
                    logger.info(f"\n{'='*80}")
                    logger.info(f"Scraping {i}/{len(urls)}: {url}")
                    logger.info(f"{'='*80}")
                    
                    data = scrape_property_url(page, url, store)
                    
                    if data:
                        phone = data['phone']
                        address = data['address']
                        agent_name = data.get('agent_name', '')
                        business_name = data.get('business_name', '')
                        
                        store.upsert_phone(phone, agent_name, business_name)
                        
                        if address:
                            store.add_address(phone, address)
                        
                        logger.info(f"Progress: {store.get_unique_phones_count()} unique phones")
                        
                        # Export to CSV incrementally
                        try:
                            export_to_csv(store, output_csv)
                        except Exception as e:
                            logger.debug(f"Could not export CSV incrementally: {e}")
                    
                    # Rate limiting
                    if i < len(urls):
                        jitter = random.uniform(-0.6, 0.6)
                        wait_time = max(0.1, delay + jitter)
                        time.sleep(wait_time)
                
            finally:
                browser.close()
        
        # Final export
        export_to_csv(store, output_csv)
        logger.info("Scraping completed successfully")
        
    except KeyboardInterrupt:
        logger.info("\nScraping interrupted by user")
        logger.info("Progress saved to database. Re-run to resume.")
        export_to_csv(store, output_csv)
    except Exception as e:
        logger.error(f"Scraping failed: {e}", exc_info=True)
        export_to_csv(store, output_csv)
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description='Scrape data from Zillow property URLs')
    parser.add_argument('--input', type=str, default='data/zillow_urls.csv', help='Input CSV with URLs (default: data/zillow_urls.csv)')
    parser.add_argument('--output', type=str, default='data/zillow_sfr.csv', help='Output CSV file (default: data/zillow_sfr.csv)')
    parser.add_argument('--delay', type=float, default=3.0, help='Delay between requests in seconds (default: 3.0)')
    parser.add_argument('--headless', action='store_true', help='Run browser in headless mode')
    
    args = parser.parse_args()
    
    if not Path(args.input).exists():
        logger.error(f"Input CSV file not found: {args.input}")
        sys.exit(1)
    
    scrape_from_urls(
        input_csv=args.input,
        output_csv=args.output,
        delay=args.delay,
        headless=args.headless
    )


if __name__ == "__main__":
    main()

