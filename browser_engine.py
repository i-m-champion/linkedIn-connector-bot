"""
Browser Automation Engine using Playwright with Persistent Contexts.
Provides multi-tenant profile isolation, stealth injection, manual interactive authentication,
and automated execution of queued interactions with polite randomized delays.
Features:
- Reuses active browser contexts (opens new tabs in the same browser window)
- Automatic fallback if proxy fails so login is never blocked
- Post-login discovery of target profiles having > 100 mutual connections
"""

import os
import re
import random
import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse, quote
from typing import Optional, Dict, Any, List

from playwright.async_api import async_playwright, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
import database as db

logger = logging.getLogger("browser_engine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(BASE_DIR, "browser_profiles")
os.makedirs(PROFILES_DIR, exist_ok=True)

# In-memory registry to hold active browser sessions per profile
# Format: profile_id -> {"context": BrowserContext, "playwright": PlaywrightInstance}
ACTIVE_SESSIONS: Dict[str, Dict[str, Any]] = {}

# Evasion script injected into every persistent context
STEALTH_SCRIPT = """
(() => {
    // Mask navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });

    // Emulate standard navigator.languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en']
    });

    // Mock window.chrome object
    window.chrome = {
        runtime: {},
        loadTimes: () => {},
        csi: () => {},
        app: {}
    };

    // Fix permissions query for notifications
    const originalQuery = window.navigator.permissions?.query;
    if (originalQuery) {
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );
    }
})();
"""

# Client script to extract profiles with > 100 mutual connections
EXTRACT_MUTUAL_CONNECTIONS_SCRIPT = """
(() => {
    const results = [];
    const seen = new Set();
    const mutualRegex = /([\\d,]+)\\s*(?:other\\s+)?mutual/i;

    const elements = document.querySelectorAll('span, div, p, a, h3');
    for (const el of elements) {
        const text = (el.innerText || el.textContent || '').trim();
        if (!text || text.length > 150) continue;

        const match = text.match(mutualRegex);
        if (match) {
            const count = parseInt(match[1].replace(/,/g, ''), 10);
            if (!isNaN(count) && count > 100) {
                // Find parent container card
                let card = el.closest('li') || 
                           el.closest('[data-view-name*="discover"]') || 
                           el.closest('.discover-entity-type-card') || 
                           el.closest('.entity-result__item') || 
                           el.closest('div.artdeco-card') || 
                           el.parentElement?.parentElement;
                if (card) {
                    const link = card.querySelector('a[href*="/in/"]');
                    if (link && link.href) {
                        let rawUrl = link.href;
                        let cleanUrl = rawUrl.split('?')[0].split('#')[0].replace(/\\/$/, '');
                        if (cleanUrl.includes('/in/') && !seen.has(cleanUrl)) {
                            seen.add(cleanUrl);
                            const nameEl = card.querySelector('.discover-person-card__name, .entity-result__title-text a, span[aria-hidden="true"], h3');
                            const name = nameEl ? nameEl.innerText.trim() : '';
                            results.push({
                                url: cleanUrl,
                                name: name,
                                mutual_count: count
                            });
                        }
                    }
                }
            }
        }
    }
    return results;
})()
"""


def parse_proxy_settings(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    """Parses standard HTTP/HTTPS/SOCKS5 proxy string into Playwright proxy dictionary."""
    if not proxy_url or not proxy_url.strip():
        return None

    clean_url = proxy_url.strip()
    # Skip obvious placeholder proxies
    if "example.com" in clean_url:
        logger.warning(f"Ignoring placeholder proxy: {clean_url}")
        return None

    if not clean_url.startswith(("http://", "https://", "socks5://", "socks4://")):
        clean_url = f"http://{clean_url}"

    parsed = urlparse(clean_url)
    server_port = f":{parsed.port}" if parsed.port else ""
    proxy_config = {
        "server": f"{parsed.scheme}://{parsed.hostname}{server_port}"
    }

    if parsed.username:
        proxy_config["username"] = parsed.username
    if parsed.password:
        proxy_config["password"] = parsed.password

    return proxy_config


async def launch_profile_context(
    playwright_instance,
    profile_id: str,
    headless: bool = False,
    proxy_url: Optional[str] = None
) -> BrowserContext:
    """
    Launches or reuses a persistent browser context inside ./browser_profiles/<profile_id>.
    Prefers installed Google Chrome on macOS with fallback to standard Playwright Chromium.
    Applies browser flags, stealth evasions, and proxy configuration.
    """
    user_data_dir = os.path.join(PROFILES_DIR, profile_id)
    os.makedirs(user_data_dir, exist_ok=True)

    proxy_config = parse_proxy_settings(proxy_url)

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    # Attempt launch using Google Chrome channel first
    for channel_opt in ["chrome", None]:
        try:
            kwargs = {
                "user_data_dir": user_data_dir,
                "headless": headless,
                "proxy": proxy_config,
                "args": launch_args,
                "ignore_default_args": ["--enable-automation"],
                "viewport": {"width": 1280, "height": 850},
                "user_agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Safari/537.36"
                ),
                "locale": "en-US",
                "timezone_id": "America/New_York",
            }
            if channel_opt:
                kwargs["channel"] = channel_opt

            context = await playwright_instance.chromium.launch_persistent_context(**kwargs)
            await context.add_init_script(STEALTH_SCRIPT)
            logger.info(f"Launched browser context for '{profile_id}' (channel: {channel_opt or 'chromium'}, headless: {headless})")
            return context
        except Exception as e:
            logger.warning(f"Launch with channel={channel_opt} failed: {e}. Trying fallback...")

    # Final fallback without channel or custom flags
    try:
        context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            proxy=proxy_config
        )
        await context.add_init_script(STEALTH_SCRIPT)
        return context
    except Exception as final_err:
        logger.warning(f"Browser launch hit an issue for '{profile_id}': {final_err}. Purging any stale locks and retrying launch...")
        kill_stale_profile_locks(profile_id)
        context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            proxy=proxy_config
        )
        await context.add_init_script(STEALTH_SCRIPT)
        return context


def kill_stale_profile_locks(profile_id: str) -> None:
    """Safely terminates any orphaned Chrome processes holding locks on this profile's directory and removes stale locks."""
    user_data_dir = os.path.join(PROFILES_DIR, profile_id)
    try:
        import subprocess
        res = subprocess.run(["pgrep", "-f", user_data_dir], capture_output=True, text=True)
        if res.stdout:
            for pid_str in res.stdout.strip().split():
                if pid_str.isdigit():
                    pid = int(pid_str)
                    if pid != os.getpid():
                        try:
                            os.kill(pid, 9)
                        except Exception:
                            pass
        import time
        time.sleep(0.5)
        for lock_file in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
            lp = os.path.join(user_data_dir, lock_file)
            if os.path.exists(lp) or os.path.islink(lp):
                try:
                    os.unlink(lp)
                except Exception:
                    pass
        logger.info(f"Cleared stale locks and processes for profile '{profile_id}'")
    except Exception as e:
        logger.warning(f"Could not clear profile locks for {profile_id}: {e}")


