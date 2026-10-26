# Gmail-Redmine Integration

Automated Gmail to Redmine ticket creation with AI-powered email classification.

## Features

- **AI-Powered Classification**: Uses Google Gemini 2.5 Flash for intelligent email classification
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
REDMINE_ISSUES_CATEGORY=1
REDMINE_ISSUES_CATEGORY_VALUE=General/ALL modules

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
ALLOWED_MODULES=Module1,Module2,Module3
REDMINE_TEXT_FORMAT=textile

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

Or process a specific message:

```bash
python -m backend.main --message-id <message_id>
```

Dry-run mode (no changes to Redmine):

```bash
python -m backend.main --dry-run
```

Skip preflight validation:

```bash
python -m backend.main --skip-preflight
```

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

## AI Classification (Gemini 2.5 Flash)

The system uses Google's Gemini 2.5 Flash model for intelligent email classification:

- **Classifies emails** as support tickets vs. regular emails
- **Extracts metadata**: center, module, description
- **Handles forwarded emails** and casual bug reports
- **Provides reasoning** for classification decisions
- **Falls back** to rule-based classification if AI fails

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
│   ├── processor/
│   ├── storage/
│   ├── preflight.py      (Preflight validation)
│   ├── attachments.py    (Attachment pipeline)
│   ├── links.py          (Link detection)
│   ├── retry.py          (Retry/backoff logic)
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
