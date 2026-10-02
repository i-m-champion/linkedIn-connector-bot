"""
Database access layer for the Multi-Profile Web Automation Management System.
Uses SQLite with aiosqlite for asynchronous non-blocking queries.
"""

import os
from datetime import datetime
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
import aiosqlite

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "automation.db")


@asynccontextmanager
async def get_db():
    """Asynchronous context manager providing an aiosqlite connection with WAL and Row factory."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute("PRAGMA journal_mode = WAL;")
        yield conn


async def init_db() -> None:
    """Initializes the database schema and indices."""
    async with get_db() as conn:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                proxy_url TEXT,
                daily_limit INTEGER NOT NULL DEFAULT 30,
                status TEXT NOT NULL DEFAULT 'idle',
                auto_pilot INTEGER NOT NULL DEFAULT 1,
                schedule_hour INTEGER NOT NULL DEFAULT 9,
                last_auto_run_date TEXT,
                last_run_at TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL,
                target_url TEXT NOT NULL,
                message_text TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                processed_at TEXT,
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_queue_profile_status ON queue_items (profile_id, status);
            CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles (status);
        """)

        # Graceful migration for existing databases
        cursor = await conn.execute("PRAGMA table_info(profiles)")
        columns = [row["name"] for row in await cursor.fetchall()]
        if "auto_pilot" not in columns:
            await conn.execute("ALTER TABLE profiles ADD COLUMN auto_pilot INTEGER NOT NULL DEFAULT 1")
        if "schedule_hour" not in columns:
            await conn.execute("ALTER TABLE profiles ADD COLUMN schedule_hour INTEGER NOT NULL DEFAULT 9")
        if "last_auto_run_date" not in columns:
            await conn.execute("ALTER TABLE profiles ADD COLUMN last_auto_run_date TEXT")

        await conn.commit()


async def create_profile(
    profile_id: str, 
    label: str, 
    proxy_url: Optional[str] = None, 
    daily_limit: int = 30,
    auto_pilot: int = 1,
    schedule_hour: int = 9
) -> Dict[str, Any]:
    """Creates a new automation profile."""
    clean_proxy = proxy_url.strip() if proxy_url and proxy_url.strip() else None
    clean_id = profile_id.strip()
    clean_label = label.strip()

    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO profiles (id, label, proxy_url, daily_limit, status, auto_pilot, schedule_hour)
            VALUES (?, ?, ?, ?, 'idle', ?, ?)
            """,
            (clean_id, clean_label, clean_proxy, daily_limit, 1 if auto_pilot else 0, schedule_hour)
        )
        await conn.commit()
    return await get_profile(clean_id)


async def get_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves a single profile by ID."""
    async with get_db() as conn:
        cursor = await conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_profiles() -> List[Dict[str, Any]]:
    """Retrieves all profiles along with their queue metrics and today's sent count."""
    today_pattern = datetime.now().strftime("%Y-%m-%d") + "%"
    async with get_db() as conn:
        query = """
            SELECT 
                p.id,
                p.label,
                p.proxy_url,
                p.daily_limit,
                p.status,
                p.auto_pilot,
                p.schedule_hour,
                p.last_auto_run_date,
                p.last_run_at,
                p.created_at,
                COUNT(CASE WHEN q.status = 'pending' THEN 1 END) AS pending_count,
                COUNT(CASE WHEN q.status = 'completed' THEN 1 END) AS completed_count,
                COUNT(CASE WHEN q.status = 'failed' THEN 1 END) AS failed_count,
                COUNT(CASE WHEN q.status = 'completed' AND (q.processed_at LIKE ? OR (q.processed_at IS NULL AND q.created_at LIKE ?)) THEN 1 END) AS today_sent_count,
                COUNT(q.id) AS total_count
            FROM profiles p
            LEFT JOIN queue_items q ON p.id = q.profile_id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """
        cursor = await conn.execute(query, (today_pattern, today_pattern))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def update_profile_status(profile_id: str, status: str, update_last_run: bool = False) -> None:
    """Updates the execution status of a profile ('idle', 'running', 'authenticating')."""
    async with get_db() as conn:
        if update_last_run:
            await conn.execute(
                "UPDATE profiles SET status = ?, last_run_at = datetime('now') WHERE id = ?",
                (status, profile_id)
            )
        else:
            await conn.execute(
                "UPDATE profiles SET status = ? WHERE id = ?",
                (status, profile_id)
            )
        await conn.commit()


async def delete_profile(profile_id: str) -> bool:
    """Deletes a profile and all its associated queue items."""
    async with get_db() as conn:
        cursor = await conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def update_profile(
    profile_id: str, 
    label: str, 
    proxy_url: Optional[str] = None, 
    daily_limit: int = 30,
    auto_pilot: Optional[int] = None,
    schedule_hour: Optional[int] = None
) -> bool:
    """Updates profile configuration details."""
    clean_proxy = proxy_url.strip() if proxy_url and proxy_url.strip() else None
    clean_label = label.strip()
    async with get_db() as conn:
        updates = ["label = ?", "proxy_url = ?", "daily_limit = ?"]
        params = [clean_label, clean_proxy, max(1, daily_limit)]
        if auto_pilot is not None:
            updates.append("auto_pilot = ?")
            params.append(1 if auto_pilot else 0)
        if schedule_hour is not None:
            updates.append("schedule_hour = ?")
            params.append(schedule_hour)
        params.append(profile_id)

        cursor = await conn.execute(
            f"UPDATE profiles SET {', '.join(updates)} WHERE id = ?",
            tuple(params)
        )
        await conn.commit()
        return cursor.rowcount > 0