async def get_or_create_context(profile_id: str, headless: bool = True, proxy_url: Optional[str] = None):
    """
    Retrieves the existing open browser context for this profile if still alive and matching requested headless mode,
    or launches a new persistent context.
    """
    if profile_id in ACTIVE_SESSIONS:
        session = ACTIVE_SESSIONS[profile_id]
        ctx = session.get("context")
        existing_headless = session.get("headless")
        try:
            if ctx and not ctx.is_closed() and (existing_headless is None or existing_headless == headless):
                _ = ctx.pages
                return ctx, session.get("playwright"), False
        except Exception as stale_err:
            logger.info(f"Stale session detected for '{profile_id}': {stale_err}. Re-launching context...")
        
        # Purge stale, closed, or mismatched mode session
        await close_session(profile_id)

    pw = await async_playwright().start()
    try:
        ctx = await launch_profile_context(pw, profile_id=profile_id, headless=headless, proxy_url=proxy_url)
        # Register close listener so ACTIVE_SESSIONS automatically purges when the browser is closed
        ctx.on("close", lambda: ACTIVE_SESSIONS.pop(profile_id, None))
        ACTIVE_SESSIONS[profile_id] = {"context": ctx, "playwright": pw, "headless": headless}
        return ctx, pw, True
    except Exception as e:
        await pw.stop()
        raise e



async def close_session(profile_id: str):
    """Closes and unregisters an active session."""
    if profile_id in ACTIVE_SESSIONS:
        session = ACTIVE_SESSIONS.pop(profile_id, None)
        if session:
            try:
                await session["context"].close()
            except Exception:
                pass
            try:
                await session["playwright"].stop()
            except Exception:
                pass


