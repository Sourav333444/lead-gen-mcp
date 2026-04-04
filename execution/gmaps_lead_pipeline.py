"""
Google Maps Lead Generation Pipeline
Scrapes Google Maps for businesses and creates a Google Sheet with results.
"""

import os
import sys
import json
import time
import hashlib
from typing import List, Dict, Optional
from datetime import datetime
from dotenv import load_dotenv

# Third-party imports
try:
    from apify_client import ApifyClient
    import gspread
    from google.oauth2.service_account import Credentials
    from google.oauth2.credentials import Credentials as OAuthCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
except ImportError as e:
    print(f"Missing required package: {e}")
    print("\nInstall with: pip install apify-client gspread google-auth google-auth-oauthlib")
    sys.exit(1)

# Load environment variables
load_dotenv()

# Configuration
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

class GoogleMapsLeadGenerator:
    """Scrapes Google Maps and creates/updates Google Sheets with qualified leads."""

    def __init__(self, search_query: str, limit: int = 30):
        self.search_query = search_query
        self.limit = limit

        if not APIFY_API_TOKEN:
            raise ValueError("APIFY_API_TOKEN not found in .env file")

        self.apify_client = ApifyClient(APIFY_API_TOKEN)
        self.gc = self._authenticate_google()

    def _authenticate_google(self):
        """Authenticate with Google Sheets API using OAuth."""
        creds = None
        token_path = "token.json"
        creds_path = "credentials.json"

        # Load existing token
        if os.path.exists(token_path):
            creds = OAuthCredentials.from_authorized_user_file(token_path, SCOPES)

        # If no valid credentials, get new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(creds_path):
                    raise FileNotFoundError(
                        f"{creds_path} not found. Download OAuth credentials from Google Cloud Console."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                creds = flow.run_local_server(port=0)

            # Save credentials for next run
            with open(token_path, 'w') as token:
                token.write(creds.to_json())

        return gspread.authorize(creds)

    def scrape_google_maps(self) -> List[Dict]:
        """Scrape Google Maps using Apify."""
        print(f"\nScraping Google Maps for: '{self.search_query}'")
        print(f"Target: {self.limit} results")

        # Apify Google Maps Scraper configuration
        run_input = {
            "searchStringsArray": [self.search_query],
            "maxCrawledPlacesPerSearch": self.limit * 3,  # Get extra to account for filtering
            "language": "en",
            "skipClosedPlaces": True,
            "includeWebsites": True,
            "includeReviews": False,  # Don't need review details
        }

        print("\nStarting Apify scraper...")
        run = self.apify_client.actor("compass/crawler-google-places").call(run_input=run_input)

        # Get results
        results = []
        for item in self.apify_client.dataset(run["defaultDatasetId"]).iterate_items():
            results.append(item)

        print(f"Scraped {len(results)} businesses from Google Maps")
        return results

    def filter_qualified_leads(self, businesses: List[Dict]) -> List[Dict]:
        """Filter businesses based on qualification criteria."""
        print("\nFiltering for qualified leads...")
        print("Criteria:")
        print("  [+] 3.5+ star rating (but not perfect)")
        print("  [+] 20+ reviews")
        print("  [+] Phone number listed")
        print("  [+] Website exists")
        print("  [-] Exclude: 1-2 star ratings")

        qualified = []

        for business in businesses:
            # Extract fields with safe defaults
            rating = business.get("totalScore") or business.get("rating") or 0
            review_count = business.get("reviewsCount") or 0
            phone = business.get("phone") or ""
            website = business.get("website") or ""

            # Apply filters
            if rating < 3.5:
                continue
            if rating == 5.0 and review_count < 50:  # Avoid suspiciously perfect scores
                continue
            if review_count < 20:
                continue
            if not phone:
                continue
            if not website:
                continue

            qualified.append(business)

            if len(qualified) >= self.limit:
                break

        print(f"\n[OK] Found {len(qualified)} qualified leads (filtered from {len(businesses)})")
        return qualified

    def create_google_sheet(self, leads: List[Dict], sheet_name: Optional[str] = None) -> str:
        """Create a new Google Sheet with the leads."""
        if not sheet_name:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            sheet_name = f"HVAC_Leads_{timestamp}"

        print(f"\nCreating Google Sheet: '{sheet_name}'")

        # Create new spreadsheet
        spreadsheet = self.gc.create(sheet_name)
        sheet = spreadsheet.sheet1

        # Make it accessible (anyone with link can view)
        spreadsheet.share(None, perm_type='anyone', role='reader')

        # Set up headers
        headers = [
            "Company Name",
            "Phone Number",
            "Google Maps Link",
            "Website",
            "Rating",
            "Review Count",
            "Address",
            "Category"
        ]

        sheet.update('A1:H1', [headers])

        # Format header row
        sheet.format('A1:H1', {
            "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "horizontalAlignment": "CENTER"
        })

        # Prepare rows
        rows = []
        for lead in leads:
            row = [
                lead.get("title") or lead.get("name") or "",
                lead.get("phone") or "",
                lead.get("url") or "",
                lead.get("website") or "",
                lead.get("totalScore") or lead.get("rating") or "",
                lead.get("reviewsCount") or "",
                lead.get("address") or "",
                lead.get("categoryName") or lead.get("category") or ""
            ]
            rows.append(row)

        # Write data
        if rows:
            sheet.update(f'A2:H{len(rows)+1}', rows)
            print(f"[OK] Wrote {len(rows)} leads to sheet")

        # Auto-resize columns
        sheet.columns_auto_resize(0, 7)

        spreadsheet_url = spreadsheet.url
        print(f"\n[OK] Google Sheet created successfully!")
        print(f"URL: {spreadsheet_url}")

        return spreadsheet_url

    def run(self) -> str:
        """Execute the full pipeline."""
        print("="*60)
        print("Google Maps Lead Generation Pipeline")
        print("="*60)

        # Step 1: Scrape Google Maps
        businesses = self.scrape_google_maps()

        if not businesses:
            print("\n[X] No businesses found. Try a different search query.")
            return None

        # Step 2: Filter for qualified leads
        qualified_leads = self.filter_qualified_leads(businesses)

        if not qualified_leads:
            print("\n[X] No qualified leads found. Try adjusting your criteria.")
            return None

        # Step 3: Create Google Sheet
        sheet_url = self.create_google_sheet(qualified_leads)

        print("\n" + "="*60)
        print("Pipeline Complete!")
        print("="*60)
        print(f"Qualified leads: {len(qualified_leads)}")
        print(f"Google Sheet: {sheet_url}")
        print("="*60)

        return sheet_url


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Google Maps Lead Generation")
    parser.add_argument("--search", required=True, help="Search query (e.g., 'HVAC companies in Austin TX')")
    parser.add_argument("--limit", type=int, default=30, help="Number of qualified leads to find")
    parser.add_argument("--sheet-name", help="Custom name for Google Sheet")

    args = parser.parse_args()

    try:
        generator = GoogleMapsLeadGenerator(
            search_query=args.search,
            limit=args.limit
        )
        generator.run()
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
