# FILE: automation/scripts/supabase_client.py
"""Multi-project Supabase router for the Opus Zim automation package.

Resolves the correct Supabase project client for users, subject content, PDF
storage, API keys and APK hosting, using service_role credentials supplied via
environment variables. All automation writes bypass RLS by design.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from supabase import Client, create_client

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

PREFIXES: tuple[str, ...] = (
    "USERS1",
    "USERS2",
    "ASCI",
    "ACOM",
    "AART",
    "OSCI",
    "OCOM",
    "OART",
    "PDF1",
    "PDF2",
    "PDF3",
    "PDF4",
    "APIKEYS",
    "APK",
)

# Authoritative subject_code -> project prefix map.
SUBJECT_PREFIX_MAP: dict[str, str] = {
    # O Level sciences
    "OLEVEL_MATH": "OSCI",
    "OLEVEL_BIO": "OSCI",
    "OLEVEL_PHYS": "OSCI",
    "OLEVEL_CHEM": "OSCI",
    "OLEVEL_COMP": "OSCI",
    "OLEVEL_AGRI": "OSCI",
    # O Level arts
    "OLEVEL_HIST": "OART",
    "OLEVEL_GEOG": "OART",
    "OLEVEL_TTD": "OART",
    # A Level sciences
    "ALEVEL_MATH": "ASCI",
    "ALEVEL_BIO": "ASCI",
    "ALEVEL_PHYS": "ASCI",
    "ALEVEL_CHEM": "ASCI",
    "ALEVEL_COMP": "ASCI",
    "ALEVEL_AGRI": "ASCI",
    # A Level arts
    "ALEVEL_HIST": "AART",
    "ALEVEL_GEOG": "AART",
    "ALEVEL_TTD": "AART",
    # OCOM and ACOM are reserved and map to no subject_code.
}

VALID_CODES: tuple[str, ...] = (
    "MATH",
    "GEOG",
    "COMP",
    "AGRI",
    "TTD",
    "HIST",
    "BIO",
    "PHYS",
    "CHEM",
)

PDF_BUCKET: str = "generated-pdfs"
PDF_BUCKET_PATH: str = "pdfs"
PDF_STORAGE_LIMIT_MB: float = 450.0
PDF_LIST_PAGE_SIZE: int = 100


class SupabaseRouter:
    """Routes automation traffic to the correct Supabase project."""

    def __init__(self) -> None:
        self._clients: dict[str, Client] = {}

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _env(self, name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(
                f"Missing required environment variable: {name}"
            )
        return value

    def _client(self, prefix: str) -> Client:
        prefix = prefix.upper()
        if prefix not in PREFIXES:
            raise ValueError(
                f"Unknown Supabase project prefix '{prefix}'. "
                f"Valid prefixes: {', '.join(PREFIXES)}"
            )
        cached = self._clients.get(prefix)
        if cached is not None:
            return cached

        url = self._env(f"SB_{prefix}_URL")
        service_key = self._env(f"SB_{prefix}_SERVICE_KEY")
        try:
            client = create_client(url, service_key)
        except Exception as exc:
            logger.error("Failed to create Supabase client for %s: %s", prefix, exc)
            raise
        logger.info("Created Supabase service client for prefix %s", prefix)
        self._clients[prefix] = client
        return client

    # ------------------------------------------------------------------ #
    # Users
    # ------------------------------------------------------------------ #

    def get_users_client(self) -> Client:
        """Return the client for the currently active Users project."""
        number = self.get_users_db_number()
        return self._client(f"USERS{number}")

    def get_users_db_number(self) -> int:
        """Return the active Users DB number (1 or 2)."""
        raw = self.get_config("active_users_db")
        if raw is None or str(raw).strip() == "":
            return 1
        try:
            number = int(str(raw).strip())
        except ValueError as exc:
            raise ValueError(
                f"active_users_db must be an integer, got '{raw}'"
            ) from exc
        if number not in (1, 2):
            raise ValueError(
                f"active_users_db must be 1 or 2, got {number}"
            )
        return number

    # ------------------------------------------------------------------ #
    # Subject content
    # ------------------------------------------------------------------ #

    def get_subject_client(self, subject_code: str, level: str) -> Client:
        """Return the content project client for a subject and level."""
        normalised = self._normalise_subject_code(subject_code, level)
        prefix = SUBJECT_PREFIX_MAP.get(normalised)
        if prefix is None:
            raise ValueError(
                f"No Supabase project mapped for subject_code '{normalised}'. "
                f"Valid subject codes: {', '.join(sorted(SUBJECT_PREFIX_MAP))}"
            )
        logger.info("Routed subject_code %s to prefix %s", normalised, prefix)
        return self._client(prefix)

    def _normalise_subject_code(self, subject_code: str, level: str) -> str:
        if not subject_code or not str(subject_code).strip():
            raise ValueError("subject_code must be a non-empty string")

        raw = str(subject_code).strip().upper().replace("-", "_").replace(" ", "_")

        if raw.startswith("OLEVEL_") or raw.startswith("ALEVEL_"):
            level_prefix, _, code = raw.partition("_")
        else:
            level_prefix = self._normalise_level(level)
            code = raw

        if code not in VALID_CODES:
            raise ValueError(
                f"Unknown subject code '{code}' derived from '{subject_code}'. "
                f"Valid codes: {', '.join(VALID_CODES)}"
            )
        return f"{level_prefix}_{code}"

    def _normalise_level(self, level: str) -> str:
        if not level or not str(level).strip():
            raise ValueError(
                "level is required when subject_code is a bare code "
                "(expected 'O Level' or 'A Level')"
            )
        cleaned = str(level).strip().upper().replace("-", " ").replace("_", " ")
        cleaned = " ".join(cleaned.split())
        if cleaned in ("O LEVEL", "OLEVEL", "O"):
            return "OLEVEL"
        if cleaned in ("A LEVEL", "ALEVEL", "A"):
            return "ALEVEL"
        raise ValueError(
            f"Unknown level '{level}'. Expected 'O Level' or 'A Level'."
        )

    # ------------------------------------------------------------------ #
    # PDF storage
    # ------------------------------------------------------------------ #

    def get_pdf_storage_client(self) -> Client:
        """Return an active Generated-PDFs client, rotating when full."""
        active = self.get_pdf_storage_db_number()
        usage = self.check_pdf_storage_usage(active)
        logger.info("PDF storage PDF%d usage: %.2f MB", active, usage)
        if usage < PDF_STORAGE_LIMIT_MB:
            return self._client(f"PDF{active}")

        usages: dict[int, float] = {active: usage}
        for step in range(1, 4):
            candidate = ((active - 1 + step) % 4) + 1
            candidate_usage = self.check_pdf_storage_usage(candidate)
            usages[candidate] = candidate_usage
            logger.info(
                "Evaluating rotation target PDF%d usage: %.2f MB",
                candidate,
                candidate_usage,
            )
            if candidate_usage < PDF_STORAGE_LIMIT_MB:
                self.set_config("active_pdf_storage_db", str(candidate))
                logger.info(
                    "Switched active PDF storage from PDF%d (%.2f MB) to PDF%d (%.2f MB)",
                    active,
                    usage,
                    candidate,
                    candidate_usage,
                )
                return self._client(f"PDF{candidate}")

        least_full = min(usages, key=lambda n: usages[n])
        logger.critical(
            "All four Generated-PDFs projects are at or above %.0f MB. "
            "Falling back to least full project PDF%d (%.2f MB).",
            PDF_STORAGE_LIMIT_MB,
            least_full,
            usages[least_full],
        )
        if least_full != active:
            self.set_config("active_pdf_storage_db", str(least_full))
        return self._client(f"PDF{least_full}")

    def get_pdf_storage_db_number(self) -> int:
        """Return the active Generated-PDFs DB number (1..4)."""
        raw = self.get_config("active_pdf_storage_db")
        if raw is None or str(raw).strip() == "":
            return 1
        try:
            number = int(str(raw).strip())
        except ValueError as exc:
            raise ValueError(
                f"active_pdf_storage_db must be an integer, got '{raw}'"
            ) from exc
        if number not in (1, 2, 3, 4):
            raise ValueError(
                f"active_pdf_storage_db must be between 1 and 4, got {number}"
            )
        return number

    def get_pdf_client_by_number(self, n: int) -> Client:
        """Return the Generated-PDFs client for an explicit number."""
        number = int(n)
        if number not in (1, 2, 3, 4):
            raise ValueError(
                f"PDF storage number must be between 1 and 4, got {number}"
            )
        return self._client(f"PDF{number}")

    def check_pdf_storage_usage(self, n: int) -> float:
        """Return megabytes used in the generated-pdfs bucket of PDF{n}."""
        client = self.get_pdf_client_by_number(n)
        total_bytes = 0
        offset = 0
        while True:
            try:
                page = client.storage.from_(PDF_BUCKET).list(
                    path=PDF_BUCKET_PATH,
                    options={
                        "limit": PDF_LIST_PAGE_SIZE,
                        "offset": offset,
                        "sortBy": {"column": "name", "order": "asc"},
                    },
                )
            except Exception as exc:
                logger.error(
                    "Failed to list bucket '%s' on PDF%d: %s", PDF_BUCKET, n, exc
                )
                raise
            if not page:
                break
            for obj in page:
                metadata = obj.get("metadata") or {}
                size = metadata.get("size") or 0
                try:
                    total_bytes += int(size)
                except (TypeError, ValueError):
                    logger.warning(
                        "Non-numeric size for object '%s' on PDF%d; counted as 0",
                        obj.get("name"),
                        n,
                    )
            if len(page) < PDF_LIST_PAGE_SIZE:
                break
            offset += PDF_LIST_PAGE_SIZE
        return round(total_bytes / (1024 * 1024), 2)

    # ------------------------------------------------------------------ #
    # API keys / APK
    # ------------------------------------------------------------------ #

    def get_api_keys_client(self) -> Client:
        """Return the Our-API-Keys project client."""
        return self._client("APIKEYS")

    def get_apk_client(self) -> Client:
        """Return the APK hosting project client."""
        return self._client("APK")

    # ------------------------------------------------------------------ #
    # Config
    # ------------------------------------------------------------------ #

    def get_config(self, key: str) -> str | None:
        """Read a system_config value from Our-API-Keys; None when absent."""
        try:
            response = (
                self.get_api_keys_client()
                .table("system_config")
                .select("config_value")
                .eq("config_key", key)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning("Could not read system_config key '%s': %s", key, exc)
            return None
        rows = response.data or []
        if not rows:
            return None
        value = rows[0].get("config_value")
        return None if value is None else str(value)

    def set_config(self, key: str, value: str) -> None:
        """Upsert a system_config value on Our-API-Keys."""
        payload = {
            "config_key": key,
            "config_value": str(value),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.get_api_keys_client().table("system_config").upsert(
                payload, on_conflict="config_key"
            ).execute()
        except Exception as exc:
            logger.error("Failed to upsert system_config key '%s': %s", key, exc)
            raise
        logger.info("Updated system_config key '%s'", key)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    router = SupabaseRouter()

    users_db = router.get_users_db_number()
    pdf_db = router.get_pdf_storage_db_number()

    api_keys_client = router.get_api_keys_client()
    gemini_response = (
        api_keys_client.table("api_keys")
        .select("id")
        .eq("provider", "gemini")
        .eq("is_active", True)
        .execute()
    )
    gemini_count = len(gemini_response.data or [])

    kie_response = (
        api_keys_client.table("api_keys")
        .select("id,usage_count,max_usage")
        .eq("provider", "kie")
        .eq("is_active", True)
        .execute()
    )
    kie_rows = kie_response.data or []
    kie_count = sum(
        1
        for row in kie_rows
        if row.get("max_usage") is None
        or int(row.get("usage_count") or 0) < int(row["max_usage"])
    )

    print(f"Active users DB number: {users_db}")
    print(f"Active PDF storage DB number: {pdf_db}")
    print(f"Usable gemini keys: {gemini_count}")
    print(f"Usable kie keys: {kie_count}")
