
import json
import sqlite3

from config import DB_PATH
from contextlib import contextmanager

@contextmanager
def get_connection():
    """Open SQLite, initialize BOM tables, and close safely."""

    conn = sqlite3.connect(DB_PATH)

    try:
        # Metadata and validation results for one BOM per chat.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bom_uploads (
                thread_id TEXT PRIMARY KEY,
                filename TEXT,
                summary TEXT,
                issues TEXT,
                uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Normalized component records stored individually.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bom_items (
                thread_id TEXT,
                item_id TEXT,
                data TEXT,
                PRIMARY KEY (thread_id, item_id)
            )
        """)

        # Automatically commit on success or roll back on failure.
        with conn:
            yield conn

    finally:
        conn.close()



def save_bom(thread_id, filename, result):
    """Save a parsed BOM, replacing any previous BOM for this chat."""

    with get_connection() as conn:

        # Remove the previous BOM associated with this thread.
        conn.execute(
            "DELETE FROM bom_items WHERE thread_id = ?",
            (thread_id,)
        )
        conn.execute(
            "DELETE FROM bom_uploads WHERE thread_id = ?",
            (thread_id,)
        )

        # Save upload metadata and validation results.
        conn.execute("""
            INSERT INTO bom_uploads (thread_id, filename, summary, issues)
            VALUES (?, ?, ?, ?)
        """, (
            thread_id,
            filename,
            json.dumps(result["summary"]),
            json.dumps(result["issues"])
        ))

        # Save normalized components individually as JSON.
        conn.executemany("""
            INSERT INTO bom_items (thread_id, item_id, data)
            VALUES (?, ?, ?)
        """, [
            (thread_id, item["item_id"], json.dumps(item))
            for item in result["items"]
        ])


def get_bom(thread_id):
    """Retrieve the saved BOM associated with a conversation."""

    with get_connection() as conn:

        # Retrieve filename, summary and validation issues.
        upload = conn.execute("""
            SELECT filename, summary, issues
            FROM bom_uploads
            WHERE thread_id = ?
        """, (thread_id,)).fetchone()

        if upload is None:
            return None

        # Retrieve all normalized components for the same thread.
        rows = conn.execute("""
            SELECT data
            FROM bom_items
            WHERE thread_id = ?
            ORDER BY item_id
        """, (thread_id,)).fetchall()

    # Convert stored JSON strings back to Python dictionaries.
    return {
        "filename": upload[0],
        "summary": json.loads(upload[1]),
        "issues": json.loads(upload[2]),
        "items": [json.loads(row[0]) for row in rows]
    }
