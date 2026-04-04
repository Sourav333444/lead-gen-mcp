"""
MCP Server — Lead Gen Toolkit
Exposes 4 tools to Claude:
  1. scrape_leads              — Google Maps → Google Sheet via Apify
  2. read_sheet                — Read rows from a Google Sheet
  3. create_instantly_campaign — Create a new Instantly campaign
  4. push_to_instantly         — Upload leads to an Instantly campaign
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ── Config ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY", "")
INSTANTLY_API_BASE = "https://api.instantly.ai/api/v2"

# Same scopes the gmaps pipeline script uses
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Google Sheets auth ──────────────────────────────────────────────────────
def _get_google_creds():
    """Return valid Google credentials, refreshing if needed. Never opens a browser."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = BASE_DIR / "token.json"
    creds_path = BASE_DIR / "credentials.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                "Google token is missing or expired and cannot be refreshed automatically. "
                "Run this in a terminal to re-authenticate:\n\n"
                "cd c:\\Users\\soura\\.vscode\\bizness && "
                ".venv\\Scripts\\python -c \""
                "from google_auth_oauthlib.flow import InstalledAppFlow; "
                "flow = InstalledAppFlow.from_client_secrets_file('credentials.json', "
                "['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive']); "
                "creds = flow.run_local_server(port=0); "
                "open('token.json','w').write(creds.to_json())\""
            )

    return creds


def _get_gspread_client():
    """Return an authenticated gspread client using local OAuth token."""
    import gspread
    return gspread.authorize(_get_google_creds())


# ── Tool implementations ────────────────────────────────────────────────────

def scrape_leads(search_query: str, limit: int = 30, sheet_name: str = None) -> dict:
    """
    Run the Google Maps lead pipeline in-process (no subprocess).
    Imports GoogleMapsLeadGenerator directly to share auth context.
    """
    import sys
    sys.path.insert(0, str(BASE_DIR))
    os.chdir(str(BASE_DIR))  # ensure relative paths (token.json etc) resolve correctly

    from execution.gmaps_lead_pipeline import GoogleMapsLeadGenerator
    from datetime import datetime

    if not sheet_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sheet_name = f"Leads_{timestamp}"

    generator = GoogleMapsLeadGenerator(
        search_query=search_query,
        limit=limit,
    )

    businesses = generator.scrape_google_maps()
    if not businesses:
        return {"status": "error", "error": "No businesses found. Try a different search query."}

    qualified = generator.filter_qualified_leads(businesses)
    if not qualified:
        return {"status": "error", "error": "No qualified leads found after filtering."}

    sheet_url = generator.create_google_sheet(qualified, sheet_name=sheet_name)

    return {
        "status": "success",
        "sheet_url": sheet_url,
        "lead_count": len(qualified),
        "scraped_total": len(businesses),
    }


def read_sheet(sheet_url: str, tab: str = None, limit: int = 200) -> dict:
    """
    Read rows from a Google Sheet.
    Returns headers + rows as list of dicts.
    """
    gc = _get_gspread_client()

    spreadsheet = gc.open_by_url(sheet_url)
    worksheet = spreadsheet.worksheet(tab) if tab else spreadsheet.sheet1

    all_values = worksheet.get_all_values()
    if not all_values:
        return {"status": "success", "rows": [], "total": 0}

    headers = all_values[0]
    rows = []
    for row in all_values[1 : limit + 1]:
        # Pad short rows
        while len(row) < len(headers):
            row.append("")
        rows.append(dict(zip(headers, row)))

    return {
        "status": "success",
        "sheet_title": spreadsheet.title,
        "tab": worksheet.title,
        "headers": headers,
        "rows": rows,
        "total": len(all_values) - 1,  # excludes header
        "returned": len(rows),
    }


