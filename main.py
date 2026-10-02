"""
FastAPI Application for Multi-Profile Web Automation Management System.
Exposes REST and Web UI endpoints for profile lifecycle, queue ingestion,
interactive authentication sessions, and background automated worker batches.
"""

import os
import csv
import io
import logging
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, UploadFile, File, BackgroundTasks, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database as db
import browser_engine as engine
import scheduler

logger = logging.getLogger("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database and tables
    await db.init_db()
    logger.info("Database initialized successfully.")
    # Reset any stale running/authenticating status left over from unexpected process reload
    recovered = await db.reset_stale_profile_statuses()
    if recovered > 0:
        logger.info(f"Cleanly reset {recovered} profile(s) from running/authenticating back to idle.")
    # Start background daily automated scheduler
    scheduler.start_scheduler()
    yield
    # Shutdown: cleanly stop scheduler
    scheduler.stop_scheduler()
    logger.info("Application shutting down.")


app = FastAPI(
    title="Multi-Profile Web Automation System",
    description="Enterprise Multi-Tenant Profile Orchestration & Automated Interaction System",
    version="1.0.0",
    lifespan=lifespan
)


templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ==========================================
# Web UI Dashboard Route
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def dashboard_view(
    request: Request,
    profile_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None)
):
    """Renders the single-page management dashboard."""
    profiles = await db.get_all_profiles()
    stats = await db.get_dashboard_stats()
    queue_items = await db.get_queue_items(profile_id=profile_id, status=status, limit=150)
    scheduler_status = await scheduler.get_scheduler_status()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "profiles": profiles,
            "queue_items": queue_items,
            "stats": stats,
            "scheduler_status": scheduler_status,
            "selected_profile_id": profile_id or "",
            "selected_status": status or "",
        }
    )


# ==========================================
# REST API Endpoints for Dynamic Polling
# ==========================================
@app.get("/api/scheduler/status")
async def api_scheduler_status():
    """Returns real-time status of the daily auto-pilot scheduler."""
    return await scheduler.get_scheduler_status()

@app.get("/api/stats")
async def api_stats():
    """Returns real-time aggregated stats."""
    stats = await db.get_dashboard_stats()
    return stats


@app.get("/api/profiles")
async def api_profiles():
    """Returns full profiles list with live statuses."""
    profiles = await db.get_all_profiles()
    return {"profiles": profiles}


@app.get("/api/queue")
async def api_queue(
    profile_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = 100
):
    """Returns queue items filtered by profile or status."""
    items = await db.get_queue_items(profile_id=profile_id, status=status, limit=limit)
    return {"items": items}


# ==========================================
# Profile Management Routes
# ==========================================
@app.post("/profiles/add")
async def add_profile(
    profile_id: str = Form(...),
    label: str = Form(...),
    proxy_url: Optional[str] = Form(None),
    daily_limit: int = Form(30)
):
    """Creates a new profile configuration."""
    clean_id = profile_id.strip().lower().replace(" ", "_")
    if not clean_id:
        raise HTTPException(status_code=400, detail="Profile ID is required")

    existing = await db.get_profile(clean_id)
    if existing:
        return RedirectResponse(url="/?error=Profile+ID+already+exists", status_code=303)

    await db.create_profile(
        profile_id=clean_id,
        label=label.strip(),
        proxy_url=proxy_url.strip() if proxy_url and proxy_url.strip() else None,
        daily_limit=max(1, daily_limit)
    )
    return RedirectResponse(url="/?success=Profile+created+successfully", status_code=303)


