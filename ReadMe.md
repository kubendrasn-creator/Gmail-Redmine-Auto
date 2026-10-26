# Gmail-Redmine Integration

Automated Gmail to Redmine ticket creation with AI-powered email classification.

## Features

- **AI-Powered Classification**: Uses Google Gemini 3.1 Flash-Lite for intelligent email classification
- **Smart Attachment Handling**: Compresses oversized videos, skips files that exceed limits
- **Link Detection**: Extracts external service links (Google Drive, Dropbox, YouTube, etc.)
- **Preflight Validation**: Validates configuration and dependencies before processing
- **Dry-Run Mode**: Preview what would happen without making changes
- **User Confirmation**: All ticket creations require manual approval (production safety)
- **Duplicate Detection**: Content fingerprinting prevents duplicate tickets
- **Thread/Reply Handling**: Detects conversations and attaches replies to existing tickets
- **Idempotency Support**: Message ID markers prevent duplicate tickets on retry

## Setup

### 1. Gmail API Setup

See [Gmail Setup Requirements](#gmail-setup-requirements) section below.

### 2. Redmine Configuration

Configure your Redmine instance in `env/.env`:

```env
# Redmine Configuration
REDMINE_URL=https://your-redmine-instance.com
REDMINE_API_KEY=your_api_key
REDMINE_PROJECT_ID=1
REDMINE_SUPPORT_TRACKER_ID=1
REDMINE_ASSIGNEE_ID=1

# Built-in Issue Category (Redmine's native category field)
# Set this to the numeric category ID from your Redmine project.
REDMINE_ISSUES_CATEGORY=1

# Custom Fields (optional)
REDMINE_CATEGORY_CUSTOM_FIELD_ID=1
REDMINE_CATEGORY_VALUE=Support
REDMINE_SEVERITY_CUSTOM_FIELD_ID=2
REDMINE_SEVERITY_VALUE=Normal

REDMINE_ESTIMATED_HOURS=0
REDMINE_START_DATE=
REDMINE_END_DATE=

# Gmail Configuration
GMAIL_MAX_RESULTS=10
GMAIL_LOOKBACK_HOURS=24
GMAIL_ALLOWED_SENDERS=user1@example.com,user2@example.com

# AI Classification Configuration (Optional but Recommended)
GEMINI_API_KEY=your_gemini_api_key
AI_MAX_INPUT_CHARS=12000
# Optional comma-separated module names from your Redmine project. Leave blank to allow any.
ALLOWED_MODULES=

# Attachment Configuration
REDMINE_MAX_ATTACHMENT_MB=10
VIDEO_COMPRESSION_ENABLED=true

# Retry Configuration
RETRY_MAX_ATTEMPTS=4
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run

```bash
python -m backend.main
```

This starts the local review UI and opens it in your browser. Use **Fetch emails** to
load the review queue; emails are not fetched automatically unless `--auto-run` is
passed.

Choose the default interface in `env/.env`:

```env
APP_MODE=ui
```

Set `APP_MODE=terminal` to use the original terminal prompts instead. The setting
accepts only `ui` or `terminal` (case-insensitive). A command-line `--mode ui` or
`--mode terminal` overrides it for one run; legacy `--no-ui` also selects terminal
mode. Restart the app after changing `env/.env`.

Useful options:

```bash
# Keep using the original terminal prompts and workflow
python -m backend.main --no-ui

# Open the UI on a chosen port without opening a browser
python -m backend.main --port 8080 --no-browser

# Fetch immediately after starting the UI
python -m backend.main --auto-run

# Review one Gmail message, with the UI
python -m backend.main --message-id <message_id>

# UI dry-run: preview only; do not write to Redmine or processed-message storage
python -m backend.main --dry-run
```

Existing options `--skip-preflight`, `--lookback-hours`, and `--max-results` are
also available. Use `WEB_PORT` and `WEB_AUTO_OPEN_BROWSER` in `env/.env` to set
the default UI port and browser behavior.

#### Gemini token usage

The UI reports provider-returned Gemini input, output/completion, thinking, tool
input, and total tokens for the current app session, plus totals for the latest
processing run. Each reviewed email also shows its classification token counts.
These are Gemini API usage metadata, not an estimate or a dollar-cost calculation.
Counters restart when the application is restarted. Rule-based fallback
classifications have no Gemini token usage to report.

#### Local UI security

The UI binds only to `127.0.0.1`. At startup it creates a random session token and
opens the browser with that token in the URL; the page keeps it in memory and sends
it with API requests. The UI removes it from the visible URL after loading. The
server validates the Host header on every request, checks the Origin on mutating
requests, and applies a restrictive Content Security Policy. Email content is
displayed as text, not interpreted as HTML. Do not share the startup URL while the
server is running.

The pending review queue is stored locally in
`data/database/pending_items.sqlite3`. It contains email content and stays on this
computer. Do not commit the database, credentials, OAuth tokens, attachments, or
generated PDFs.

#### Review decisions

| Decision | Effect |
| --- | --- |
| Approve / edit | Create the Redmine ticket, or attach a reply to the selected existing ticket; record the message as processed |
| Reject | Record the message as rejected so it does not return in later runs |
| Skip | Leave the message unmarked so it can be reviewed in a later run |
| Dry-run preview | Show the ticket payload without Redmine writes or processed-message updates |

If the app closes while an email is awaiting review, that item is marked **FAILED**
with a message explaining that no approval or ticket creation occurred. Use **Retry**
to reopen the message for a fresh review. Older queue records labeled
`SUPERSEDED` are relabeled **APPROVED** for queue display; this is not a claim that
a Redmine ticket was created.

Attachment-handling notes are added to Redmine only when an attachment was skipped
or compressed. No note is added when attachments required no special handling or
when the email had no attachments.

## Gmail Setup Requirements

### 1. Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing one)
3. Enable the **Gmail API**:
   - Navigate to APIs & Services → Library
   - Search for "Gmail API"
   - Click "Enable"

### 2. Create OAuth 2.0 Credentials

1. Go to APIs & Services → Credentials
2. Click "Create Credentials" → "OAuth client ID"
3. Configure the OAuth consent screen first (if prompted):
   - Choose "External" user type
   - Fill in required fields (app name, user support email, etc.)
   - Add yourself as a test user
4. Create OAuth client ID:
   - Application type: **Desktop application**
   - Name: Gmail-Redmine (or any name)
5. Download the JSON file and save it as `env/credentials.json`

### 3. Initial Authentication

When you first run the application, it will:
1. Detect that `env/token.json` doesn't exist
2. Open a browser window for OAuth authentication
3. Ask you to sign in with your Google account
4. Request permission to read your Gmail
5. Generate and save `env/token.json` automatically

The token file is refreshed automatically when it expires.

## AI Classification (Gemini 3.1 Flash-Lite)

The system uses Google's Gemini 3.1 Flash-Lite model for intelligent email classification:

- First checks only the subject. Clearly obvious noise is skipped without sending
  the email body to Gemini.
- Possible issues and ambiguous subjects continue to a full-body classification
  before the UI asks for approval or rejection.
- Subject-only triage is deliberately conservative: when unsure, it inspects the body.
- **Classifies emails** as support tickets vs. regular emails
- **Extracts metadata**: center, module, description
- **Handles forwarded emails** and casual bug reports
- **Provides reasoning** for classification decisions
- **Falls back** to rule-based classification if AI fails

`ALLOWED_MODULES` is an optional comma-separated allow-list of module names used by
your project, for example `ALLOWED_MODULES=Admissions,Billing,Reporting`. Names are
matched exactly (including case). If the classifier returns a module not in the list,
the module is cleared and the email is marked as missing module information so it can
be reviewed. Leave the setting empty to accept any extracted module.

Textile is a markup format (for example, for headings, links, and lists). The old
`REDMINE_TEXT_FORMAT` setting was not read by the application and did not change
ticket descriptions. The app sends the description text to Redmine, where rendering
is governed by your Redmine instance. The unused variable has been removed; configure
description formatting in Redmine itself if needed.

## Attachment Handling

The system intelligently handles email attachments:

- **Size limit**: Configured via `REDMINE_MAX_ATTACHMENT_MB` (default 10 MB)
- **Video compression**: Automatically compresses oversized videos using FFmpeg
- **Skipped files**: Files that exceed limits are kept locally and noted in the ticket
- **Streaming uploads**: Large files are streamed to avoid memory issues

## CLI Options

- `--message-id`: Process a specific Gmail message
- `--dry-run`: Run without making changes to Redmine or database
- `--skip-preflight`: Skip preflight validation checks
- `--lookback-hours N`: Override Gmail lookback hours
- `--max-results N`: Override Gmail max results

## File Structure

```
Gmail-Redmine/
├── env/
│   ├── credentials.json  (Downloaded from Google Cloud Console)
│   ├── token.json        (Generated automatically on first run)
│   └── .env              (Manual configuration)
├── backend/
│   ├── config/
│   ├── gmail/
│   ├── redmine/
│   ├── ticketing/
│   ├── processor/        (Message workflow, attachments, and confirmation)
│   ├── storage/          (Processed-message data and attachment retention)
│   ├── utils/            (Preflight, links, logging, prompts, and retry)
│   ├── web/              (Local review server and static UI)
│   └── main.py
├── data/
│   ├── database/
│   └── logs/
├── email_data/
│   ├── email_attachments/ (Temporary downloaded email attachments; cleaned after upload)
│   └── generated_emails/  (Generated email PDFs; cleaned after upload)
```

## Important Notes

- **credentials.json** contains your OAuth client secrets - keep it secure
- **token.json** contains your access tokens - also keep secure
- Both files are in `.gitignore` and should NOT be committed to git
- The app uses Gmail API with `readonly` scope (can only read emails, not send/delete)
- You must be logged into the Google account that owns the Gmail during authentication
- FFmpeg is required for video compression (optional, but recommended)