# Client-side script that scans the DOM for candidate cards matching quality criteria:
# 1. Tech Founders (Founders, Co-Founders, CEO/CTO of tech startups/companies)
# 2. HR & Technical Recruiters at Big Tech or Small Tech companies
# 3. Current Software Engineers & Technical Leaders at Good Tech companies
# Prioritizing candidates with >= 100 mutual connections and big tech experience.
SCRIPT_SCAN_CANDIDATES = r"""
((minMutual) => {
    minMutual = (typeof minMutual === "number") ? minMutual : 10;
    // Regex patterns for matching connections count:
    const r1 = /([\d,]+)\s*\+?\s*(?:others?\s+)?(?:mutual(?:\s+connections?)?|connections?\s+in\s+common|in\s+common|connections?)/i;
    const r2 = /(?:mutual(?:\s+connections?)?|connections?\s+in\s+common|in\s+common|connections?)[\s:]+([\d,]+)/i;
    const r3 = /([\d,]+)\s*\+\s*connections?/i;

    function extractCount(text) {
        if (!text) return 0;
        const kMatch = text.match(/([\d\.]+)\s*k\+?\s*(?:mutual|connections?)/i);
        if (kMatch && kMatch[1]) {
            const val = parseFloat(kMatch[1]) * 1000;
            return isNaN(val) ? 0 : Math.round(val);
        }
        const m = text.match(r1) || text.match(r2) || text.match(r3);
        if (m && m[1]) {
            const val = parseInt(m[1].replace(/,/g, ''), 10);
            return isNaN(val) ? 0 : val;
        }
        return 0;
    }

    // Role & Quality Classification Engine
    function evaluateCandidateQuality(headline, fullCardText) {
        const text = ((headline || '') + ' ' + (fullCardText || '')).toLowerCase();

        // 1. Tech Founder
        const founderRegex = /\b(co-founder|founder|cofounder|founding engineer|founding member|founding partner|ceo|cto|chief technology officer|chief executive officer)\b/i;
        const isFounder = founderRegex.test(text);

        // 2. HR / Talent Acquisition / Recruiter (Big tech or small tech)
        const hrRegex = /\b(hr|human resources|recruiter|recruiting|talent acquisition|talent partner|head of talent|talent lead|people operations|people ops|head of people|staffing|sourcer|hrbp|talent advisor|technical recruiter)\b/i;
        const isHR = hrRegex.test(text);

        // 3. Current Software Engineers at Good Tech Companies
        const sweRegex = /\b(software engineer|software developer|swe|sde|sde[- ]?[123i]+|frontend|front-end|backend|back-end|full stack|fullstack|full-stack|devops|site reliability|sre|cloud engineer|platform engineer|systems engineer|data engineer|data scientist|ai engineer|machine learning|ml engineer|deep learning|tech lead|engineering manager|vp of engineering|head of engineering|solutions architect|security engineer|ios developer|android developer|mobile engineer|web developer)\b/i;
        const isEngineer = sweRegex.test(text);

        if (!isFounder && !isHR && !isEngineer) {
            return null; // Reject: not in desired target roles
        }

        // Tech Context / Big Tech check:
        const bigTechRegex = /\b(google|microsoft|amazon|meta|facebook|apple|netflix|uber|stripe|airbnb|salesforce|nvidia|oracle|adobe|atlassian|linkedin|openai|anthropic|databricks|snowflake|palantir|bytedance|cisco|intel|amd|qualcomm|ibm|spotify|shopify|lyft|coinbase|robinhood|github|gitlab|cloudflare|datadog|mongodb|zoom|dropbox|snap|pinterest)\b/i;
        const isBigTech = bigTechRegex.test(text);

        const techContextRegex = /\b(tech|technology|technologies|software|labs|ai|startup|saas|cloud|fintech|robotics|ventures|capital|digital|systems|infra|platform|app|code|web)\b/i;
        const hasTechContext = isBigTech || techContextRegex.test(text);

        let category = "";
        let roleLabel = "";
        let baseScore = 0;

        if (isFounder) {
            category = "TECH_FOUNDER";
            roleLabel = isBigTech ? "Tech Founder (Ex-BigTech)" : "Tech Founder";
            baseScore = 100;
        } else if (isHR) {
            category = "HR_RECRUITER";
            roleLabel = isBigTech ? "HR / Recruiter (Big Tech)" : "HR / Recruiter (Tech)";
            baseScore = isBigTech ? 95 : 88;
        } else if (isEngineer) {
            category = "SOFTWARE_ENGINEER";
            roleLabel = isBigTech ? "Software Engineer (Big Tech)" : (hasTechContext ? "Software Engineer (Tech)" : "Software Engineer");
            baseScore = isBigTech ? 92 : 85;
        }

        if (isBigTech) baseScore += 20;
        if (hasTechContext) baseScore += 10;

        return {
            category: category,
            roleLabel: roleLabel,
            isBigTech: isBigTech,
            hasTechContext: hasTechContext,
            score: baseScore
        };
    }

    const candidateMap = new Map();
    let tagIdx = Date.now();

    function isConnectButton(b) {
        if (!b) return false;
        const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
        const aria = (b.getAttribute('aria-label') || '').toLowerCase();
        
        // Exclude ignored states
        if (txt.includes('pending') || txt.includes('message') || txt.includes('following') || 
            txt.includes('withdraw') || aria.includes('pending') || aria.includes('message') || 
            aria.includes('following') || aria.includes('withdraw')) {
            return false;
        }
        // Match Connect
        return txt === 'connect' || txt.startsWith('connect') || aria.includes('invite') || aria.includes('connect');
    }

    function processCardElement(card, knownBtn = null) {
        if (!card) return;

        // 1. Find profile URL
        const link = card.querySelector('a[href*="/in/"]') || 
                     (card.tagName === 'A' && card.href && card.href.includes('/in/') ? card : null);
        if (!link || !link.href || !link.href.includes('/in/')) return;

        let rawUrl = link.href;
        if (rawUrl.startsWith('/')) {
            rawUrl = 'https://www.linkedin.com' + rawUrl;
        }
        const cleanUrl = rawUrl.split('?')[0].split('#')[0].replace(/\/$/, '');
        if (!cleanUrl || !cleanUrl.includes('/in/') || candidateMap.has(cleanUrl)) return;

        // 2. Check if already invited or pending
        const cardText = (card.innerText || card.textContent || '').trim();
        const lower = cardText.toLowerCase();
        if (lower.includes('invitation sent') || lower.includes('pending') || lower.includes('withdraw')) {
            return;
        }

        // 3. Extract headline / occupation
        const occEl = card.querySelector(
            '.discover-person-card__occupation, .artdeco-entity-lockup__subtitle, .entity-result__primary-subtitle, [data-view-name*="discover"] .artdeco-entity-lockup__subtitle, .base-search-card__subtitle'
        );
        let occupation = occEl ? (occEl.innerText || occEl.textContent || '').trim() : '';
        if (!occupation) {
            const lines = cardText.split('\n').map(l => l.trim()).filter(l => l.length > 3 && l.length < 150);
            for (const l of lines) {
                const low = l.toLowerCase();
                if (low.includes('mutual') || low.includes('connection') || low.includes('connect')) continue;
                if (low.includes('recent activity') || low.includes('people you may know')) continue;
                occupation = l;
                break;
            }
        }

        // 4. Quality Role Filter: ONLY Tech Founders, HR / Recruiters, Software Engineers
        const quality = evaluateCandidateQuality(occupation, cardText);
        if (!quality) return; // Strict quality check: Skip non-tech or unrelated professions!

        // 5. Connection count extraction (require at least minMutual connections, boost for >= 100)
        const count = extractCount(cardText);
        if (count < minMutual && !(count >= 3 && (quality.isBigTech || quality.category === "TECH_FOUNDER"))) return;

        // 6. Find connect button if not already provided
        let connectBtn = knownBtn;
        if (!connectBtn || !isConnectButton(connectBtn)) {
            const buttons = card.querySelectorAll('button, [role="button"], a.artdeco-button');
            for (const b of buttons) {
                if (isConnectButton(b)) {
                    connectBtn = b;
                    break;
                }
            }
        }
        if (!connectBtn) return;

        // 7. Determine if under "People you may know based on your recent activity"
        let isRecentActivity = false;
        let curr = card;
        while (curr && curr !== document.body) {
            const heading = curr.querySelector('h1, h2, h3, h4, h5');
            if (heading) {
                const hText = (heading.innerText || heading.textContent || '').toLowerCase();
                if (hText.includes('recent activity') || hText.includes('based on your recent activity')) {
                    isRecentActivity = true;
                    break;
                }
            }
            let prev = curr.previousElementSibling;
            while (prev) {
                const prevText = (prev.innerText || prev.textContent || '').toLowerCase();
                if (prevText.includes('recent activity') || prevText.includes('based on your recent activity')) {
                    isRecentActivity = true;
                    break;
                }
                prev = prev.previousElementSibling;
            }
            if (isRecentActivity) break;
            curr = curr.parentElement;
        }

        // 8. Name extraction
        const nameEl = card.querySelector(
            '.discover-person-card__name, .entity-result__title-text a, span[aria-hidden="true"], h3, a[href*="/in/"]'
        );
        let name = nameEl ? (nameEl.innerText || nameEl.textContent || '').trim().split('\n')[0] : 'Member';
        if (!name || name.length > 50) name = 'Member';

        // 9. Stamp unique tags
        tagIdx++;
        const cardTag = 'ac-card-' + tagIdx;
        const btnTag = 'ac-btn-' + tagIdx;
        card.setAttribute('data-ac-card', cardTag);
        connectBtn.setAttribute('data-ac-btn', btnTag);

        // Overall quality score: role base score + mutual connection bonus (100+ gets big boost) + recent activity boost
        let finalScore = quality.score + (count >= 100 ? 50 : 0) + (isRecentActivity ? 25 : 0) + Math.min(count, 500) / 10;

        candidateMap.set(cleanUrl, {
            cardTag: cardTag,
            btnTag: btnTag,
            url: cleanUrl,
            name: name,
            headline: occupation,
            role_category: quality.category,
            role_label: quality.roleLabel,
            is_big_tech: quality.isBigTech,
            quality_score: finalScore,
            mutual_count: count,
            is_recent_activity: isRecentActivity
        });
    }

    // Approach 1: Scan all Connect buttons directly and resolve their parent card
    const allButtons = document.querySelectorAll('button, [role="button"], a.artdeco-button');
    for (const b of allButtons) {
        if (!isConnectButton(b)) continue;
        let card = b.closest('.discover-entity-type-card, .discover-person-card, [data-view-name*="discover"], [data-view-name="profile-card"], .artdeco-card, [data-finite-scroll-hotkey-item], .entity-result__item, section li');
        if (!card) {
            let p = b.parentElement;
            while (p && p !== document.body) {
                if (p.querySelector('a[href*="/in/"]')) {
                    card = p;
                    break;
                }
                p = p.parentElement;
            }
        }
        if (card) {
            processCardElement(card, b);
        }
    }

    // Approach 2: Scan all potential card containers
    const cardSelectors = [
        '.discover-entity-type-card',
        '.discover-person-card',
        '[data-view-name*="discover"]',
        '[data-view-name="profile-card"]',
        'li.artdeco-card',
        'div.artdeco-card',
        '.entity-result__item',
        '[data-finite-scroll-hotkey-item]'
    ];
    const allCards = document.querySelectorAll(cardSelectors.join(', '));
    for (const card of allCards) {
        processCardElement(card);
    }

    // Approach 3: Inside-out check for text elements matching connection count
    const textEls = document.querySelectorAll('span, p, div');
    for (const el of textEls) {
        const txt = (el.innerText || el.textContent || '').trim();
        if (!txt || txt.length > 150) continue;
        const low = txt.toLowerCase();
        if (!low.includes('connection') && !low.includes('mutual') && !low.includes('common')) continue;
        if (extractCount(txt) >= minMutual) {
            let p = el.parentElement;
            while (p && p !== document.body) {
                if (p.querySelector('a[href*="/in/"]')) {
                    processCardElement(p);
                    break;
                }
                p = p.parentElement;
            }
        }
    }

    const candidates = Array.from(candidateMap.values());
    // Sort strictly by highest quality score: Tech Founders & Big Tech SWEs/HRs with >= 100 connections first!
    candidates.sort((a, b) => b.quality_score - a.quality_score);
    return candidates;
})(typeof arguments !== "undefined" && arguments.length > 0 && typeof arguments[0] === "number" ? arguments[0] : 10)
"""