@app.post("/profiles/update")
async def update_profile_endpoint(
    profile_id: str = Form(...),
    label: str = Form(...),
    proxy_url: Optional[str] = Form(None),
    daily_limit: int = Form(30),
    auto_pilot: int = Form(1),
    schedule_hour: int = Form(9)
):
    """Updates an existing profile's configuration."""
    clean_id = profile_id.strip()
    profile = await db.get_profile(clean_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    await db.update_profile(
        profile_id=clean_id,
        label=label.strip(),
        proxy_url=proxy_url.strip() if proxy_url and proxy_url.strip() else None,
        daily_limit=max(1, daily_limit),
        auto_pilot=auto_pilot,
        schedule_hour=max(0, min(23, schedule_hour))
    )
    return RedirectResponse(url=f"/?success=Profile+{clean_id}+updated+successfully", status_code=303)



@app.get("/api/profile/{target_profile_id}")
async def api_get_profile(target_profile_id: str):
    """Returns single profile details as JSON."""
    profile = await db.get_profile(target_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"profile": profile}


@app.post("/profiles/delete/{target_profile_id}")
async def delete_profile(target_profile_id: str):
    """Removes a profile and all its queue entries."""
    await db.delete_profile(target_profile_id)
    return RedirectResponse(url="/?success=Profile+deleted", status_code=303)


# ==========================================
# Automation & Authentication Triggers
# ==========================================
@app.post("/profiles/auth/{target_profile_id}")
async def trigger_auth_session(
    target_profile_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    start_url: Optional[str] = Form(None)
):
    """
    Launches or focuses a visible browser session in a new tab:
    - Automatically opens in a new tab in the active browser window.
    - Monitors login and automatically queues profiles with >100 mutual connections.
    """
    profile = await db.get_profile(target_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile["status"] != "idle":
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"success": False, "message": f"Profile is currently {profile['status']}"}, status_code=400)
        return RedirectResponse(
            url=f"/?error=Profile+{target_profile_id}+is+currently+{profile['status']}", 
            status_code=303
        )

    background_tasks.add_task(
        engine.open_interactive_session,
        profile_id=target_profile_id,
        start_url=start_url.strip() if start_url and start_url.strip() else None,
        timeout_seconds=360
    )

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"success": True, "message": f"Opening new tab for {target_profile_id}..."})

    return RedirectResponse(
        url=f"/?success=Browser+session+opened+in+new+tab+for+{target_profile_id}.+Log+in+to+auto-queue+profiles+with+>100+mutual+connections!", 
        status_code=303
    )


