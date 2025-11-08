"""
Configuration file for property manager scraper
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Location settings - modify these for your area
LOCATION = os.getenv('LOCATION', 'San Francisco, CA')  # e.g., "San Francisco, CA", "New York, NY"
ZIP_CODE = os.getenv('ZIP_CODE', '')  # Optional: specific zip code
SEARCH_RADIUS = os.getenv('SEARCH_RADIUS', '10')  # Search radius in miles

# Scraping settings
MAX_LISTINGS = int(os.getenv('MAX_LISTINGS', '50'))  # Maximum number of listings to scrape
DELAY_BETWEEN_REQUESTS = float(os.getenv('DELAY_BETWEEN_REQUESTS', '2.0'))  # Seconds between requests
PAGE_LOAD_TIMEOUT = int(os.getenv('PAGE_LOAD_TIMEOUT', '30'))  # Seconds to wait for page load

# Output settings
OUTPUT_FILE = os.getenv('OUTPUT_FILE', 'property_managers.csv')

# Browser settings (for Selenium)
HEADLESS = os.getenv('HEADLESS', 'True').lower() == 'true'  # Run browser in headless mode
USER_AGENT = os.getenv('USER_AGENT', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