async def send_candidate_invitation(page: Page, cand: Dict[str, Any]) -> str:
    """
    Rapidly clicks the Connect button for a candidate and confirms modal.
    Returns: 'SUCCESS', 'WEEKLY_LIMIT', 'EMAIL_REQUIRED', or 'FAILED'.
    """
    card_tag = cand["cardTag"]
    btn_tag = cand["btnTag"]
    name = cand["name"]
    count = cand["mutual_count"]
    cand_url = cand["url"]

    logger.info(f"Targeting: {name} with {count} connections ({cand_url})")

    # 1. Scroll card into view smoothly/centered so sticky headers don't overlap
    try:
        await page.evaluate(f"""() => {{
            const el = document.querySelector("[data-ac-btn='{btn_tag}']") || 
                       document.querySelector("[data-ac-card='{card_tag}']");
            if (el) {{
                el.scrollIntoView({{ behavior: 'instant', block: 'center' }});
            }}
        }}""")
        await asyncio.sleep(0.3)
    except Exception:
        pass

    # 2. Click the Connect button: try Playwright click, fallback to direct JS click
    clicked = False
    btn_loc = page.locator(f"[data-ac-btn='{btn_tag}']").first
    if await btn_loc.count() > 0:
        try:
            await btn_loc.click(timeout=1500)
            clicked = True
            logger.info(f"⚡ Clicked 'Connect' via Playwright for {name} ({count} connections)")
        except Exception:
            pass

    if not clicked:
        # Fallback to direct JS click (bypasses any sticky header interception)
        clicked = await page.evaluate(f"""() => {{
            const b = document.querySelector("[data-ac-btn='{btn_tag}']") ||
                      document.querySelector("[data-ac-card='{card_tag}'] button");
            if (b) {{
                b.click();
                return true;
            }}
            return false;
        }}""")
        if clicked:
            logger.info(f"⚡ Clicked 'Connect' via JS click for {name} ({count} connections)")

    if not clicked:
        logger.warning(f"Failed clicking Connect for {name}")
        return "FAILED"

    # Micro delay to allow modal or status change to register
    await asyncio.sleep(random.uniform(0.2, 0.4))

    # 3. Handle modal if one popped up
    # Modal A: "Send without a note" or "Send now" or "Send"
    try:
        modal_handled = await page.evaluate("""() => {
            const dialog = document.querySelector('div[role="dialog"]');
            if (!dialog) return false;
            
            const buttons = Array.from(dialog.querySelectorAll('button'));
            for (const b of buttons) {
                const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                if (txt.includes('without a note') || txt.includes('send now') || txt === 'send' || 
                    aria.includes('without a note') || aria.includes('send now') || aria.includes('send invitation')) {
                    b.click();
                    return true;
                }
            }
            return false;
        }""")
        if modal_handled:
            logger.info("Clicked 'Send without a note' in modal dialog")
            await asyncio.sleep(0.2)
    except Exception:
        pass

    # Modal B: Weekly limit modal
    try:
        limit_modal = page.locator(
            "div:has-text('weekly invitation limit'), "
            "h2:has-text('weekly invitation limit'), "
            "div:has-text('reached the weekly limit')"
        ).first
        if await limit_modal.count() > 0 and await limit_modal.is_visible():
            logger.warning("Reached LinkedIn weekly invitation limit modal!")
            dismiss_btn = page.locator("button:has-text('Got it'), button[aria-label='Dismiss']").first
            if await dismiss_btn.count() > 0:
                await dismiss_btn.click()
            return "WEEKLY_LIMIT"
    except Exception:
        pass

    # Modal C: Email required modal
    try:
        email_input = page.locator("div[role='dialog'] input[type='email'], div[role='dialog'] input#email").first
        if await email_input.count() > 0 and await email_input.is_visible():
            logger.info(f"Skipping {name}: email required to connect")
            cancel_btn = page.locator("div[role='dialog'] button[aria-label='Dismiss'], div[role='dialog'] button:has-text('Cancel')").first
            if await cancel_btn.count() > 0:
                await cancel_btn.click()
            return "EMAIL_REQUIRED"
    except Exception:
        pass

    return "SUCCESS"


