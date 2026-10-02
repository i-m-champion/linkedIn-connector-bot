# Multi-Profile Web Automation Management System

An enterprise multi-tenant profile orchestration and automated queuing system built with **FastAPI**, **SQLite (aiosqlite)**, and **Playwright**.

---

## Key Features

1. **Multi-Tenant Profile Isolation**:
   - Each profile maintains its own dedicated, persistent browser directory under `./browser_profiles/<profile_id>`.
   - Cookies, local storage, sessions, and cache are completely isolated.
   - Built-in stealth script injection (`navigator.webdriver` masking, randomized navigator properties, automation flag removal).

2. **Per-Profile Proxy Support**:
   - Configure individual HTTP, HTTPS, or SOCKS5 proxies per profile (supports authentication: `http://user:password@host:port`).

3. **Headed Interactive Authentication**:
   - Operators can launch a headed (visible) browser session with a single click to log into web portals, solve CAPTCHAs, or complete two-factor authentication (2FA).
   - Sessions and cookies are saved directly into the profile's persistent directory.

4. **Intelligent Queue & Automation Engine**:
   - Asynchronous batch execution honoring each profile's configured `daily_limit`.
   - Human-like scrolling, randomized interaction delays (5–15 seconds), and automatic action detection (Connect, Follow, custom messages, "More" dropdown detection).
   - Real-time status tracking (`idle`, `running`, `authenticating`).

5. **Modern Dark-Mode Dashboard**:
   - Built with Tailwind CSS and modern glassmorphism aesthetics.
   - Single-target enqueuing, drag-and-drop CSV batch importing, downloadable CSV templates, and live auto-refreshing stats.

---

## Directory Structure

```
├── browser_engine.py       # Playwright automation routines, persistent context manager & stealth
├── database.py             # Asynchronous SQLite operations with WAL mode (aiosqlite)
├── main.py                 # FastAPI application, routing, CSV parsing, background dispatchers
├── requirements.txt        # Python dependency manifest
├── templates/
│   └── index.html          # Responsive Tailwind CSS dashboard
├── test_system.py          # Automated integration & smoke test suite
└── browser_profiles/       # (Generated) Isolated profile storage directories
```

---

## Quickstart Guide

### 1. Set Up Environment & Install Dependencies

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright Chromium browser binary
playwright install chromium
```

### 2. Run the Smoke Test Suite

```bash
python test_system.py
```

### 3. Launch the Web Application

```bash
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser at **[http://127.0.0.1:8000](http://127.0.0.1:8000)** to access the dashboard.

---

## Workflow Guide

1. **Create a Profile**:
   - Click **"New Profile"** in the top navigation.
   - Enter a unique slug (e.g., `sales_agent_01`), a descriptive label, optional proxy URL, and daily interaction cap.

2. **Authenticate Session (Opens in New Tab)**:
   - Click **"Headed Login (New Tab)"** on any profile card.
   - The browser opens in a **new tab in the same browser window** (using Google Chrome with persistent storage).
   - Log into your LinkedIn account normally (complete 2FA / CAPTCHA if prompted).
   - **Automatic Invitation Sending**: Upon detecting login, the system automatically opens the "My Network" page in a new tab, locates all recommendation cards with **more than 100 mutual connections**, and **automatically clicks "Connect" and sends connection requests** with polite human delays!

3. **24/7 Daily Auto-Pilot with 50/50 Dual Strategy**:
   - Built directly into the application lifespan via `scheduler.py`.
   - Runs continuously in the background, checking every 60 seconds.
   - Every day at the configured schedule hour (e.g. 09:00 AM local time), it automatically runs the **50/50 Dual Strategy** without asking for operator permission:
     - **Method 1 (50% via LinkedIn Direct Search)**:
       - Navigates to LinkedIn People Search with rotating high-intent queries:
         1. **High-Profile Tech Leaders**: `"VP of Engineering"`, `"Director of Engineering"`, `"Head of Engineering"`, `"CTO"`, `"Tech Founder"`.
         2. **Tech Insiders**: `"Staff Software Engineer"`, `"Principal Software Engineer"`, `"Distinguished Engineer"`, `"Tech Lead"`.
         3. **Tech Companies HRs**: `"Technical Recruiter"`, `"Lead Technical Recruiter"`, `"Head of Talent"`, `"Talent Acquisition"`.
       - Scans search cards, detects direct Connect buttons (or navigates to profile), handles "Send without a note" modals, and dispatches 50% of the daily limit (~15/day).
     - **Method 2 (50% via My Network Recommendations)**:
       - Navigates to My Network (`https://www.linkedin.com/mynetwork/grow/`).
       - Finds candidates having **>100 mutual connections** (prioritizing **Recent Activity** and verified tech roles).
       - Automatically sends 50% of the daily limit (~15/day).
     - **Smart Remainder Fulfillment**:
       - If either method exhausts available candidates on a given pass, the supplemental method automatically steps in to ensure the full daily cap (e.g. 30/30) is 100% achieved every single day.
   - **Guaranteed Daily Cap Completion**: Tracks actual invitations sent today in SQLite split by method (`today_search_count` and `today_network_count`).
   - Records the run date in SQLite so it runs at most once per calendar day.
   - **Dashboard Controls**:
     - Toggle Auto-Pilot on/off for any profile with `⏸️ Pause Auto-Pilot` / `⚡ Auto-Pilot ON`.
     - Click `▶️ Run Today Now` to immediately trigger today's automated run without waiting.
     - Customize the daily schedule hour (0-23) in the profile edit modal.

4. **Edit Profile Settings & Proxies**:
   - Click the pencil icon on any profile card to edit the label, daily limit, proxy URL, schedule hour, or Auto-Pilot status.
   - If you do not have a dedicated proxy, leave the proxy field blank.

5. **Enqueue Targets Manually or via CSV**:
   - **Single URL**: Use the "Quick-Queue Single Target" form on the dashboard.
   - **Bulk CSV**: Click **"Import CSV"** to upload a CSV file containing `target_url` and optional `custom_note` columns. You can download a sample CSV directly from the header.

6. **Run Batch on Specific Queued Items**:
   - Click **"Run Batch"** on any profile with pending items.
   - The background worker will process up to `daily_limit` items sequentially with polite delays.

