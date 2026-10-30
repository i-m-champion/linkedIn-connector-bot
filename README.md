<div align="center">

# ⚡ LinkedIn Connector Bot

### Enterprise Multi-Tenant Profile Orchestration & Automated Network Growth Engine

[![Python Version](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-45BA4B?style=for-the-badge&logo=playwright&logoColor=white)](https://playwright.dev)
[![SQLite](https://img.shields.io/badge/SQLite-aiosqlite%20WAL-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-Glassmorphism-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)](https://tailwindcss.com)
[![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)

<p align="center">
  <b>High-Intent Quality Targeting</b> • 
  <b>24/7 Background Auto-Pilot</b> • 
  <b>Self-Healing Watchdog</b> • 
  <b>Incoming Request Acceptance</b> • 
  <b>Stealth Isolation</b>
</p>

[Key Features](#-key-features) •
[Architecture](#-system-architecture) •
[Quickstart](#-quickstart-guide) •
[CLI Management](#-daemon--cli-controls) •
[Configuration](#-automation-workflow--strategy) •
[Safety & Stealth](#-anti-detection--stealth-engine)

---

</div>

## 🌟 Key Features

<table>
  <tr>
    <td width="50%">
      <h3>🎯 High-Intent Quality Targeting</h3>
      <ul>
        <li>Direct integration with <b>LinkedIn My Network</b> recommendations.</li>
        <li>Dynamic client-side DOM scanner with smart role classification:
          <ul>
            <li><b>Tech Founders & C-Suite</b> (Founders, Co-Founders, CEOs, CTOs)</li>
            <li><b>Tech HR & Recruiters</b> (Talent Acquisition, Head of People)</li>
            <li><b>SWEs & Technical Leaders</b> (Staff/Senior SWEs, AI/ML, DevOps)</li>
          </ul>
        </li>
        <li>Prioritizes candidates having <b>&ge; 100 mutual connections</b> and boosts <i>"Based on your recent activity"</i> candidates.</li>
      </ul>
    </td>
    <td width="50%">
      <h3>🛡️ Self-Healing Watchdog & Resilience</h3>
      <ul>
        <li>Active background watchdog continuously checks profiles every 60 seconds.</li>
        <li>Automatically detects and recovers stuck sessions (<code>authenticating</code> or <code>running</code> &gt; 15 mins) back to <code>idle</code>.</li>
        <li>Auto-cleans stale Chromium lockfiles (<code>Singleton*</code>) and dangling processes before launching.</li>
        <li>Emergency one-click <b>Unlock / Reset</b> control in the Web UI.</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>📥 Automatic Incoming Request Acceptance</h3>
      <ul>
        <li>Scans <code>/mynetwork/invitation-manager/</code> on every cycle and auto-accepts pending requests.</li>
        <li>Accepted invitations do <b>not</b> consume your daily outgoing connection limit.</li>
        <li>Tracks accepted connections separately with timestamps in SQLite.</li>
        <li>One-click <b>"📥 Accept Pending"</b> trigger available on dashboard.</li>
      </ul>
    </td>
    <td width="50%">
      <h3>🔒 Multi-Tenant Profile Stealth Isolation</h3>
      <ul>
        <li>Independent persistent browser profiles under <code>./browser_profiles/&lt;profile_id&gt;</code>.</li>
        <li>Complete isolation of cookies, localStorage, session storage, and cache.</li>
        <li>Per-profile HTTP / HTTPS / SOCKS5 proxy configuration.</li>
        <li>Evasion script injection: masks <code>navigator.webdriver</code>, emulates realistic navigator properties, and patches permissions.</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>⏰ 24/7 Background Auto-Pilot</h3>
      <ul>
        <li>Configurable daily run hour (e.g. 09:00 AM local time) per profile.</li>
        <li>Guaranteed daily cap fulfillment (default 30/day) with adaptive candidate expansion.</li>
        <li>Runs headlessly and silently in the background with randomized human-like delays.</li>
        <li>Automatic once-per-day enforcement with SQLite history tracking.</li>
      </ul>
    </td>
    <td width="50%">
      <h3>💻 Modern Glassmorphism Dashboard</h3>
      <ul>
        <li>Built with <b>Tailwind CSS</b> featuring high-contrast dark mode.</li>
        <li>Real-time live scheduler monitoring and status badges.</li>
        <li>Single-target quick queue and bulk drag-and-drop CSV importer.</li>
        <li>Headed manual login mode for 2FA/CAPTCHA resolution in a new tab.</li>
      </ul>
    </td>
  </tr>
</table>

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph UI ["Client Layer"]
        DASH["Modern Tailwind Dashboard\n(http://127.0.0.1:8000)"]
        CLI["CLI Management Scripts\n(start / stop / status)"]
    end

    subgraph CORE ["FastAPI Orchestration Core"]
        APP["FastAPI Web Server\n(main.py)"]
        SCHED["24/7 Daily Auto-Pilot Scheduler\n(scheduler.py)"]
        WATCH["Self-Healing Watchdog\n(auto_heal_stuck_profiles)"]
        DB[(SQLite aiosqlite\nWAL Mode)]
    end

    subgraph ENGINE ["Browser Automation Engine (Playwright)"]
        PE["Context Manager & Stealth Layer\n(browser_engine.py)"]
        DISC["My Network DOM Scanner\n(>=100 Mutuals, Tech Roles)"]
        ACC["Incoming Invitation Manager\n(/mynetwork/invitation-manager/)"]
        PROFILES["Isolated Storage\n(./browser_profiles/<id>)"]
    end

    subgraph EXT ["Target Web Platform"]
        LI["LinkedIn Web Application"]
    end

    DASH -->|REST / HTML| APP
    CLI -->|Signals / API| APP
    APP --> SCHED
    SCHED --> WATCH
    APP --> DB
    SCHED --> DB
    SCHED -->|Dispatches Runs| PE
    APP -->|Interactive Auth| PE
    PE --> DISC
    PE --> ACC
    PE --> PROFILES
    DISC -->|Human Delayed Interaction| LI
    ACC -->|Auto-Accept| LI
```

---

## 📁 Directory Structure

```
linkedIn-connector-bot/
├── browser_engine.py       # Playwright browser manager, stealth injection, DOM parser
├── database.py             # Asynchronous SQLite persistence with WAL mode & schemas
├── main.py                 # FastAPI application routes, UI endpoints, CSV handlers
├── scheduler.py            # 24/7 background scheduler loop & self-healing watchdog
├── test_system.py          # Comprehensive unit & integration smoke test suite
├── start_bot.sh            # Production-grade background startup script
├── stop_bot.sh             # Graceful shutdown & lockfile cleanup script
├── status_bot.sh           # Process health & live scheduler inspector
├── requirements.txt        # Python dependency manifest
├── templates/
│   └── index.html          # Responsive glassmorphism dashboard (Tailwind CSS)
└── browser_profiles/       # Persistent, isolated profile session directories (ignored in git)
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites

- Python 3.9+ installed
- Google Chrome or Chromium browser installed
- macOS or Linux operating system

### 2. Setup Environment & Dependencies

```bash
# Clone the repository
git clone https://github.com/i-m-champion/linkedIn-connector-bot.git
cd linkedIn-connector-bot

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install required packages
pip install -r requirements.txt

# Install Playwright browser binaries
playwright install chromium
```

### 3. Run Automated Smoke Tests

Verify your local installation, database routines, and browser engine mocks:

```bash
python -m unittest test_system.py
```

### 4. Start the Application

You can launch the bot using the included CLI daemon script or directly via Uvicorn:

```bash
# Option A: Recommended background daemon
./start_bot.sh

# Option B: Direct interactive foreground
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser at **[http://127.0.0.1:8000](http://127.0.0.1:8000)** to view the live dashboard.

---

## 🕹️ Daemon & CLI Controls

Convenient shell scripts are included to manage the bot as a background service:

| Command | Action | Description |
|:---|:---|:---|
| `./start_bot.sh` | **Start / Boot** | Kills stale port listeners, removes orphaned `Singleton*` locks, and boots Uvicorn in the background. |
| `./status_bot.sh` | **Health Check** | Displays PID, CPU/Memory consumption, and live scheduler JSON status. |
| `./stop_bot.sh` | **Stop / Teardown** | Gracefully terminates running processes and purges session locks. |

---

## ⚙️ Automation Workflow & Strategy

### 1. Adding a Profile
- Navigate to **"New Profile"** on the dashboard.
- Assign an alphanumeric ID (e.g. `primary_outreach`), descriptive name, optional proxy (`http://user:pass@host:port`), and your desired `daily_limit` (recommended: `25`–`35`).

### 2. Headed Login & Initial Session Verification
- Click **"Headed Login"** on your profile card.
- A visible Chrome window will launch. Log in with your LinkedIn credentials (and complete 2FA or CAPTCHA if prompted).
- Sessions, cookies, and local credentials are saved permanently inside `./browser_profiles/<profile_id>`. Future runs operate completely headlessly.

### 3. 24/7 Daily Auto-Pilot
- Set your preferred schedule hour (default `09` = 09:00 AM local time).
- When the scheduler fires:
  1. **Phase 1: Accept Pending Invitations**: Navigates to `/mynetwork/invitation-manager/` and clicks *Accept* on all incoming connection requests.
  2. **Phase 2: High-Quality Growth**: Navigates to `/mynetwork/grow/`, evaluates candidate quality, and connects with verified Tech Founders, HR recruiters, and Software Engineers with $\ge 100$ mutual connections.
  3. **Phase 3: Completion**: Once the daily limit is achieved, the date is recorded in SQLite and the profile returns to `idle`.

### 4. Dashboard Manual Triggers
- **`▶️ Run Today Now`**: Immediately triggers today's automated run without waiting for the scheduled hour.
- **`📥 Accept Pending`**: Immediately runs a standalone pass to accept incoming connection requests.
- **`⏸️ Pause Auto-Pilot`**: Temporarily suspends automated runs for a given profile.
- **`Unlock / Reset`**: Emergency reset if a profile is ever left in a non-idle status.

---

## 🛡️ Anti-Detection & Stealth Engine

To ensure compliance with human browsing behavior and account longevity, the engine employs multi-layer stealth mechanisms:

> [!TIP]
> **Account Longevity Best Practice**: Keep daily connection limits between **25 and 35 invitations per day** to stay well within standard LinkedIn safety thresholds.

- **Stealth Flag Masking**: Neutralizes `navigator.webdriver`, injects authentic `window.chrome` runtimes, and mimics legitimate browser language and notification permissions.
- **Polite Interaction Jitter**: Every mouse move, scroll gesture, and button click includes randomized human delays ($1.0$s to $2.5$s).
- **Adaptive Fallback**: If proxies encounter connection timeout or authentication failures, the session gracefully handles the network state without crashing the scheduler.
- **Isolated State**: Profiles never share storage, cookies, or browser cache, eliminating cross-contamination.

---

## 📊 REST API Endpoints

The dashboard is powered by clean REST endpoints that can also be integrated into custom tools:

| Endpoint | Method | Purpose |
|:---|:---:|:---|
| `/api/scheduler/status` | `GET` | Returns live status of the scheduler and all profile auto-pilot schedules. |
| `/api/profiles` | `GET` | List all profiles, daily limits, and current execution states. |
| `/api/stats` | `GET` | Aggregated statistics (total sent, today's count, queue counts). |
| `/api/profiles/reset/{id}` | `POST` | Emergency unlocks profile status back to `idle`. |
| `/profiles/auth/{id}` | `POST` | Launches headed browser session for interactive authentication. |
| `/scheduler/trigger/{id}` | `POST` | Triggers immediate auto-pilot run for target profile. |

---

## ⚖️ Disclaimer & Responsible Use

> [!IMPORTANT]
> This project is designed for educational, research, and personal productivity purposes. Automation of web platforms must always adhere to platform Terms of Service and local privacy regulations. The author and contributors are not responsible for any misuse, rate limits, or account restrictions resulting from improper usage.

---

<div align="center">
  <sub>Built with ❤️ for intelligent, quality-first professional networking automation.</sub>
</div>