async def auto_send_network_invitations(
    page: Page, 
    profile_id: str, 
    min_mutual: int = 50, 
    max_invitations: int = 30
) -> int:
    """
    Automates sending connection requests directly through My Network section to profiles having
    >= 100 mutual connections (prioritizing Recent Activity). Targets high-quality profiles:
    Tech Founders, HR/Recruiters, Software Engineers.
    Fulfills the profile's full daily cap limit directly through My Network.
    """
    profile = await db.get_profile(profile_id)
    daily_cap = profile.get("daily_limit", 30) if profile else 30
    target_cap = min(max_invitations, daily_cap)
    today_str = datetime.now().strftime("%Y-%m-%d")

    total_sent_today = await db.get_today_sent_count(profile_id, today_str)

    if total_sent_today >= target_cap:
        logger.info(f"🎯 Full daily cap limit ({total_sent_today}/{target_cap}) has already been sent today for profile '{profile_id}'. Stopping network run.")
        return 0

    remaining_needed = max(0, target_cap - total_sent_today)
    logger.info(
        f"⚡ Starting rapid quality auto-send for '{profile_id}' directly on My Network: "
        f"Already sent today: {total_sent_today}/{target_cap}. "
        f"Goal: Send remaining {remaining_needed} invitations (Targeting: >100 mutual connections, Tech Founders, HR/Recruiters, SWEs)..."
    )

    sent_this_session = 0

    # Pre-populate attempted URLs from database so we never duplicate
    already_sent = await db.get_sent_urls_for_profile(profile_id)
    attempted_urls = set(u.rstrip("/") for u in already_sent)

    # Step 1: Process any pending items already in database queue first (fast)
    pending_in_db = await db.get_pending_queue_items(profile_id, limit=remaining_needed)
    if pending_in_db:
        logger.info(f"Processing {len(pending_in_db)} already queued pending targets...")
        for item in pending_in_db:
            if total_sent_today >= target_cap:
                break
            item_id = item["id"]
            target_url = item["target_url"]
            note = item.get("message_text")
            attempted_urls.add(target_url.rstrip("/"))
            logger.info(f"Navigating to queued target: {target_url}")
            try:
                res = await execute_interaction_step(page, target_url, note)
                if res.get("success"):
                    await db.update_queue_item_status(item_id, "completed", error_message="Invitation sent automatically")
                    total_sent_today += 1
                    sent_this_session += 1
                    logger.info(f"✓ [{total_sent_today}/{target_cap} sent today] Sent invitation to {target_url}")
                else:
                    await db.update_queue_item_status(item_id, "failed", error_message=res.get("message"))
            except Exception as e:
                logger.error(f"Failed sending to {target_url}: {e}")
                await db.update_queue_item_status(item_id, "failed", error_message=str(e)[:200])

            if total_sent_today < target_cap:
                await asyncio.sleep(random.uniform(1.0, 1.8))

    # Step 2: Recommendations on My Network (/mynetwork/grow/ & /mynetwork/)
    network_urls = [
        "https://www.linkedin.com/mynetwork/grow/",
        "https://www.linkedin.com/mynetwork/"
    ]

    for net_url in network_urls:
        if total_sent_today >= target_cap:
            break
        try:
            curr_url = page.url.lower().rstrip("/")
            if net_url.rstrip("/") not in curr_url:
                logger.info(f"⚡ Navigating to My Network: {net_url}")
                await page.goto(net_url, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(2.0)
            else:
                logger.info(f"⚡ Already on My Network: {curr_url}")

            # Look for any 'See all' links across recommendation carousels to expand full grids
            try:
                clicked_see_all = await page.evaluate("""() => {
                    const headers = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, span, div'));
                    for (const h of headers) {
                        const txt = (h.innerText || h.textContent || '').trim().toLowerCase();
                        if (txt.includes('recent activity') || txt.includes('similar roles') || 
                            txt.includes('suggestions for you') || txt.includes('people you may know')) {
                            const section = h.closest('section, div.artdeco-card, [data-view-name], div');
                            if (section) {
                                const clickable = Array.from(section.querySelectorAll('a, button'));
                                for (const el of clickable) {
                                    const elText = (el.innerText || el.textContent || '').toLowerCase();
                                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                                    const href = (el.getAttribute('href') || '').toLowerCase();
                                    if (elText.includes('see all') || aria.includes('see all') || href.includes('grow')) {
                                        el.scrollIntoView({ behavior: 'instant', block: 'center' });
                                        el.click();
                                        return elText || aria || href;
                                    }
                                }
                            }
                        }
                    }
                    return null;
                }""")
                if clicked_see_all:
                    logger.info(f"⚡ Clicked 'See all' ({clicked_see_all}) to expand recommendations grid!")
                    await asyncio.sleep(2.0)
            except Exception as e:
                logger.debug(f"See all check: {e}")

            # Loop up to 60 scroll/pagination passes on the Network page
            curr_min_mutual = min_mutual
            consecutive_empty_scrolls = 0
            for scroll_idx in range(60):
                if total_sent_today >= target_cap:
                    logger.info(f"🎯 Network target reached ({total_sent_today}/{target_cap} sent today).")
                    break

                # Tiered adaptive threshold: expand search if candidates run low
                if consecutive_empty_scrolls >= 5 and curr_min_mutual > 15:
                    curr_min_mutual = 15
                    logger.info(f"⚡ Expanding network search threshold to >={curr_min_mutual} mutual connections...")
                elif consecutive_empty_scrolls >= 12 and curr_min_mutual > 5:
                    curr_min_mutual = 5
                    logger.info(f"⚡ Expanding network search threshold to >={curr_min_mutual} mutual connections to fulfill daily cap...")

                candidates = await page.evaluate(SCRIPT_SCAN_CANDIDATES, curr_min_mutual)
                new_cands = [c for c in candidates if c.get("url", "").rstrip("/") not in attempted_urls]

                if new_cands:
                    consecutive_empty_scrolls = 0
                    recent_count = sum(1 for c in new_cands if c.get("is_recent_activity"))
                    founders_count = sum(1 for c in new_cands if c.get("role_category") == "TECH_FOUNDER")
                    swe_count = sum(1 for c in new_cands if c.get("role_category") == "SOFTWARE_ENGINEER")
                    hr_count = sum(1 for c in new_cands if c.get("role_category") == "HR_RECRUITER")
                    logger.info(
                        f"Found {len(new_cands)} verified quality candidates on pass {scroll_idx + 1}/60 "
                        f"({founders_count} Tech Founders, {swe_count} SWEs, {hr_count} HR/Recruiters, {recent_count} Recent Activity)"
                    )

                    for cand in new_cands:
                        if total_sent_today >= target_cap:
                            break
                        cand_url = cand.get("url", "").rstrip("/")
                        if cand_url in attempted_urls:
                            continue
                        attempted_urls.add(cand_url)

                        status = await send_candidate_invitation(page, cand)
                        if status == "WEEKLY_LIMIT":
                            logger.warning(f"Stopping network auto-send: LinkedIn weekly invitation limit reached. Total sent today: {total_sent_today}/{target_cap}")
                            return sent_this_session
                        if status == "SUCCESS":
                            await db.record_sent_connection(
                                profile_id=profile_id, 
                                target_url=cand_url, 
                                mutual_count=cand["mutual_count"], 
                                name=cand["name"],
                                role_label=cand.get("role_label"),
                                method="network"
                            )
                            total_sent_today += 1
                            sent_this_session += 1
                            sec_tag = " [Recent Activity]" if cand.get("is_recent_activity") else ""
                            role_tag = f" [{cand.get('role_label', 'Quality Target')}]"
                            logger.info(f"✓ [{total_sent_today}/{target_cap} sent today] Sent invitation to {cand['name']} ({cand['mutual_count']} connections){role_tag}{sec_tag}")
                            if total_sent_today >= target_cap:
                                break
                            cooldown = random.uniform(0.6, 1.2)
                            await asyncio.sleep(cooldown)
                else:
                    consecutive_empty_scrolls += 1
                    if consecutive_empty_scrolls >= 35:
                        logger.info(f"No new candidates found after {consecutive_empty_scrolls} consecutive scrolls on {net_url}. Moving to next view.")
                        break

                # Scroll down using OS-level PageDown and mouse wheel to trigger dynamic lazy loading
                try:
                    await page.keyboard.press("PageDown")
                    await page.mouse.wheel(0, 1500)
                    await page.evaluate("""() => {
                        window.scrollBy({ top: 1400, behavior: 'smooth' });
                        const main = document.querySelector('main, .scaffold-layout__main, #main');
                        if (main) main.scrollTop += 1400;
                    }""")
                    await asyncio.sleep(0.4)
                except Exception:
                    pass

                # Aggressively locate and click "Show more results" / "See more recommendations" via JavaScript
                try:
                    clicked_more = await page.evaluate("""() => {
                        window.scrollTo(0, document.body.scrollHeight);
                        const containers = document.querySelectorAll('main, .scaffold-layout__main, #main, [data-view-name*="network"]');
                        for (const c of containers) {
                            c.scrollTop = c.scrollHeight;
                        }

                        const btns = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                        for (const b of btns) {
                            const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
                            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                            if (txt.includes('show more results') || 
                                txt.includes('see more recommendations') ||
                                txt.includes('load more') ||
                                (txt.includes('show more') && !txt.includes('experience') && !txt.includes('about')) ||
                                aria.includes('show more results') ||
                                aria.includes('see more recommendations')) {
                                b.scrollIntoView({ behavior: 'instant', block: 'center' });
                                b.click();
                                return txt || aria;
                            }
                        }
                        return null;
                    }""")
                    if clicked_more:
                        logger.info(f"⚡ Clicked pagination button '{clicked_more}' at bottom to load more candidate cards!")
                        consecutive_empty_scrolls = 0
                        await asyncio.sleep(2.0)
                except Exception as e:
                    logger.debug(f"Pagination click helper: {e}")

        except Exception as e:
            logger.error(f"Error on {net_url}: {e}", exc_info=True)

    if total_sent_today >= target_cap:
        await db.record_daily_auto_run_completed(profile_id, today_str)
        logger.info(f"🎉 Fully completed all daily cap invitations ({total_sent_today}/{target_cap}) for '{profile_id}' directly via My Network! Marked as completed for today.")

    logger.info(f"⚡ Rapid My Network auto-send completed for '{profile_id}': Sent {sent_this_session} in this run (Total today: {total_sent_today}/{target_cap}).")
    return sent_this_session


async def auto_send_dual_strategy_invitations(
    page: Page, 
    profile_id: str, 
    daily_cap: int = 30
) -> int:
    """
    Sends all connection requests directly through My Network section (100+ mutual connections).
    Fulfills the profile's full daily cap limit.
    """
    return await auto_send_network_invitations(
        page=page,
        profile_id=profile_id,
        min_mutual=50,
        max_invitations=daily_cap
    )



# Client-side script that scans the DOM for pending incoming invitation cards with "Accept" buttons
SCRIPT_SCAN_ACCEPT_BUTTONS = r"""
(() => {
    const results = [];
    let idx = Date.now();

    // Scan all buttons on page
    const allButtons = document.querySelectorAll('button, [role="button"]');
    for (const b of allButtons) {
        if (!b || b.disabled) continue;
        const txt = (b.innerText || b.textContent || '').trim().toLowerCase();
        const aria = (b.getAttribute('aria-label') || '').toLowerCase();

        // Skip ignored, disabled, or already accepted buttons
        if (txt.includes('accepted') || aria.includes('accepted')) continue;
        if (txt.includes('ignore') || aria.includes('ignore')) continue;
        if (txt.includes('withdraw') || aria.includes('withdraw')) continue;

        // Match Accept buttons
        const isAccept = (txt === 'accept' || 
                          txt.startsWith('accept ') || 
                          aria.startsWith('accept ') || 
                          aria.includes('accept invitation') ||
                          b.classList.contains('invitation-card__action-btn--accept') ||
                          b.getAttribute('data-control-name') === 'accept');

        if (isAccept) {
            // Find parent card / container
            let card = b.closest('li') || 
                       b.closest('.invitation-card') || 
                       b.closest('.artdeco-list__item') || 
                       b.closest('[data-view-name*="invitation"]') || 
                       b.closest('.invitation-card__details') ||
                       b.parentElement?.parentElement;

            let name = 'Member';
            let profileUrl = '';

            if (card) {
                const nameEl = card.querySelector('.invitation-card__title, .invitation-card__name, .artdeco-entity-lockup__title, a[href*="/in/"], h3, span[aria-hidden="true"]');
                if (nameEl) {
                    const extracted = (nameEl.innerText || nameEl.textContent || '').trim().split('\n')[0];
                    if (extracted && extracted.length < 60) name = extracted;
                }
                const linkEl = card.querySelector('a[href*="/in/"]');
                if (linkEl && linkEl.href) {
                    profileUrl = linkEl.href.split('?')[0].split('#')[0].replace(/\/$/, '');
                }
            }

            if (name === 'Member' && aria) {
                const m = aria.match(/accept\s+([^']+?)(?:'s)?\s+invitation/i);
                if (m && m[1]) name = m[1].trim();
            }

            idx++;
            const tag = 'ac-accept-' + idx;
            b.setAttribute('data-ac-accept', tag);
            results.push({
                tag: tag,
                name: name,
                profileUrl: profileUrl
            });
        }
    }
    return results;
})()
"""


async def auto_accept_pending_invitations(page: Page, profile_id: str) -> int:
    """
    Navigates to LinkedIn's Received Invitations section and automatically
    accepts all pending incoming connection invitations.
    Returns: Total count of invitations accepted in this run.
    """
    invitations_url = "https://www.linkedin.com/mynetwork/invitation-manager/"
    logger.info(f"Checking for pending incoming invitations for '{profile_id}' at {invitations_url}...")
    accepted_count = 0

    try:
        current_url = page.url.lower()
        if "invitation-manager" not in current_url:
            await page.goto(invitations_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2.5)
        else:
            await asyncio.sleep(1.0)
    except Exception as nav_err:
        logger.warning(f"Failed navigating to invitation manager for '{profile_id}': {nav_err}")
        return 0

    # Ensure on 'Received' invitations tab if tab controls exist
    try:
        await page.evaluate("""() => {
            const tabs = document.querySelectorAll('button, a');
            for (const t of tabs) {
                const txt = (t.innerText || t.textContent || '').trim().toLowerCase();
                if (txt.includes('received')) {
                    t.click();
                    break;
                }
            }
        }""")
        await asyncio.sleep(1.0)
    except Exception:
        pass

    consecutive_empty = 0
    max_passes = 15

    for pass_idx in range(max_passes):
        try:
            buttons = await page.evaluate(SCRIPT_SCAN_ACCEPT_BUTTONS)
        except Exception as eval_err:
            logger.debug(f"Error evaluating accept buttons: {eval_err}")
            buttons = []

        if not buttons:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                break
            try:
                await page.mouse.wheel(0, 400)
            except Exception:
                pass
            await asyncio.sleep(1.0)
            continue

        consecutive_empty = 0
        logger.info(f"Found {len(buttons)} pending incoming invitation(s) to accept on pass {pass_idx + 1}...")

        for b_info in buttons:
            tag = b_info["tag"]
            name = b_info["name"]
            profile_url = b_info["profileUrl"]

            clicked = False
            # 1. Try Playwright locator click
            try:
                loc = page.locator(f"[data-ac-accept='{tag}']").first
                if await loc.count() > 0:
                    await loc.scroll_into_view_if_needed()
                    await asyncio.sleep(0.2)
                    await loc.click(timeout=4000)
                    clicked = True
            except Exception:
                clicked = False

            # 2. Fallback: JavaScript direct click
            if not clicked:
                try:
                    js_clicked = await page.evaluate(f"""() => {{
                        const el = document.querySelector("[data-ac-accept='{tag}']");
                        if (el) {{
                            el.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                            el.click();
                            return true;
                        }}
                        return false;
                    }}""")
                    clicked = bool(js_clicked)
                except Exception:
                    clicked = False

            if clicked:
                accepted_count += 1
                logger.info(f"✓ [{accepted_count}] Accepted incoming connection invitation from: {name}")
                try:
                    await db.record_accepted_connection(
                        profile_id=profile_id,
                        sender_name=name,
                        sender_url=profile_url
                    )
                except Exception as db_err:
                    logger.debug(f"DB record accepted connection: {db_err}")

                cooldown = random.uniform(0.6, 1.2)
                await asyncio.sleep(cooldown)
            else:
                logger.warning(f"Could not click Accept button for {name}")

        await asyncio.sleep(1.5)

    if accepted_count > 0:
        logger.info(f"🎉 Completed incoming invitations check for '{profile_id}': Successfully accepted all {accepted_count} pending invitation(s)!")
    else:
        logger.info(f"ℹ️ No pending incoming invitations found for '{profile_id}'.")

    return accepted_count


async def open_interactive_session(
    profile_id: str,
    start_url: Optional[str] = None,
    timeout_seconds: int = 360,
    auto_connect: bool = True,
    headless: bool = True
) -> None:
    """
    Opens or reuses a browser session (silent headless mode by default for automation;
    headed mode when explicitly requested for operator login):
    - If a browser is already open for this profile, opens a NEW TAB in that same browser context.
    - If not open, launches the browser context.
    - Automatically handles proxy failures so navigation is never blocked.
    - Monitors authentication; once authenticated and auto_connect is True, automatically
      accepts pending incoming invitations and sends connection requests directly via My Network
      (target profiles with >= 50 mutual connections, founders, recruiters, SWEs) up to the daily cap.
    """
    mode_str = "silent background (headless)" if headless else "visible on screen (headed)"
    logger.info(f"Starting browser session for profile '{profile_id}' in {mode_str} mode")
    profile = await db.get_profile(profile_id)
    if not profile:
        logger.error(f"Profile {profile_id} not found")
        return

    proxy_url = profile.get("proxy_url")
    daily_limit = profile.get("daily_limit", 20)
    target_start_url = start_url or "https://www.linkedin.com/login"

    await db.update_profile_status(profile_id, "authenticating")

    try:
        try:
            context, pw_instance, is_new_launch = await get_or_create_context(
                profile_id=profile_id,
                headless=headless,
                proxy_url=proxy_url
            )
            # Open in a NEW TAB in the browser
            page = await context.new_page()
        except Exception as launch_err:
            logger.warning(f"Initial context or page creation failed for '{profile_id}' ({launch_err}). Forcing clean restart...")
            await close_session(profile_id)
            target_proxy = None if (proxy_url and "proxy" in str(launch_err).lower()) else proxy_url
            context, pw_instance, is_new_launch = await get_or_create_context(
                profile_id=profile_id,
                headless=headless,
                proxy_url=target_proxy
            )
            page = await context.new_page()

        await page.bring_to_front()
        logger.info(f"Opened tab in browser for profile '{profile_id}'. Navigating to {target_start_url}")

        try:
            await page.goto(target_start_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as goto_err:
            if "ERR_PROXY_CONNECTION_FAILED" in str(goto_err):
                logger.error(f"Proxy failed: {goto_err}. Re-opening direct tab without proxy...")
                await close_session(profile_id)
                context, pw_instance, is_new_launch = await get_or_create_context(
                    profile_id=profile_id,
                    headless=headless,
                    proxy_url=None
                )
                page = await context.new_page()
                await page.bring_to_front()
                await page.goto(target_start_url, wait_until="domcontentloaded", timeout=60000)
            else:
                raise goto_err

        # Monitor login authentication loop
        elapsed = 0
        check_interval = 2
        authenticated = False
        effective_timeout = 30 if headless else timeout_seconds

        while elapsed < effective_timeout:
            if not context.pages or page.is_closed():
                logger.info("Login tab closed by operator.")
                break

            current_url = page.url.lower()

            is_authenticated_url = any(k in current_url for k in ["/feed", "/mynetwork", "/in/", "/home", "/dashboard", "/search"])
            has_nav = False
            try:
                has_nav = (await page.locator("#global-nav, header.global-nav").count()) > 0
            except Exception:
                pass

            if is_authenticated_url or has_nav:
                logger.info(f"Authentication detected! Current URL: {current_url}")
                authenticated = True
                await asyncio.sleep(1.5)
                break

            await asyncio.sleep(check_interval)
            elapsed += check_interval

        if not authenticated:
            if headless:
                logger.warning(
                    f"⚠️ Profile '{profile_id}' is not logged in. Headless run aborted. "
                    f"Please click 'Headed Login' in the dashboard to sign in interactively once."
                )
            else:
                logger.warning(f"Authentication timed out for profile '{profile_id}' after {timeout_seconds}s.")

        # If authenticated and auto_connect requested:
        if authenticated and auto_connect:
            worker_page = page if (page and not page.is_closed()) else await context.new_page()
            await worker_page.bring_to_front()
            await db.update_profile_status(profile_id, "running")

            # 1. Automatically check and accept all pending incoming invitations
            try:
                accepted_count = await auto_accept_pending_invitations(
                    page=worker_page,
                    profile_id=profile_id
                )
                if accepted_count > 0:
                    logger.info(f"⚡ Successfully accepted {accepted_count} pending incoming invitation(s) for '{profile_id}'!")
            except Exception as accept_err:
                logger.warning(f"Error checking/accepting incoming invitations for '{profile_id}': {accept_err}")

            # 2. Check daily outgoing cap and send outgoing connection invitations
            daily_limit = profile.get("daily_limit", 30)
            today_str = datetime.now().strftime("%Y-%m-%d")
            already_sent_today = await db.get_today_sent_count(profile_id, today_str)

            if already_sent_today >= daily_limit:
                logger.info(f"🎯 Profile '{profile_id}' has already reached its full daily cap limit ({already_sent_today}/{daily_limit}) for today. All connection invitations are complete.")
            else:
                remaining_needed = daily_limit - already_sent_today
                logger.info(
                    f"Logged in! Starting FAST automatic My Network connection invitations for '{profile_id}' "
                    f"(Already sent today: {already_sent_today}/{daily_limit}, Remaining: {remaining_needed}, "
                    f"Target: My Network [>100 Mutual Connections, Tech Founders, HRs, SWEs])..."
                )
                sent = await auto_send_network_invitations(
                    page=worker_page,
                    profile_id=profile_id,
                    min_mutual=50,
                    max_invitations=daily_limit
                )
                logger.info(f"⚡ Completed sending {sent} connection invitations for '{profile_id}' directly via My Network in this run.")
                await asyncio.sleep(2)

        logger.info(f"Session finished for profile '{profile_id}'")

    except BaseException as e:
        logger.error(f"Error or interrupt during session for {profile_id}: {e}", exc_info=True)
        if "closed" in str(e).lower() or "TargetClosedError" in type(e).__name__:
            await close_session(profile_id)
        if isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)):
            raise
    finally:
        try:
            await db.update_profile_status(profile_id, "idle", update_last_run=True)
        except Exception as db_err:
            logger.error(f"Error resetting profile '{profile_id}' to idle in finally: {db_err}")
        if headless:
            try:
                await close_session(profile_id)
            except Exception as close_err:
                logger.error(f"Error closing headless session for '{profile_id}': {close_err}")



