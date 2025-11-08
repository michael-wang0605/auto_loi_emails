#!/usr/bin/env python3
"""
Test script to collect 5 Zillow property URLs and verify they're valid.
This helps debug the URL collection and navigation process.
"""
import logging
from playwright.sync_api import sync_playwright

from src.zillow_scraper import get_listing_urls_from_search_page, BASE_URL, USER_AGENT

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_collect_urls():
    """Test collecting 5 URLs from Zillow search page."""
    city = "Atlanta"
    state = "GA"
    city_normalized = city.lower().replace(' ', '-').replace(',', '').replace("'", "")
    state_normalized = state.lower()
    
    search_url = f"{BASE_URL}/{city_normalized}-{state_normalized}/rentals/"
    
    logger.info("=" * 80)
    logger.info("ZILLOW URL COLLECTION TEST")
    logger.info("=" * 80)
    logger.info(f"Search URL: {search_url}")
    logger.info("Collecting 5 property URLs...")
    logger.info("=" * 80)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Non-headless so we can see what's happening
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080}
        )
        
        page = context.new_page()
        
        try:
            # Collect URLs
            listing_urls = get_listing_urls_from_search_page(page, search_url)
            
            logger.info(f"\n{'='*80}")
            logger.info(f"COLLECTED {len(listing_urls)} URLs")
            logger.info(f"{'='*80}")
            
            # Take first 5
            test_urls = listing_urls[:5]
            
            if not test_urls:
                logger.error("❌ No URLs collected! Check the selectors.")
                return
            
            logger.info(f"\nFirst 5 URLs collected:")
            for i, url in enumerate(test_urls, 1):
                logger.info(f"  {i}. {url}")
            
            # Test navigating to each URL
            logger.info(f"\n{'='*80}")
            logger.info("TESTING NAVIGATION TO EACH URL")
            logger.info(f"{'='*80}")
            
            for i, url in enumerate(test_urls, 1):
                logger.info(f"\n--- Testing URL {i}/5: {url} ---")
                
                try:
                    # Navigate to the URL
                    logger.info(f"  Navigating to: {url}")
                    page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    page.wait_for_timeout(2000)
                    
                    # Check where we ended up
                    current_url = page.url
                    page_title = page.title()
                    
                    logger.info(f"  ✅ Navigation successful")
                    logger.info(f"  Current URL: {current_url}")
                    logger.info(f"  Page title: {page_title}")
                    
                    # Check if we got blocked
                    if 'denied' in page_title.lower() or 'blocked' in page_title.lower():
                        logger.warning(f"  ⚠️  Page appears to be BLOCKED")
                    elif url in current_url or url.replace('https://www.', '') in current_url:
                        logger.info(f"  ✅ URL matches - navigation worked!")
                    else:
                        logger.warning(f"  ⚠️  URL mismatch - might have been redirected")
                        logger.warning(f"     Expected: {url}")
                        logger.warning(f"     Got: {current_url}")
                    
                    # Check if page has content
                    body_text = page.inner_text('body')[:200] if page.inner_text('body') else ''
                    logger.info(f"  Page content preview: {body_text[:100]}...")
                    
                except Exception as e:
                    logger.error(f"  ❌ Navigation failed: {e}")
                
                # Wait between tests
                if i < len(test_urls):
                    import time
                    time.sleep(2)
            
            logger.info(f"\n{'='*80}")
            logger.info("TEST COMPLETE")
            logger.info(f"{'='*80}")
            
        finally:
            browser.close()


if __name__ == "__main__":
    test_collect_urls()