def push_to_instantly(
    campaign_id: str,
    leads: list[dict],
) -> dict:
    """
    Upload leads to an existing Instantly campaign.

    Each lead dict should have at minimum:
      - email  (required)
      - first_name, last_name, company_name  (optional but recommended)
      - personalization  (optional — used as email icebreaker variable)

    Returns counts of successful and failed uploads.
    """
    if not INSTANTLY_API_KEY:
        return {"status": "error", "error": "INSTANTLY_API_KEY not set in .env"}

    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json",
    }

    url = f"{INSTANTLY_API_BASE}/leads"
    succeeded = []
    failed = []

    for i, lead in enumerate(leads):
        email = lead.get("email") or lead.get("Email") or lead.get("emails", "").split(",")[0].strip()
        if not email:
            failed.append({"index": i, "reason": "no email"})
            continue

        payload = {
            "campaign_id": campaign_id,
            "email": email,
            "first_name": lead.get("first_name") or lead.get("First Name") or lead.get("owner_name", "").split()[0] if lead.get("owner_name") else "",
            "last_name": lead.get("last_name") or lead.get("Last Name") or " ".join(lead.get("owner_name", "").split()[1:]),
            "company_name": lead.get("company_name") or lead.get("Company Name") or lead.get("business_name") or lead.get("Company Name", ""),
            "personalization": lead.get("personalization") or lead.get("icebreaker") or "",
            "phone": lead.get("phone") or lead.get("Phone Number") or "",
            "website": lead.get("website") or lead.get("Website") or "",
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)

            if resp.status_code == 429:
                time.sleep(5)
                resp = requests.post(url, headers=headers, json=payload, timeout=30)

            if resp.status_code in (200, 201):
                succeeded.append(email)
            else:
                failed.append({"email": email, "status": resp.status_code, "detail": resp.text[:200]})

        except Exception as e:
            failed.append({"email": email, "error": str(e)})

        # Light rate limiting
        if (i + 1) % 10 == 0:
            time.sleep(1)

    return {
        "status": "success" if not failed else "partial" if succeeded else "error",
        "campaign_id": campaign_id,
        "uploaded": len(succeeded),
        "failed": len(failed),
        "failed_details": failed[:20],  # cap at 20 for readability
    }


def create_instantly_campaign(
    campaign_name: str,
    sequence_steps: list[dict] = None,
) -> dict:
    """
    Create a new campaign in Instantly and return its ID.
    sequence_steps is optional — pass a list of {subject, body, delay_days} dicts.
    If omitted, creates campaign with a placeholder first email.
    """
    if not INSTANTLY_API_KEY:
        return {"status": "error", "error": "INSTANTLY_API_KEY not set in .env"}

    from datetime import datetime, timedelta

    headers = {
        "Authorization": f"Bearer {INSTANTLY_API_KEY}",
        "Content-Type": "application/json",
    }

    # Build sequence
    if sequence_steps:
        steps = [
            {
                "type": "email",
                "delay": step.get("delay_days", 0),
                "variants": [
                    {
                        "subject": step.get("subject", "Following up"),
                        "body": step.get("body", ""),
                    }
                ],
            }
            for step in sequence_steps
        ]
    else:
        steps = [
            {
                "type": "email",
                "delay": 0,
                "variants": [
                    {
                        "subject": "Quick question",
                        "body": "<p>Hi {{firstName}},</p><p>{{icebreaker}}</p><p>Would love to connect.</p><p>{{sendingAccountFirstName}}</p>",
                    }
                ],
            }
        ]

    start_date = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")

    payload = {
        "name": campaign_name,
        "sequences": [{"steps": steps}],
        "campaign_schedule": {
            "start_date": start_date,
            "end_date": end_date,
            "schedules": [
                {
                    "name": "Weekday Schedule",
                    "days": {"1": True, "2": True, "3": True, "4": True, "5": True},
                    "timing": {"from": "09:00", "to": "17:00"},
                    "timezone": "America/Chicago",
                }
            ],
        },
        "email_gap": 10,
        "daily_limit": 50,
        "stop_on_reply": True,
        "stop_on_auto_reply": True,
        "link_tracking": True,
        "open_tracking": True,
        "text_only": False,
    }

    try:
        resp = requests.post(
            f"{INSTANTLY_API_BASE}/campaigns",
            headers=headers,
            json=payload,
            timeout=60,
        )

        if resp.status_code == 429:
            time.sleep(30)
            resp = requests.post(
                f"{INSTANTLY_API_BASE}/campaigns",
                headers=headers,
                json=payload,
                timeout=60,
            )

        if resp.status_code not in (200, 201):
            return {
                "status": "error",
                "http_status": resp.status_code,
                "detail": resp.text[:500],
            }

        data = resp.json()
        return {
            "status": "success",
            "campaign_id": data.get("id"),
            "campaign_name": campaign_name,
        }

    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── MCP Server definition ───────────────────────────────────────────────────