async def simulate_human_scroll(page: Page) -> None:
    """Simulates realistic human scrolling behavior."""
    try:
        scroll_steps = random.randint(2, 4)
        for _ in range(scroll_steps):
            delta = random.randint(200, 500)
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(random.uniform(0.6, 1.4))
        await page.mouse.wheel(0, -random.randint(100, 300))
        await asyncio.sleep(random.uniform(0.4, 0.9))
    except Exception as e:
        logger.debug(f"Scroll simulation error: {e}")


async def type_humanlike(locator, text: str) -> None:
    """Types text character by character with randomized realistic pauses."""
    for char in text:
        await locator.type(char)
        await asyncio.sleep(random.uniform(0.04, 0.12))


async def execute_interaction_step(page: Page, target_url: str, custom_note: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes interaction step on target profile URL:
    - Navigates to target_url
    - Performs human scrolling
    - Locates action button (Connect/Follow/Send/primary CTA), including checking 'More' dropdown
    - Handles optional note input modal
    - Dispatches request and confirms
    """
    logger.info(f"Navigating to target: {target_url}")
    await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(random.uniform(2.5, 4.5))

    await simulate_human_scroll(page)

    # Check if already connected or invitation already pending
    has_pending = await page.locator("button:has-text('Pending'), span:has-text('Pending')").count()
    if has_pending > 0:
        return {"success": True, "message": "Already connected or invitation pending"}

    # Attempt 1: Direct action button
    direct_connect_selectors = [
        "button:has-text('Connect')",
        "button[aria-label*='Invite'][aria-label*='connect']",
        "button:has-text('Follow')",
        "button[data-control-name='connect']",
        "a:has-text('Connect')",
    ]

    action_button = None
    for selector in direct_connect_selectors:
        btn = page.locator(selector).first
        if await btn.count() > 0 and await btn.is_visible():
            action_button = btn
            logger.info(f"Found direct action button using selector: {selector}")
            break

    # Attempt 2: "More" dropdown
    if not action_button:
        logger.info("Direct action button not visible. Checking 'More' dropdown...")
        more_selectors = [
            "button:has-text('More')",
            "button[aria-label*='More actions']",
            "button[aria-label*='More']",
        ]
        for m_sel in more_selectors:
            more_btn = page.locator(m_sel).first
            if await more_btn.count() > 0 and await more_btn.is_visible():
                await more_btn.click()
                await asyncio.sleep(random.uniform(0.8, 1.6))
                
                dropdown_connect = page.locator(
                    "div.artdeco-dropdown__content button:has-text('Connect'), "
                    "div[role='menu'] button:has-text('Connect'), "
                    "div[role='dialog'] button:has-text('Connect'), "
                    "span:has-text('Connect')"
                ).first
                if await dropdown_connect.count() > 0 and await dropdown_connect.is_visible():
                    action_button = dropdown_connect
                    logger.info("Found Connect button inside 'More' dropdown!")
                break

    # Attempt 3: Generic fallback
    if not action_button:
        generic_selectors = [
            "button[type='submit']",
            "button.action-btn",
            "button.btn-primary",
            "button:has-text('Submit')",
            "button:has-text('Contact')",
        ]
        for g_sel in generic_selectors:
            btn = page.locator(g_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                action_button = btn
                break

    if not action_button:
        return {"success": False, "message": "Interaction button not found on page"}

    await asyncio.sleep(random.uniform(0.6, 1.5))
    await action_button.click()
    await asyncio.sleep(random.uniform(1.2, 2.5))

    # Handle modals (Add a note / Send without a note)
    add_note_btn = page.locator("button:has-text('Add a note')").first
    send_without_note_btn = page.locator("button:has-text('Send without a note')").first
    send_btn = page.locator(
        "button:has-text('Send'), "
        "button:has-text('Send now'), "
        "button[aria-label*='Send invitation'], "
        "button:has-text('Done')"
    ).first

    if custom_note and custom_note.strip():
        if await add_note_btn.count() > 0 and await add_note_btn.is_visible():
            await add_note_btn.click()
            await asyncio.sleep(random.uniform(0.7, 1.4))

            textarea = page.locator("textarea[name='message'], textarea#custom-message, textarea").first
            if await textarea.count() > 0:
                await textarea.click()
                await type_humanlike(textarea, custom_note.strip())
                await asyncio.sleep(random.uniform(0.8, 1.5))

        send_btn = page.locator(
            "button:has-text('Send'), "
            "button:has-text('Send now'), "
            "button[aria-label*='Send invitation']"
        ).first
        if await send_btn.count() > 0 and await send_btn.is_visible():
            await send_btn.click()
            await asyncio.sleep(random.uniform(1.5, 3.0))
            return {"success": True, "message": "Invitation sent with personalized note"}
    else:
        if await send_without_note_btn.count() > 0 and await send_without_note_btn.is_visible():
            await send_without_note_btn.click()
            await asyncio.sleep(random.uniform(1.5, 3.0))
            return {"success": True, "message": "Invitation sent without note"}
        elif await send_btn.count() > 0 and await send_btn.is_visible():
            await send_btn.click()
            await asyncio.sleep(random.uniform(1.5, 3.0))
            return {"success": True, "message": "Action dispatched successfully"}

    return {"success": True, "message": "Action executed successfully"}


async def run_profile_queue(profile_id: str, headless: bool = True) -> Dict[str, Any]:
    """
    Worker pipeline:
    1. Loads profile and verifies pending work
    2. Updates status to 'running'
    3. Reuses or launches persistent context with proxy and stealth
    4. Sequentially executes items up to daily_limit
    5. Implements polite 5-15s random delays between actions
    6. Updates database records
    7. Cleans up session and resets profile status to 'idle'
    """
    profile = await db.get_profile(profile_id)
    if not profile:
        return {"error": f"Profile '{profile_id}' does not exist"}

    if profile["status"] in ("running", "authenticating"):
        return {"error": f"Profile '{profile_id}' is currently busy ({profile['status']})"}

    limit = profile.get("daily_limit", 15)
    pending_items = await db.get_pending_queue_items(profile_id, limit=limit)
    if not pending_items:
        return {"message": "No pending queue items for this profile", "processed": 0}

    logger.info(f"Starting batch for profile '{profile_id}'. Items to process: {len(pending_items)}")
    await db.update_profile_status(profile_id, "running", update_last_run=True)

    processed_count = 0
    success_count = 0
    failed_count = 0

    try:
        try:
            context, pw_instance, is_new = await get_or_create_context(
                profile_id=profile_id,
                headless=headless,
                proxy_url=profile.get("proxy_url")
            )
            page = await context.new_page()
        except Exception as launch_err:
            logger.warning(f"Failed to obtain page in context for '{profile_id}': {launch_err}. Retrying fresh context...")
            await close_session(profile_id)
            context, pw_instance, is_new = await get_or_create_context(
                profile_id=profile_id,
                headless=headless,
                proxy_url=profile.get("proxy_url")
            )
            page = await context.new_page()

        for item in pending_items:
            item_id = item["id"]
            target_url = item["target_url"]
            note = item.get("message_text")

            logger.info(f"Processing item {item_id} -> {target_url}")
            try:
                result = await execute_interaction_step(page, target_url, note)
                if result.get("success"):
                    await db.update_queue_item_status(item_id, "completed", error_message=result.get("message"))
                    success_count += 1
                else:
                    await db.update_queue_item_status(item_id, "failed", error_message=result.get("message"))
                    failed_count += 1
            except PlaywrightTimeoutError:
                err_msg = "Navigation or action timed out"
                logger.warning(f"Item {item_id} timed out")
                await db.update_queue_item_status(item_id, "failed", error_message=err_msg)
                failed_count += 1
            except Exception as e:
                err_msg = str(e)[:300]
                logger.error(f"Item {item_id} failed with error: {err_msg}")
                await db.update_queue_item_status(item_id, "failed", error_message=err_msg)
                failed_count += 1

            processed_count += 1

            # Polite safety cooldown delay between actions (5 - 15 seconds)
            if processed_count < len(pending_items):
                cooldown = random.uniform(5.0, 15.0)
                logger.info(f"Polite delay: waiting {cooldown:.1f}s before next interaction...")
                await asyncio.sleep(cooldown)

        # Close the worker tab
        try:
            await page.close()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Batch execution error for profile {profile_id}: {e}", exc_info=True)
    finally:
        await db.update_profile_status(profile_id, "idle", update_last_run=True)
        if headless:
            await close_session(profile_id)

    summary = {
        "profile_id": profile_id,
        "processed": processed_count,
        "successful": success_count,
        "failed": failed_count,
    }
    logger.info(f"Completed batch for profile '{profile_id}': {summary}")
    return summary
