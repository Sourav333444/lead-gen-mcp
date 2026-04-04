# Lead Gen MCP Server

An MCP server that gives Claude a complete B2B lead generation toolkit — scrape Google Maps, read Google Sheets, and push leads into Instantly email campaigns, all from a single conversation.

## What it does

| Tool | Description |
|---|---|
| `scrape_leads` | Scrape Google Maps for qualified businesses → saves to Google Sheet |
| `read_sheet` | Read rows from any Google Sheet as structured data |
| `create_instantly_campaign` | Create a new Instantly email campaign |
| `push_to_instantly` | Upload leads into an Instantly campaign |

**Example workflow in Claude:**
> "Scrape 50 HVAC companies in Austin, create an Instantly campaign called 'HVAC Austin — Free Audit', and push the leads."

Claude does all four steps in one shot.

## Prerequisites

- Python 3.11+
- [Apify](https://apify.com) account + API token (for Google Maps scraping)
- [Instantly](https://instantly.ai) account + API v2 key (for email campaigns)
- Google Cloud project with OAuth credentials (for Google Sheets)

## Install

```bash
git clone https://github.com/YOUR_USERNAME/lead-gen-mcp
cd lead-gen-mcp

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Mac/Linux

pip install "mcp[cli]" apify-client gspread google-auth google-auth-oauthlib requests python-dotenv
```

## Configure

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```env
APIFY_API_TOKEN=apify_api_...
INSTANTLY_API_KEY=your_instantly_v2_key
```

### Google Sheets auth (one-time)

Download OAuth credentials from [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials → Create OAuth 2.0 Client ID (Desktop app). Save as `credentials.json` in the project root.

Then run:

```bash
python -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file('credentials.json', [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
])
creds = flow.run_local_server(port=0)
open('token.json', 'w').write(creds.to_json())
"
```

This saves `token.json` — you won't need to do this again unless the token expires.

## Connect to Claude Code

Add to your `.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "lead-gen-toolkit": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

On Windows:
```json
{
  "mcpServers": {
    "lead-gen-toolkit": {
      "command": "C:\\path\\to\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\server.py"]
    }
  }
}
```

Restart Claude Code. Run `/mcp` to confirm `lead-gen-toolkit` is connected.

## Usage

Once connected, just talk to Claude:

```
Scrape 30 roofing contractors in Denver CO and save to a sheet called "Denver Roofers"
```

```
Read the leads from [sheet URL] and push them to Instantly campaign [campaign_id]
```

```
Create an Instantly campaign called "Plumbers NYC | Free Estimate" and give me the campaign ID
```

## Cost

| Component | Cost |
|---|---|
| Apify Google Maps | ~$0.01–0.02 per lead |
| Instantly | your plan's sending limits |
| Google Sheets | free |

100 leads ≈ $1.50–2.50 total.

## File structure

```
server.py          # MCP server (the thing you're installing)
execution/
  gmaps_lead_pipeline.py   # Google Maps scraper + Sheet writer
.env               # Your API keys (never commit this)
credentials.json   # Google OAuth credentials (never commit this)
token.json         # Google OAuth token (never commit this)
```

## Troubleshooting

**"Google token is missing or expired"** — re-run the auth command above.

**"INSTANTLY_API_KEY not set"** — add your key to `.env`. Get it from Instantly → Settings → Integrations → API Keys → select `all:all` scope.

**Apify scrape returns 0 results** — include the city in your query: `"HVAC in Austin TX"` not just `"HVAC"`.