async def update_profile_auto_pilot(
    profile_id: str, 
    auto_pilot: int, 
    schedule_hour: Optional[int] = None
) -> bool:
    """Updates the Auto-Pilot toggle and optional scheduled hour for a profile."""
    async with get_db() as conn:
        if schedule_hour is not None:
            cursor = await conn.execute(
                """
                UPDATE profiles 
                SET auto_pilot = ?, schedule_hour = ?
                WHERE id = ?
                """,
                (1 if auto_pilot else 0, schedule_hour, profile_id)
            )
        else:
            cursor = await conn.execute(
                """
                UPDATE profiles 
                SET auto_pilot = ?
                WHERE id = ?
                """,
                (1 if auto_pilot else 0, profile_id)
            )
        await conn.commit()
        return cursor.rowcount > 0


async def record_daily_auto_run_completed(profile_id: str, run_date: str) -> None:
    """Records that today's automated run for this profile was completed."""
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE profiles 
            SET last_auto_run_date = ?, last_run_at = datetime('now')
            WHERE id = ?
            """,
            (run_date, profile_id)
        )
        await conn.commit()


async def get_today_sent_count(profile_id: str, date_str: Optional[str] = None) -> int:
    """Retrieves the exact count of successful connection invitations sent today for a given profile."""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    pattern = f"{date_str}%"
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT COUNT(*) AS sent_today 
            FROM queue_items 
            WHERE profile_id = ? 
              AND status = 'completed' 
              AND (processed_at LIKE ? OR (processed_at IS NULL AND created_at LIKE ?))
            """,
            (profile_id, pattern, pattern)
        )
        row = await cursor.fetchone()
        return row["sent_today"] if row else 0


async def reset_stale_profile_statuses() -> int:
    """Resets any profiles left in 'running' or 'authenticating' back to 'idle' on startup or recovery."""
    async with get_db() as conn:
        cursor = await conn.execute(
            "UPDATE profiles SET status = 'idle' WHERE status != 'idle'"
        )
        await conn.commit()
        return cursor.rowcount


async def get_auto_pilot_due_profiles(current_hour: int, today_str: str) -> List[Dict[str, Any]]:
    """Retrieves all profiles that are configured for Auto-Pilot, idle, and have not yet run today."""
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM profiles 
            WHERE auto_pilot = 1 
              AND status = 'idle'
              AND (last_auto_run_date IS NULL OR last_auto_run_date != ?)
              AND schedule_hour <= ?
            """,
            (today_str, current_hour)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def target_url_exists(profile_id: str, target_url: str) -> bool:
    """Checks if a target URL is already queued or processed for a profile."""
    clean_target = target_url.strip().rstrip("/")
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT id FROM queue_items 
            WHERE profile_id = ? AND (target_url = ? OR target_url = ?)
            LIMIT 1
            """,
            (profile_id, clean_target, f"{clean_target}/")
        )
        row = await cursor.fetchone()
        return row is not None


async def add_discovered_target(profile_id: str, target_url: str, mutual_count: int, name: Optional[str] = None) -> bool:
    """Adds a target profile discovered via network recommendations if not already present."""
    clean_target = target_url.strip().rstrip("/")
    if await target_url_exists(profile_id, clean_target):
        return False

    note_text = f"Discovered: {mutual_count} mutual connections"
    if name:
        note_text += f" ({name})"

    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO queue_items (profile_id, target_url, message_text, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (profile_id, clean_target, note_text)
        )
        await conn.commit()
        return True


async def record_sent_connection(
    profile_id: str, 
    target_url: str, 
    mutual_count: int, 
    name: Optional[str] = None,
    role_label: Optional[str] = None
) -> None:
    """Records that a connection invitation was sent to a target profile."""
    clean_target = target_url.strip().rstrip("/")
    note_text = f"Sent invitation: {mutual_count} mutual connections"
    if role_label:
        note_text += f" | {role_label}"
    if name:
        note_text += f" ({name})"

    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT id FROM queue_items 
            WHERE profile_id = ? AND (target_url = ? OR target_url = ?)
            LIMIT 1
            """,
            (profile_id, clean_target, f"{clean_target}/")
        )
        row = await cursor.fetchone()
        if row:
            await conn.execute(
                """
                UPDATE queue_items 
                SET status = 'completed', message_text = ?, error_message = 'Invitation sent successfully', processed_at = datetime('now')
                WHERE id = ?
                """,
                (note_text, row["id"])
            )
        else:
            await conn.execute(
                """
                INSERT INTO queue_items (profile_id, target_url, message_text, status, error_message, processed_at)
                VALUES (?, ?, ?, 'completed', 'Invitation sent successfully', datetime('now'))
                """,
                (profile_id, clean_target, note_text)
            )
        await conn.commit()


async def get_sent_urls_for_profile(profile_id: str) -> List[str]:
    """Retrieves all target URLs that have already received an invitation or are completed for this profile."""
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT target_url FROM queue_items 
            WHERE profile_id = ? AND status = 'completed'
            """,
            (profile_id,)
        )
        rows = await cursor.fetchall()
        return [r["target_url"].rstrip("/") for r in rows]



