"""
SQLite database helpers for persistent storage and checkpointing.
"""
import sqlite3
import logging
from typing import Optional, List, Set
from urllib.parse import urlparse, parse_qs, urlunparse

logger = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    """
    Normalize URL by removing query parameters and fragments.
    This helps deduplicate URLs that are functionally the same.
    """
    parsed = urlparse(url)
    # Remove query and fragment
    normalized = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        '',  # params
        '',  # query
        ''   # fragment
    ))
    return normalized.rstrip('/')


class Store:
    """SQLite database store for phones, addresses, and crawled URLs."""
    
    def __init__(self, db_path: str = "data.db"):
        """Initialize database connection and create tables if needed."""
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
    
    def _init_tables(self):
        """Create tables if they don't exist."""
        cursor = self.conn.cursor()
        
        # phones table: phone (unique key), manager_name
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS phones (
                phone TEXT PRIMARY KEY,
                manager_name TEXT
            )
        """)
        
        # addresses table: phone, address (unique per phone)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS addresses (
                phone TEXT,
                address TEXT,
                UNIQUE(phone, address),
                FOREIGN KEY(phone) REFERENCES phones(phone)
            )
        """)
        
        # crawled_urls table: track which URLs we've already processed
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crawled_urls (
                url TEXT PRIMARY KEY
            )
        """)
        
        # Create indexes for faster lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_addresses_phone 
            ON addresses(phone)
        """)
        
        self.conn.commit()
        logger.info(f"Database initialized at {self.db_path}")
    
    def is_url_crawled(self, url: str) -> bool:
        """Check if a URL has already been crawled."""
        normalized = normalize_url(url)
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM crawled_urls WHERE url = ?", (normalized,))
        return cursor.fetchone() is not None
    
    def mark_url_crawled(self, url: str):
        """Mark a URL as crawled."""
        normalized = normalize_url(url)
        cursor = self.conn.cursor()
        try:
            cursor.execute("INSERT OR IGNORE INTO crawled_urls (url) VALUES (?)", (normalized,))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass  # Already exists, ignore
    
    def upsert_phone(self, phone: str, manager_name: Optional[str] = None):
        """
        Insert or update phone record.
        Only updates manager_name if it's empty and we have a new value.
        """
        cursor = self.conn.cursor()
        # Check if phone exists
        cursor.execute("SELECT manager_name FROM phones WHERE phone = ?", (phone,))
        existing = cursor.fetchone()
        
        if existing:
            # Update manager_name only if it's empty and we have a new value
            if not existing['manager_name'] and manager_name:
                cursor.execute(
                    "UPDATE phones SET manager_name = ? WHERE phone = ?",
                    (manager_name, phone)
                )
                self.conn.commit()
                logger.debug(f"Updated manager_name for phone {phone}")
        else:
            # Insert new phone
            cursor.execute(
                "INSERT INTO phones (phone, manager_name) VALUES (?, ?)",
                (phone, manager_name or '')
            )
            self.conn.commit()
            logger.debug(f"Inserted new phone {phone}")
    
    def add_address(self, phone: str, address: str) -> bool:
        """
        Add address for a phone if it doesn't already exist.
        Returns True if address was added, False if it already existed.
        """
        if not address or not address.strip():
            return False
        
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO addresses (phone, address) VALUES (?, ?)",
                (phone, address.strip())
            )
            self.conn.commit()
            logger.debug(f"Added address {address} for phone {phone}")
            return True
        except sqlite3.IntegrityError:
            # Address already exists for this phone
            return False
    
    def get_units_count(self, phone: str) -> int:
        """Get the count of unique addresses (units) for a phone."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as count FROM addresses WHERE phone = ?",
            (phone,)
        )
        result = cursor.fetchone()
        return result['count'] if result else 0
    
    def get_unique_phones_count(self) -> int:
        """Get total number of unique phones stored."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM phones")
        result = cursor.fetchone()
        return result['count'] if result else 0
    
    def get_all_phones(self) -> List[dict]:
        """
        Get all phones with their addresses and units count.
        Returns list of dicts with keys: phone, manager_name, addresses (list), units (int)
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT phone, manager_name FROM phones ORDER BY phone")
        phones = cursor.fetchall()
        
        results = []
        for phone_row in phones:
            phone = phone_row['phone']
            manager_name = phone_row['manager_name'] or ''
            
            # Get all addresses for this phone
            cursor.execute(
                "SELECT address FROM addresses WHERE phone = ? ORDER BY address",
                (phone,)
            )
            address_rows = cursor.fetchall()
            addresses = [row['address'] for row in address_rows]
            units = len(addresses)
            
            results.append({
                'phone': phone,
                'manager_name': manager_name,
                'addresses': addresses,
                'units': units
            })
        
        return results
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")


