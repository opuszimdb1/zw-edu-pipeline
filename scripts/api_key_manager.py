# FILE: automation/scripts/api_key_manager.py
"""API-key rotation manager for the Opus Zim automation package.

Reads and mutates the api_keys table on the Our-API-Keys Supabase project.
Gemini keys are unlimited and tried in order; Kie AI keys are metered and must
respect max_usage. Key values are never logged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from supabase import Client

logger = logging.getLogger(__name__)

TABLE: str = "api_keys"


class APIKeyManager:
    """Selects and meters provider API keys stored on Our-API-Keys."""

    def __init__(self, api_keys_client: Client) -> None:
        self._client = api_keys_client

    # ------------------------------------------------------------------ #
    # Gemini
    # ------------------------------------------------------------------ #

    def get_gemini_key(self) -> str:
        """Return the first usable Gemini API key value."""
        keys = self.get_all_gemini_keys()
        if not keys:
            raise RuntimeError(
                "No active Gemini API key found in Our-API-Keys.api_keys"
            )
        return keys[0]["api_key"]

    def get_all_gemini_keys(self) -> list[dict]:
        """Return all active Gemini keys in fallback order."""
        try:
            response = (
                self._client.table(TABLE)
                .select("id,api_key,last_error_at,added_at")
                .eq("provider", "gemini")
                .eq("is_active", True)
                .order("last_error_at", desc=False, nullsfirst=True)
                .order("added_at", desc=False)
                .execute()
            )
        except Exception as exc:
            logger.error("Failed to fetch gemini keys: %s", exc)
            raise
        rows = response.data or []
        logger.info("Fetched %d active gemini key(s)", len(rows))
        return [
            {"id": str(row["id"]), "api_key": str(row["api_key"])} for row in rows
        ]

    # ------------------------------------------------------------------ #
    # Kie AI
    # ------------------------------------------------------------------ #

    def get_kie_key(self) -> dict:
        """Return the least-used usable Kie AI key."""
        try:
            response = (
                self._client.table(TABLE)
                .select("id,api_key,usage_count,max_usage,added_at")
                .eq("provider", "kie")
                .eq("is_active", True)
                .order("usage_count", desc=False)
                .order("added_at", desc=False)
                .execute()
            )
        except Exception as exc:
            logger.error("Failed to fetch kie keys: %s", exc)
            raise

        for row in response.data or []:
            max_usage = row.get("max_usage")
            usage_count = int(row.get("usage_count") or 0)
            if max_usage is None or usage_count < int(max_usage):
                logger.info("Selected kie key id=%s", row["id"])
                return {"id": str(row["id"]), "api_key": str(row["api_key"])}

        raise RuntimeError("No usable Kie AI API key (all exhausted or inactive)")

    def mark_kie_success(self, key_id: str) -> None:
        """Increment usage for a Kie key and deactivate it when quota is hit."""
        row = self._fetch_row(key_id)
        if row is None:
            return

        new_usage = int(row.get("usage_count") or 0) + 1
        max_usage = row.get("max_usage")
        updates: dict[str, object] = {
            "usage_count": new_usage,
            "last_used_at": self._now(),
        }

        quota_reached = max_usage is not None and new_usage >= int(max_usage)
        if quota_reached:
            updates["is_active"] = False
            updates["notes"] = self._append_note(
                row.get("notes"), "auto-deactivated: quota reached"
            )

        self._update_row(key_id, updates)
        logger.info("Recorded kie success for key id=%s (usage=%d)", key_id, new_usage)
        if quota_reached:
            logger.warning(
                "Auto-deactivated kie key id=%s: quota reached (%d/%s)",
                key_id,
                new_usage,
                max_usage,
            )

    # ------------------------------------------------------------------ #
    # Shared mutators
    # ------------------------------------------------------------------ #

    def mark_key_error(self, key_id: str) -> None:
        """Record an error timestamp for a key without deactivating it."""
        row = self._fetch_row(key_id)
        if row is None:
            return
        self._update_row(key_id, {"last_error_at": self._now()})
        logger.warning(
            "Recorded error for %s key id=%s", row.get("provider"), key_id
        )

    def deactivate_key(self, key_id: str) -> None:
        """Deactivate a key after repeated failures."""
        row = self._fetch_row(key_id)
        if row is None:
            return
        self._update_row(
            key_id,
            {
                "is_active": False,
                "notes": self._append_note(
                    row.get("notes"), "deactivated: repeated failures"
                ),
            },
        )
        logger.warning(
            "Deactivated %s key id=%s after repeated failures",
            row.get("provider"),
            key_id,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _append_note(self, existing: str | None, suffix: str) -> str:
        base = (existing or "").strip()
        return f"{base} | {suffix}" if base else suffix

    def _fetch_row(self, key_id: str) -> dict | None:
        try:
            response = (
                self._client.table(TABLE)
                .select("id,provider,usage_count,max_usage,is_active,notes")
                .eq("id", key_id)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.error("Failed to fetch api key id=%s: %s", key_id, exc)
            raise
        rows = response.data or []
        if not rows:
            logger.warning("No api_keys row found for id=%s; skipping update", key_id)
            return None
        return rows[0]

    def _update_row(self, key_id: str, updates: dict) -> None:
        try:
            self._client.table(TABLE).update(updates).eq("id", key_id).execute()
        except Exception as exc:
            logger.error("Failed to update api key id=%s: %s", key_id, exc)
            raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from supabase_client import SupabaseRouter

    router = SupabaseRouter()
    manager = APIKeyManager(router.get_api_keys_client())

    print(f"Active users DB number: {router.get_users_db_number()}")
    print(f"Active PDF storage DB number: {router.get_pdf_storage_db_number()}")

    gemini_keys = manager.get_all_gemini_keys()
    print(f"Usable gemini keys: {len(gemini_keys)}")

    try:
        manager.get_kie_key()
        kie_usable = 1
    except RuntimeError as exc:
        logger.warning("Kie key check: %s", exc)
        kie_usable = 0
    print(f"Usable kie keys (at least): {kie_usable}")
