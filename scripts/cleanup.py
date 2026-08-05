# FILE: automation/scripts/cleanup.py
"""Opus Zim — scheduled cleanup of expired PDFs.

Scans all four Generated-PDFs projects for rows whose scheduled_deletion_at has
passed and removes the stored objects. Takes no payload.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from supabase_client import SupabaseRouter  # noqa: E402
from storage_manager import StorageManager  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cleanup")

PDF_DB_NUMBERS = (1, 2, 3, 4)
ROTATION_THRESHOLD_MB = 450.0


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat()


def expired_rows(client: Any, cutoff: str) -> list[dict[str, Any]]:
    """Select generated_files rows that are due for deletion."""
    result = (
        client.table("generated_files")
        .select("*")
        .is_("deleted_at", "null")
        .lte("scheduled_deletion_at", cutoff)
        .execute()
    )
    return list(result.data or [])


def log_storage_usage(router: SupabaseRouter, db_num: int) -> None:
    """Log storage usage for one PDF project and warn when rotation is due."""
    try:
        usage_mb = router.check_pdf_storage_usage(db_num)
    except Exception as exc:  # noqa: BLE001 - usage check is informational
        logger.warning("Could not read storage usage for PDF%s: %s", db_num, exc)
        return

    logger.info("PDF%s storage usage: %.2f MB", db_num, usage_mb)

    try:
        active = int(router.get_pdf_storage_db_number())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read active PDF storage number: %s", exc)
        return

    if db_num == active and usage_mb >= ROTATION_THRESHOLD_MB:
        logger.info(
            "ROTATION NOTICE: active PDF storage PDF%s is at %.2f MB (>= %.0f MB). "
            "Rotation will be performed automatically on the next upload.",
            db_num,
            usage_mb,
            ROTATION_THRESHOLD_MB,
        )


def run() -> None:
    """Scan all PDF projects and delete expired files."""
    try:
        router = SupabaseRouter()
        storage = StorageManager(router)
    except Exception as exc:  # noqa: BLE001 - cannot continue without the router
        logger.error("Could not construct SupabaseRouter: %s", exc)
        sys.exit(1)

    cutoff = now_iso()
    deleted = 0
    failed = 0
    scanned = 0

    for db_num in PDF_DB_NUMBERS:
        logger.info("Scanning PDF%s for files expiring at or before %s", db_num, cutoff)
        try:
            client = router.get_pdf_client_by_number(db_num)
            rows = expired_rows(client, cutoff)
        except Exception as exc:  # noqa: BLE001 - keep scanning other projects
            logger.warning("Could not scan PDF%s: %s", db_num, exc)
            log_storage_usage(router, db_num)
            continue

        scanned += len(rows)
        logger.info("PDF%s has %s expired row(s)", db_num, len(rows))

        for row in rows:
            storage_path = row.get("storage_path")
            row_id = row.get("id")
            if not storage_path:
                logger.warning("PDF%s row %s has no storage_path — skipping", db_num, row_id)
                failed += 1
                continue
            try:
                ok = storage.delete_pdf(storage_path, db_num)
            except Exception as exc:  # noqa: BLE001 - a scheduled job keeps going
                logger.warning("Delete raised for PDF%s %s: %s", db_num, storage_path, exc)
                ok = False

            if not ok:
                logger.warning("Failed to delete PDF%s object %s", db_num, storage_path)
                failed += 1
                continue

            try:
                client.table("generated_files").update({"deleted_at": now_iso()}).eq(
                    "id", row_id
                ).execute()
                deleted += 1
                logger.info("Deleted PDF%s object %s (row %s)", db_num, storage_path, row_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Deleted object but could not mark row %s: %s", row_id, exc)
                failed += 1

        log_storage_usage(router, db_num)

    logger.info("Cleanup complete — deleted: %s, failed: %s, scanned: %s", deleted, failed, scanned)
    print(f"Cleanup complete — deleted: {deleted}, failed: {failed}, scanned: {scanned}")
    sys.exit(0)


if __name__ == "__main__":
    run()