async def add_queue_item(profile_id: str, target_url: str, message_text: Optional[str] = None) -> int:
    """Adds a single item to the execution queue."""
    clean_target = target_url.strip()
    clean_msg = message_text.strip() if message_text and message_text.strip() else None

    async with get_db() as conn:
        cursor = await conn.execute(
            """
            INSERT INTO queue_items (profile_id, target_url, message_text, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (profile_id, clean_target, clean_msg)
        )
        await conn.commit()
        return cursor.lastrowid


async def add_queue_items_bulk(items: List[tuple]) -> int:
    """Bulk inserts a list of (profile_id, target_url, message_text) tuples."""
    if not items:
        return 0
    async with get_db() as conn:
        cursor = await conn.executemany(
            """
            INSERT INTO queue_items (profile_id, target_url, message_text, status)
            VALUES (?, ?, ?, 'pending')
            """,
            items
        )
        await conn.commit()
        return cursor.rowcount


async def get_pending_queue_items(profile_id: str, limit: int = 15) -> List[Dict[str, Any]]:
    """Retrieves next pending queue items for a designated profile up to limit."""
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM queue_items 
            WHERE profile_id = ? AND status = 'pending'
            ORDER BY id ASC
            LIMIT ?
            """,
            (profile_id, limit)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def update_queue_item_status(item_id: int, status: str, error_message: Optional[str] = None) -> None:
    """Updates the status and timestamps of a queue item."""
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE queue_items 
            SET status = ?, error_message = ?, processed_at = datetime('now')
            WHERE id = ?
            """,
            (status, error_message, item_id)
        )
        await conn.commit()


async def get_queue_items(
    profile_id: Optional[str] = None, 
    status: Optional[str] = None, 
    limit: int = 100, 
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Fetches queue items with optional filters, joined with profile label."""
    query = """
        SELECT 
            q.id,
            q.profile_id,
            p.label AS profile_label,
            q.target_url,
            q.message_text,
            q.status,
            q.error_message,
            q.created_at,
            q.processed_at
        FROM queue_items q
        LEFT JOIN profiles p ON q.profile_id = p.id
        WHERE 1=1
    """
    params: List[Any] = []
    if profile_id:
        query += " AND q.profile_id = ?"
        params.append(profile_id)
    if status:
        query += " AND q.status = ?"
        params.append(status)

    query += " ORDER BY q.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    async with get_db() as conn:
        cursor = await conn.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def delete_queue_item(item_id: int) -> bool:
    """Deletes a queue item by ID."""
    async with get_db() as conn:
        cursor = await conn.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
        await conn.commit()
        return cursor.rowcount > 0


async def reset_failed_items(profile_id: Optional[str] = None) -> int:
    """Resets failed items back to 'pending' state for retry."""
    async with get_db() as conn:
        if profile_id:
            cursor = await conn.execute(
                "UPDATE queue_items SET status = 'pending', error_message = NULL WHERE profile_id = ? AND status = 'failed'",
                (profile_id,)
            )
        else:
            cursor = await conn.execute(
                "UPDATE queue_items SET status = 'pending', error_message = NULL WHERE status = 'failed'"
            )
        await conn.commit()
        return cursor.rowcount


async def clear_queue(profile_id: Optional[str] = None, status: Optional[str] = None) -> int:
    """Clears queue items matching criteria."""
    query = "DELETE FROM queue_items WHERE 1=1"
    params: List[Any] = []
    if profile_id:
        query += " AND profile_id = ?"
        params.append(profile_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    
    async with get_db() as conn:
        cursor = await conn.execute(query, tuple(params))
        await conn.commit()
        return cursor.rowcount


async def get_dashboard_stats() -> Dict[str, int]:
    """Returns overview count aggregates for dashboard display."""
    async with get_db() as conn:
        p_cursor = await conn.execute("SELECT COUNT(*) as total, COUNT(CASE WHEN status != 'idle' THEN 1 END) as active FROM profiles")
        p_row = await p_cursor.fetchone()
        
        q_cursor = await conn.execute("""
            SELECT 
                COUNT(*) as total_items,
                COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed
            FROM queue_items
        """)
        q_row = await q_cursor.fetchone()

        return {
            "total_profiles": p_row["total"] if p_row else 0,
            "active_profiles": p_row["active"] if p_row else 0,
            "total_items": q_row["total_items"] if q_row else 0,
            "pending_items": q_row["pending"] if q_row else 0,
            "completed_items": q_row["completed"] if q_row else 0,
            "failed_items": q_row["failed"] if q_row else 0,
        }
