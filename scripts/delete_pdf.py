# FILE: automation/scripts/delete_pdf.py
"""Opus Zim — delete a single PDF after it has been emailed/downloaded.

Triggered by the `delete-pdf-after-email` repository_dispatch event.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from supabase_client import SupabaseRouter  # noqa: E402
from storage_manager import StorageManager  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("delete_pdf")

REQUIRED_VARS = ("PROJECT_ID", "STORAGE_PATH", "DB_NUM")


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat()


def read_payload() -> tuple[str, str, int]:
    """Read and validate PROJECT_ID, STORAGE_PATH and DB_NUM."""
    values = {name: (os.environ.get(name) or "").strip() for name in REQUIRED_VARS}
    missing = [name for name in REQUIRED_VARS if not values[name]]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    try:
        db_num = int(values["DB_NUM"])
    except ValueError:
        logger.error("DB_NUM must be an integer 1..4 (got %r)", values["DB_NUM"])
        sys.exit(1)

    if db_num < 1 or db_num > 4:
        logger.error("DB_NUM must be in the range 1..4 (got %s)", db_num)
        sys.exit(1)

    return values["PROJECT_ID"], values["STORAGE_PATH"], db_num


def run() -> None:
    """Delete one stored PDF and update the bookkeeping rows."""
    project_id, storage_path, db_num = read_payload()
    logger.info("Deleting PDF for project %s from PDF%s at %s", project_id, db_num, storage_path)

    try:
        router = SupabaseRouter()
        storage = StorageManager(router)
    except Exception as exc:  # noqa: BLE001 - cannot continue without the router
        logger.error("Could not construct SupabaseRouter: %s", exc)
        sys.exit(1)

    try:
        ok = storage.delete_pdf(storage_path, db_num)
    except Exception as exc:  # noqa: BLE001
        logger.error("Delete raised for %s on PDF%s: %s", storage_path, db_num, exc)
        sys.exit(1)

    if not ok:
        logger.error("delete_pdf returned False for %s on PDF%s", storage_path, db_num)
        sys.exit(1)

    logger.info("Storage object deleted from PDF%s", db_num)

    try:
        pdf_client = router.get_pdf_client_by_number(db_num)
        pdf_client.table("generated_files").update({"deleted_at": now_iso()}).eq(
            "project_id", project_id
        ).execute()
        logger.info("generated_files rows for project %s marked deleted", project_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not mark generated_files row deleted: %s", exc)
        sys.exit(1)

    try:
        users_client = router.get_users_client()
        current = (
            users_client.table("projects")
            .select("status")
            .eq("id", project_id)
            .limit(1)
            .execute()
        )
        rows = current.data or []
        status = rows[0].get("status") if rows else None
        if status in ("paid", "downloaded"):
            users_client.table("projects").update(
                {"status": "downloaded", "downloaded_at": now_iso()}
            ).eq("id", project_id).execute()
            logger.info("projects row %s marked 'downloaded'", project_id)
        else:
            logger.info(
                "projects row %s left at status %r (only 'paid'/'downloaded' are advanced)",
                project_id,
                status,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not update projects row: %s", exc)
        sys.exit(1)

    logger.info("Delete complete for project %s", project_id)
    sys.exit(0)


if __name__ == "__main__":
    run()