app = Server("lead-gen-toolkit")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="scrape_leads",
            description=(
                "Scrape Google Maps for qualified B2B leads and save them to a new Google Sheet. "
                "Uses Apify to find businesses, filters by rating/reviews/phone/website, and "
                "enriches each lead with contact info extracted from their website. "
                "Returns the Google Sheet URL and lead count. "
                "Cost: ~$0.02/lead. Takes 2–5 minutes for 30 leads."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search_query": {
                        "type": "string",
                        "description": "Google Maps search query, e.g. 'HVAC companies in Austin TX' or 'dentists in Miami FL'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of qualified leads to collect (default: 30, max: 200)",
                        "default": 30,
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "Optional name for the Google Sheet. Auto-generated if not provided.",
                    },
                },
                "required": ["search_query"],
            },
        ),
        Tool(
            name="read_sheet",
            description=(
                "Read rows from a Google Sheet. Returns headers and rows as structured data. "
                "Use this to load leads from an existing sheet before uploading to Instantly."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sheet_url": {
                        "type": "string",
                        "description": "Full Google Sheets URL (https://docs.google.com/spreadsheets/d/...)",
                    },
                    "tab": {
                        "type": "string",
                        "description": "Worksheet tab name (defaults to first sheet if not provided)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows to return (default: 200)",
                        "default": 200,
                    },
                },
                "required": ["sheet_url"],
            },
        ),
        Tool(
            name="push_to_instantly",
            description=(
                "Upload a list of leads to an Instantly email campaign. "
                "Each lead needs at minimum an email address. "
                "first_name, last_name, company_name, and personalization fields improve deliverability and reply rates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_id": {
                        "type": "string",
                        "description": "Instantly campaign ID to add leads to. Get this from the Instantly dashboard URL or use create_instantly_campaign first.",
                    },
                    "leads": {
                        "type": "array",
                        "description": "Array of lead objects. Each should have: email (required), first_name, last_name, company_name, personalization, phone, website.",
                        "items": {"type": "object"},
                    },
                },
                "required": ["campaign_id", "leads"],
            },
        ),
        Tool(
            name="create_instantly_campaign",
            description=(
                "Create a new email campaign in Instantly. Returns the campaign ID needed for push_to_instantly. "
                "You can optionally pass email sequence steps (subject, body, delay_days). "
                "If omitted, creates a campaign with a placeholder first email you can edit in the Instantly dashboard."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "campaign_name": {
                        "type": "string",
                        "description": "Name for the campaign, e.g. 'HVAC Austin | Free Audit Offer'",
                    },
                    "sequence_steps": {
                        "type": "array",
                        "description": "Optional list of email steps. Each step: {subject, body (HTML), delay_days}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "subject": {"type": "string"},
                                "body": {"type": "string"},
                                "delay_days": {"type": "integer"},
                            },
                        },
                    },
                },
                "required": ["campaign_name"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "scrape_leads":
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: scrape_leads(
                    search_query=arguments["search_query"],
                    limit=arguments.get("limit", 30),
                    sheet_name=arguments.get("sheet_name"),
                ),
            )

        elif name == "read_sheet":
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: read_sheet(
                    sheet_url=arguments["sheet_url"],
                    tab=arguments.get("tab"),
                    limit=arguments.get("limit", 200),
                ),
            )

        elif name == "push_to_instantly":
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: push_to_instantly(
                    campaign_id=arguments["campaign_id"],
                    leads=arguments["leads"],
                ),
            )

        elif name == "create_instantly_campaign":
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: create_instantly_campaign(
                    campaign_name=arguments["campaign_name"],
                    sequence_steps=arguments.get("sequence_steps"),
                ),
            )

        else:
            result = {"status": "error", "error": f"Unknown tool: {name}"}

    except Exception as e:
        import traceback
        result = {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
        }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# ── Entry point ─────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
