# CivicPulse — AI-Powered Civic Grievance Agent

> Report civic issues in seconds. AI analyzes, routes, drafts, and sends complaints automatically.

---

## Quick Start

### 1. Clone & Setup Backend

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# Fill in your API keys in .env
```

### 2. Setup Supabase Database

1. Go to supabase.com → New Project
2. Open SQL Editor
3. Paste and run `backend/schema.sql`
4. Copy your Project URL and anon key to `.env`

### 3. Get API Keys

| Service | URL | Free Tier |
|---|---|---|
| Gemini API | gemini.google.dev | Yes |
| Supabase | supabase.com | Yes |
| Tavily | tavily.com | 1000 req/mo |
| Gmail OAuth | console.cloud.google.com | Yes |
| Twitter API | developer.twitter.com | 500k reads/mo |

### 4. Gmail OAuth Setup (for sending emails)

```bash
pip install google-auth-oauthlib
python -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file('credentials.json', ['https://www.googleapis.com/auth/gmail.send'])
creds = flow.run_local_server()
print('REFRESH_TOKEN:', creds.refresh_token)
"
```

### 5. Run Backend

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Backend runs at: http://localhost:8000
API docs at: http://localhost:8000/docs

### 6. Open Frontend

```bash
# Just open the HTML file in browser
open frontend/index.html

# OR serve it
cd frontend && python -m http.server 3000
```

---

## Project Structure

```
civicpulse/
├── backend/
│   ├── main.py              # FastAPI app — all routes
│   ├── agents/
│   │   ├── analyst.py       # Agent A: Gemini Vision analysis
│   │   ├── router.py        # Agent B: Department finder
│   │   └── executor.py      # Agent C: Email + Tweet sender
│   ├── utils/
│   │   └── db.py            # Supabase helpers + duplicate detection
│   ├── schema.sql           # Database schema
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    └── index.html           # Full UI (no build needed)
```

---

## API Endpoints

```
POST /report          — File a new complaint
GET  /issue/{id}      — Get issue by ID
GET  /issues          — Get all issues (for map)
PATCH /issue/{id}/status — Update issue status
```

---

## Demo Flow (For Hackathon Presentation)

1. Open frontend → Report page
2. Upload photo of a pothole / broken light
3. Type description + location
4. Drop pin on Bengaluru map
5. Click FILE COMPLAINT
6. Watch 7 AI agent steps run live
7. Show complaint letter generated
8. Show email sent (or drafted)
9. Show tweet posted with dept tagged
10. Switch to Dashboard → show pin on map
11. Track page → show issue timeline

---

## Agent Pipeline

```
User Input (photo + location + description)
         ↓
    Agent A (Gemini Vision)
    • Classify category
    • Score severity 1-10
    • Extract visual details
    • Validate complaint
         ↓
    Duplicate Check
    • 500m radius search
    • Merge if duplicate
    • Increment report count
         ↓
    Agent B (Tavily + Gemini)
    • Search responsible dept
    • Find official email
    • Find Twitter handle
         ↓
    Agent C (Gmail + Twitter)
    • Draft formal letter
    • Send email to dept
    • Post public tweet
    • Save to Supabase
         ↓
    Issue Tracker (Supabase)
    • Live status updates
    • Map visualization
    • Resolution tracking
```