@app.post("/profiles/auto-send/{target_profile_id}")
@app.post("/profiles/discover/{target_profile_id}")
async def trigger_auto_send_session(
    target_profile_id: str,
    background_tasks: BackgroundTasks
):
    """
    Triggers the 50/50 dual-strategy connection session:
    - 50% via LinkedIn Search (High-Profile Tech Leaders, Tech Insiders, Tech HRs)
    - 50% via My Network (100+ Mutual Connections)
    """
    profile = await db.get_profile(target_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile["status"] != "idle":
        return RedirectResponse(
            url=f"/?error=Profile+{target_profile_id}+is+currently+{profile['status']}", 
            status_code=303
        )

    background_tasks.add_task(
        engine.open_interactive_session,
        profile_id=target_profile_id,
        start_url="https://www.linkedin.com/mynetwork/grow/",
        timeout_seconds=360,
        auto_connect=True
    )

    return RedirectResponse(
        url=f"/?success=Auto-sending+50/50+connection+invitations+(Search+&+Network)+for+{target_profile_id}...", 
        status_code=303
    )


@app.post("/profiles/recycle/{target_profile_id}")
async def trigger_recycle_stale_invitations(
    target_profile_id: str,
    background_tasks: BackgroundTasks
):
    """
    Directly navigates to LinkedIn 'Sent Invitations' section, locates invitations sent
    more than 1 week ago, withdraws them, and resends them (outside of the 30 daily cap).
    """
    profile = await db.get_profile(target_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile["status"] != "idle":
        return RedirectResponse(
            url=f"/?error=Profile+{target_profile_id}+is+currently+{profile['status']}", 
            status_code=303
        )

    async def run_recycle_worker():
        await db.update_profile_status(target_profile_id, "running")
        try:
            context, pw_instance, is_new = await engine.get_or_create_context(
                profile_id=target_profile_id,
                headless=False,
                proxy_url=profile.get("proxy_url")
            )
            page = await context.new_page()
            await page.bring_to_front()
            await engine.withdraw_and_resend_stale_invitations(page, target_profile_id, max_recycle=25)
            try:
                await page.close()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error in recycle worker for {target_profile_id}: {e}", exc_info=True)
        finally:
            await db.update_profile_status(target_profile_id, "idle", update_last_run=True)

    background_tasks.add_task(run_recycle_worker)

    return RedirectResponse(
        url=f"/?success=Checking+and+recycling+stale+invitations+(>1+week+old)+for+{target_profile_id}...", 
        status_code=303
    )


# ==========================================
# Daily Auto-Pilot Scheduler Controls
# ==========================================
@app.post("/scheduler/toggle/{target_profile_id}")
async def toggle_auto_pilot(target_profile_id: str):
    """Toggles daily auto-pilot on/off for a profile without needing user permission."""
    profile = await db.get_profile(target_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    new_val = 0 if profile.get("auto_pilot", 1) else 1
    await db.update_profile_auto_pilot(target_profile_id, new_val)
    status_label = "ENABLED" if new_val else "PAUSED"
    return RedirectResponse(
        url=f"/?success=Daily+Auto-Pilot+{status_label}+for+{target_profile_id}.+Runs+automatically+daily!",
        status_code=303
    )


@app.post("/scheduler/settings/{target_profile_id}")
async def update_scheduler_settings(
    target_profile_id: str,
    schedule_hour: int = Form(9),
    auto_pilot: bool = Form(True)
):
    """Updates scheduled daily run hour (0-23) and auto-pilot state."""
    profile = await db.get_profile(target_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    clamped_hour = max(0, min(23, schedule_hour))
    await db.update_profile_auto_pilot(target_profile_id, 1 if auto_pilot else 0, clamped_hour)
    return RedirectResponse(
        url=f"/?success=Daily+Auto-Pilot+scheduled+for+{clamped_hour:02d}:00+daily+for+{target_profile_id}",
        status_code=303
    )


@app.post("/scheduler/trigger/{target_profile_id}")
async def trigger_immediate_daily_run(
    target_profile_id: str,
    background_tasks: BackgroundTasks
):
    """Triggers today's automated daily run immediately right now."""
    profile = await db.get_profile(target_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile["status"] != "idle":
        return RedirectResponse(
            url=f"/?error=Profile+{target_profile_id}+is+currently+{profile['status']}",
            status_code=303
        )

    background_tasks.add_task(
        scheduler.run_profile_daily_automation,
        profile_id=target_profile_id,
        force=True
    )

    return RedirectResponse(
        url=f"/?success=Immediate+daily+auto-pilot+run+triggered+for+{target_profile_id}...",
        status_code=303
    )



@app.post("/profiles/run/{target_profile_id}")
async def trigger_profile_run(
    target_profile_id: str,
    background_tasks: BackgroundTasks,
    headless: bool = Form(False)
):
    """
    Launches worker batch processing for pending queue items
    assigned to target profile up to its daily_limit.
    """
    profile = await db.get_profile(target_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile["status"] != "idle":
        return RedirectResponse(
            url=f"/?error=Profile+{target_profile_id}+is+currently+{profile['status']}", 
            status_code=303
        )

    pending = await db.get_pending_queue_items(target_profile_id, limit=1)
    if not pending:
        return RedirectResponse(
            url=f"/?error=No+pending+items+in+queue+for+{target_profile_id}", 
            status_code=303
        )

    background_tasks.add_task(
        engine.run_profile_queue,
        profile_id=target_profile_id,
        headless=headless
    )

    return RedirectResponse(
        url=f"/?success=Batch+execution+started+for+{target_profile_id}", 
        status_code=303
    )


# ==========================================
# Queue Management & CSV Ingestion
# ==========================================
@app.post("/queue/add")
async def add_single_item(
    profile_id: str = Form(...),
    target_url: str = Form(...),
    message_text: Optional[str] = Form(None)
):
    """Quick-adds a single target URL to the profile queue."""
    clean_url = target_url.strip()
    if not clean_url:
        return RedirectResponse(url="/?error=Target+URL+cannot+be+empty", status_code=303)

    await db.add_queue_item(
        profile_id=profile_id,
        target_url=clean_url,
        message_text=message_text.strip() if message_text and message_text.strip() else None
    )
    return RedirectResponse(url=f"/?profile_id={profile_id}&success=Target+queued+successfully", status_code=303)


@app.post("/queue/upload-csv")
async def upload_csv(
    profile_id: str = Form(...),
    file: UploadFile = File(...)
):
    """
    Ingests CSV file to populate the target queue.
    Supports CSV headers: target_url, profile_url, url, message_text, note, custom_note.
    Or fallback to column 0 (url) and column 1 (note).
    """
    if not file.filename.endswith((".csv", ".txt")):
        return RedirectResponse(url="/?error=File+must+be+a+CSV+format", status_code=303)

    content = await file.read()
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = content.decode("latin-1")

    reader = csv.reader(io.StringIO(decoded))
    rows = list(reader)

    if not rows:
        return RedirectResponse(url="/?error=Uploaded+CSV+file+is+empty", status_code=303)

    items_to_insert = []
    first_row = [c.strip().lower() for c in rows[0]]

    # Detect header columns
    url_idx = -1
    msg_idx = -1

    for idx, col in enumerate(first_row):
        if col in ("target_url", "profile_url", "url", "link", "target"):
            url_idx = idx
        elif col in ("message_text", "note", "custom_note", "message", "text"):
            msg_idx = idx

    start_row = 1 if url_idx != -1 else 0
    if url_idx == -1:
        url_idx = 0
        msg_idx = 1 if len(first_row) > 1 else -1

    for row in rows[start_row:]:
        if not row or len(row) <= url_idx:
            continue
        url_val = row[url_idx].strip()
        if not url_val or not url_val.startswith(("http://", "https://")):
            continue

        msg_val = row[msg_idx].strip() if msg_idx != -1 and len(row) > msg_idx and row[msg_idx].strip() else None
        items_to_insert.append((profile_id, url_val, msg_val))

    if not items_to_insert:
        return RedirectResponse(url="/?error=No+valid+target+URLs+found+in+CSV", status_code=303)

    count = await db.add_queue_items_bulk(items_to_insert)
    return RedirectResponse(
        url=f"/?profile_id={profile_id}&success=Successfully+imported+{count}+targets+into+queue",
        status_code=303
    )


@app.post("/queue/delete/{item_id}")
async def delete_queue_item(item_id: int):
    """Deletes an individual queue item."""
    await db.delete_queue_item(item_id)
    return RedirectResponse(url="/?success=Item+removed+from+queue", status_code=303)


@app.post("/queue/retry-failed")
async def retry_failed_items(profile_id: Optional[str] = Form(None)):
    """Resets failed items back to 'pending' state."""
    clean_id = profile_id.strip() if profile_id and profile_id.strip() else None
    count = await db.reset_failed_items(clean_id)
    return RedirectResponse(url=f"/?success=Reset+{count}+failed+items+back+to+pending", status_code=303)


@app.post("/queue/clear")
async def clear_queue_items(
    profile_id: Optional[str] = Form(None),
    status: Optional[str] = Form(None)
):
    """Clears items matching filters from the queue."""
    clean_id = profile_id.strip() if profile_id and profile_id.strip() else None
    clean_status = status.strip() if status and status.strip() else None
    count = await db.clear_queue(clean_id, clean_status)
    return RedirectResponse(url=f"/?success=Cleared+{count}+items+from+queue", status_code=303)


@app.get("/download-sample-csv")
async def download_sample_csv():
    """Provides a sample CSV template for operators."""
    content = "target_url,custom_note\n" \
              "https://www.linkedin.com/in/williamhgates,Hi Bill! Loved your recent keynote on AI advancements.\n" \
              "https://www.linkedin.com/in/satyanadella,Hello Satya! Excited about Microsoft's new cloud innovations.\n" \
              "https://www.linkedin.com/in/sundarpichai,\n"
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=automation_targets_sample.csv"}
    )
