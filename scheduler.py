"""
Background Auto-Scheduler for Daily Automated LinkedIn Connection Dispatches.
Runs continuously in the FastAPI event loop, checking every 60 seconds if any
profile is due for its daily auto-pilot run without requiring manual operator intervention.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

import database as db
import browser_engine as engine

logger = logging.getLogger("scheduler")

SCHEDULER_RUNNING = False
SCHEDULER_TASK: Optional[asyncio.Task] = None
SCHEDULER_CHECK_INTERVAL_SECONDS = 60
LAST_CHECK_TIME: Optional[str] = None


async def run_profile_daily_automation(profile_id: str, force: bool = False) -> Dict[str, Any]:
    """
    Executes the automated daily connection run for a single profile:
    - Navigates to My Network (/mynetwork/grow/ & /mynetwork/)
    - Finds people with >= 100 connections / mutual connections
    - Automatically sends invitations until the profile reaches its FULL daily cap limit
    - Only marks as fully completed for today once daily_limit invitations have been reached!
    """
    profile = await db.get_profile(profile_id)
    if not profile:
        return {"success": False, "message": f"Profile '{profile_id}' not found"}

    today_str = datetime.now().strftime("%Y-%m-%d")
    daily_limit = profile.get("daily_limit", 30)
    already_sent_today = await db.get_today_sent_count(profile_id, today_str)

    if not force and already_sent_today >= daily_limit:
        return {
            "success": False,
            "message": f"Profile '{profile_id}' has already fulfilled its daily cap limit ({already_sent_today}/{daily_limit}) for today ({today_str})"
        }

    remaining_to_send = max(0, daily_limit - already_sent_today)
    logger.info(
        f"⏰ [Daily Auto-Pilot] Launching unattended daily run for '{profile_id}': "
        f"Already sent today: {already_sent_today}/{daily_limit}. Sending remaining {remaining_to_send} invitations to hit full cap..."
    )

    try:
        # Launch session with auto_connect enabled
        await engine.open_interactive_session(
            profile_id=profile_id,
            start_url="https://www.linkedin.com/mynetwork/grow/",
            timeout_seconds=600,
            auto_connect=True
        )

        final_sent_today = await db.get_today_sent_count(profile_id, today_str)
        if final_sent_today >= daily_limit:
            await db.record_daily_auto_run_completed(profile_id, today_str)
            logger.info(f"🎉 [Daily Auto-Pilot] Daily cap completely achieved ({final_sent_today}/{daily_limit}) for profile '{profile_id}'.")
            return {"success": True, "message": f"Daily cap reached ({final_sent_today}/{daily_limit}) for {profile_id}"}
        else:
            logger.warning(f"⚠️ [Daily Auto-Pilot] Run finished with {final_sent_today}/{daily_limit} sent today for '{profile_id}'. Will resume to fulfill full cap.")
            return {"success": True, "message": f"Progress: {final_sent_today}/{daily_limit} sent today for {profile_id}"}
    except Exception as e:
        logger.error(f"❌ [Daily Auto-Pilot] Failed daily run for '{profile_id}': {e}", exc_info=True)
        return {"success": False, "message": str(e)}


async def check_and_run_daily_tasks() -> None:
    """Checks all profiles configured for Auto-Pilot and triggers dispatch if due."""
    global LAST_CHECK_TIME
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    current_hour = now.hour
    LAST_CHECK_TIME = now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        due_profiles = await db.get_auto_pilot_due_profiles(current_hour=current_hour, today_str=today_str)
        if not due_profiles:
            return

        for p in due_profiles:
            profile_id = p["id"]
            if p.get("status") != "idle":
                logger.debug(f"[Daily Auto-Pilot] Profile '{profile_id}' is currently {p.get('status')}; postponing until idle.")
                continue

            daily_limit = p.get("daily_limit", 30)
            sched_hr = p.get("schedule_hour", 9)
            logger.info(
                f"⏰ [Daily Auto-Pilot] Scheduled time reached for '{profile_id}' "
                f"(Configured hour: {sched_hr:02d}:00, current: {now.strftime('%H:%M:%S')}, limit: {daily_limit}/day)."
            )

            # Run in background so other checks aren't held up
            asyncio.create_task(run_profile_daily_automation(profile_id, force=False))

    except Exception as e:
        logger.error(f"[Daily Auto-Pilot] Error in check_and_run_daily_tasks: {e}", exc_info=True)


async def scheduler_loop() -> None:
    """Continuous background loop running while the server is active."""
    global SCHEDULER_RUNNING
    logger.info("🚀 [Daily Auto-Pilot] Background scheduler loop started. Monitoring daily automated runs...")

    # Wait 5 seconds after startup for initial DB and environment warmup
    await asyncio.sleep(5)

    while SCHEDULER_RUNNING:
        try:
            await check_and_run_daily_tasks()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Daily Auto-Pilot] Loop error: {e}")

        try:
            await asyncio.sleep(SCHEDULER_CHECK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break

    logger.info("🛑 [Daily Auto-Pilot] Background scheduler loop stopped.")


def start_scheduler() -> None:
    """Starts the daily background scheduler task."""
    global SCHEDULER_RUNNING, SCHEDULER_TASK
    if SCHEDULER_TASK is None or SCHEDULER_TASK.done():
        SCHEDULER_RUNNING = True
        SCHEDULER_TASK = asyncio.create_task(scheduler_loop())
        logger.info("⚡ Daily Auto-Pilot scheduler registered and active.")


def stop_scheduler() -> None:
    """Cancels and stops the daily scheduler background task."""
    global SCHEDULER_RUNNING, SCHEDULER_TASK
    SCHEDULER_RUNNING = False
    if SCHEDULER_TASK and not SCHEDULER_TASK.done():
        SCHEDULER_TASK.cancel()
        logger.info("Daily Auto-Pilot scheduler stopped.")


async def get_scheduler_status() -> Dict[str, Any]:
    """Returns real-time status of the daily auto-pilot scheduler and per-profile schedules."""
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    profiles = await db.get_all_profiles()

    profile_statuses: List[Dict[str, Any]] = []
    for p in profiles:
        is_ap = bool(p.get("auto_pilot", 1))
        last_date = p.get("last_auto_run_date")
        sched_hr = p.get("schedule_hour", 9)
        today_sent = p.get("today_sent_count", 0)
        daily_lim = p.get("daily_limit", 20)
        completed_today = (today_sent >= daily_lim)

        if completed_today:
            next_run_desc = f"✓ Daily cap achieved ({today_sent}/{daily_lim}). Next: Tomorrow at {sched_hr:02d}:00"
        elif today_sent > 0:
            next_run_desc = f"In Progress: {today_sent}/{daily_lim} sent today. Resuming to complete daily cap."
        elif now.hour >= sched_hr:
            next_run_desc = f"Due today (scheduled for {sched_hr:02d}:00)"
        else:
            next_run_desc = f"Today at {sched_hr:02d}:00"

        profile_statuses.append({
            "id": p["id"],
            "label": p["label"],
            "auto_pilot": is_ap,
            "schedule_hour": sched_hr,
            "today_sent_count": today_sent,
            "completed_today": completed_today,
            "last_auto_run_date": last_date,
            "next_run_desc": next_run_desc,
            "daily_limit": daily_lim,
            "status": p.get("status", "idle")
        })

    return {
        "scheduler_running": SCHEDULER_RUNNING,
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today_date": today_str,
        "last_check_time": LAST_CHECK_TIME,
        "check_interval_seconds": SCHEDULER_CHECK_INTERVAL_SECONDS,
        "profiles": profile_statuses
    }
