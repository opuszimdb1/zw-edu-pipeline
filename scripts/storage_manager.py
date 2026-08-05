# FILE: automation/scripts/storage_manager.py
"""Opus Zim — Module 10: Storage manager.

Moves PDFs, page previews and subject reference files in and out of the correct
Supabase project buckets.

Ownership rules (do not violate):
  * This module ONLY uploads / downloads / deletes storage objects and returns
    paths. It NEVER inserts or updates rows in ``generated_files`` or
    ``projects`` — Module 11's ``main.py`` owns every one of those writes.
  * PDF storage rotation (450 MB threshold per Generated-PDFs project) is
    enforced by Module 7's ``SupabaseRouter``; this module only calls it.

Buckets / path conventions (exact, never change):
  Generated-PDFs-One..Four (PDF1..PDF4)
    'generated-pdfs' (private) -> pdfs/{project_id}/{project_id}.pdf
    'previews'       (private) -> previews/{project_id}/page1.jpg
  Subject projects (ASCI ACOM AART OSCI OCOM OART)
    'reference-pdfs' (private, 25MB limit)
        -> {subject_code}/{file_type}/{file_name}
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PDF_BUCKET = "generated-pdfs"
PREVIEW_BUCKET = "previews"
REFERENCE_BUCKET = "reference-pdfs"

PDF_CONTENT_TYPE = "application/pdf"
PREVIEW_CONTENT_TYPE = "image/jpeg"

MAX_TEXTBOOKS = 6
MAX_MARKING_GUIDES = 1

# Retention for generated artefacts is 24 hours (enforced by Module 11 cleanup).
RETENTION_HOURS = 24
# Storage threshold is 450 MB per Generated-PDFs project (enforced by Module 7).
STORAGE_THRESHOLD_MB = 450


def _error_message(response: object) -> str | None:
    """Extract an error message from a supabase-py storage response, if any."""
    if response is None:
        return None
    error = None
    if isinstance(response, dict):
        error = response.get("error")
    else:
        error = getattr(response, "error", None)
    if not error:
        return None
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)


class StorageManager:
    """Storage gateway for the Opus Zim automation pipeline."""

    def __init__(self, supabase_router):
        """Store the Module 7 router used to resolve the active PDF project."""
        self.router = supabase_router

    # ------------------------------------------------------------------ upload

    def upload_pdf(self, pdf_bytes: bytes, project_id: str, filename: str) -> tuple[str, int]:
        """Upload the finished PDF to the active Generated-PDFs project.

        ``filename`` is only the human-facing download name (used by the email
        and by signed-URL downloads); it is never part of the object path.

        Returns:
            ``(storage_path, pdf_storage_db_number)``.

        Raises:
            RuntimeError: when the storage upload fails.
        """
        # Call the client getter FIRST so any 450 MB rotation is applied before
        # we read the active db number.
        client = self.router.get_pdf_storage_client()
        db_num = self.router.get_pdf_storage_db_number()

        path = f"pdfs/{project_id}/{project_id}.pdf"
        size_mb = round(len(pdf_bytes) / (1024 * 1024), 2)

        try:
            response = client.storage.from_(PDF_BUCKET).upload(
                path=path,
                file=pdf_bytes,
                file_options={"content-type": PDF_CONTENT_TYPE, "upsert": "true"},
            )
        except Exception as exc:  # noqa: BLE001 - surface storage SDK failures
            raise RuntimeError(f"PDF upload failed for {path} on PDF{db_num}: {exc}") from exc

        message = _error_message(response)
        if message:
            raise RuntimeError(f"PDF upload failed for {path} on PDF{db_num}: {message}")

        logger.info("✅ Uploaded PDF to PDF%s: %s (%s MB)", db_num, path, size_mb)
        logger.debug(
            "upload_pdf completed at %s (download name: %s)",
            datetime.now(timezone.utc).isoformat(),
            filename,
        )
        return path, db_num

    def upload_preview(self, img_bytes: bytes, project_id: str) -> str:
        """Upload the page-1 JPEG preview to the active Generated-PDFs project.

        A preview failure is NOT fatal to the pipeline: this method raises
        ``RuntimeError`` and Module 11's ``main.py`` catches it and continues
        without a preview path.

        Returns:
            The ``previews`` bucket object path.
        """
        client = self.router.get_pdf_storage_client()
        db_num = self.router.get_pdf_storage_db_number()

        path = f"previews/{project_id}/page1.jpg"
        size_kb = round(len(img_bytes) / 1024, 1)

        try:
            response = client.storage.from_(PREVIEW_BUCKET).upload(
                path=path,
                file=img_bytes,
                file_options={"content-type": PREVIEW_CONTENT_TYPE, "upsert": "true"},
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Preview upload failed for {path} on PDF{db_num}: {exc}") from exc

        message = _error_message(response)
        if message:
            raise RuntimeError(f"Preview upload failed for {path} on PDF{db_num}: {message}")

        logger.info("✅ Uploaded preview to PDF%s: %s (%s KB)", db_num, path, size_kb)
        return path

    # ------------------------------------------------------------------ access

    def get_signed_url(
        self,
        path: str,
        db_num: int,
        expires_in: int = 3600,
        bucket: str = PDF_BUCKET,
    ) -> str:
        """Create a time-limited signed URL for an object on PDF{db_num}.

        Raises:
            RuntimeError: when the signed URL cannot be created or is absent.
        """
        client = self.router.get_pdf_client_by_number(db_num)

        try:
            resp = client.storage.from_(bucket).create_signed_url(path, expires_in)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Signed URL failed for {bucket}/{path} on PDF{db_num}: {exc}"
            ) from exc

        message = _error_message(resp)
        if message:
            raise RuntimeError(
                f"Signed URL failed for {bucket}/{path} on PDF{db_num}: {message}"
            )

        url = None
        if isinstance(resp, dict):
            url = resp.get("signedURL") or resp.get("signedUrl")
        else:
            url = getattr(resp, "signedURL", None) or getattr(resp, "signedUrl", None)

        if not url:
            raise RuntimeError(
                f"Signed URL response for {bucket}/{path} on PDF{db_num} "
                "contained neither 'signedURL' nor 'signedUrl'"
            )

        logger.info("🔗 Signed URL created for PDF%s: %s (%ss)", db_num, path, expires_in)
        return str(url)

    def download_pdf(self, path: str, db_num: int) -> bytes:
        """Download a generated PDF object from PDF{db_num}.

        Raises:
            RuntimeError: when the download fails or returns no bytes.
        """
        client = self.router.get_pdf_client_by_number(db_num)

        try:
            data = client.storage.from_(PDF_BUCKET).download(path)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"PDF download failed for {path} on PDF{db_num}: {exc}") from exc

        if not data:
            raise RuntimeError(f"PDF download returned no bytes for {path} on PDF{db_num}")

        logger.info(
            "⬇️  Downloaded PDF from PDF%s: %s (%s MB)",
            db_num,
            path,
            round(len(data) / (1024 * 1024), 2),
        )
        return bytes(data)

    # ------------------------------------------------------------------ delete

    def delete_pdf(self, path: str, db_num: int) -> bool:
        """Delete a generated PDF object and its matching preview object.

        Storage objects only — ``generated_files`` rows are owned by Module 11.
        A missing preview is not an error. Never raises, so the 24-hour cleanup
        workflow can continue over the remaining rows.

        Returns:
            ``True`` when the PDF object was removed, ``False`` on failure.
        """
        try:
            client = self.router.get_pdf_client_by_number(db_num)
            response = client.storage.from_(PDF_BUCKET).remove([path])
            message = _error_message(response)
            if message:
                logger.error("❌ Failed to delete %s on PDF%s: %s", path, db_num, message)
                return False
        except Exception as exc:  # noqa: BLE001
            logger.error("❌ Failed to delete %s on PDF%s: %s", path, db_num, exc)
            return False

        logger.info("🗑️  Deleted PDF on PDF%s: %s", db_num, path)

        # Derive the preview path: pdfs/{project_id}/{project_id}.pdf
        parts = path.split("/")
        project_id = parts[1] if len(parts) >= 2 else None
        if project_id:
            preview_path = f"previews/{project_id}/page1.jpg"
            try:
                preview_resp = client.storage.from_(PREVIEW_BUCKET).remove([preview_path])
                preview_error = _error_message(preview_resp)
                if preview_error:
                    logger.warning(
                        "⚠️  Preview %s not removed on PDF%s: %s",
                        preview_path,
                        db_num,
                        preview_error,
                    )
                else:
                    logger.info("🗑️  Deleted preview on PDF%s: %s", db_num, preview_path)
            except Exception as exc:  # noqa: BLE001 - a missing preview is fine
                logger.warning(
                    "⚠️  Preview %s not removed on PDF%s: %s", preview_path, db_num, exc
                )
        else:
            logger.warning("⚠️  Could not derive preview path from %s", path)

        return True

    # -------------------------------------------------------------- references

    def download_reference_files(
        self,
        subject_client,
        subject_code: str,
        dest_dir: str,
    ) -> tuple[list[str], str | None]:
        """Download a subject's textbooks and marking guide to ``dest_dir``.

        Caps: at most 6 ``textbook`` files and at most 1 ``marking_guide``
        (the most recently created wins).

        Returns:
            ``(textbook_paths, marking_guide_path_or_None)`` as absolute local
            file paths.

        Raises:
            RuntimeError: when zero textbooks could be downloaded, because
            Gemini cannot generate a project without reference material.
        """
        os.makedirs(dest_dir, exist_ok=True)

        try:
            result = (
                subject_client.table("reference_files")
                .select("id, file_type, file_name, storage_path, created_at")
                .eq("subject_code", subject_code)
                .order("created_at", desc=True)
                .execute()
            )
            rows = result.data or []
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Could not query reference_files for {subject_code}: {exc}"
            ) from exc

        textbook_rows = [r for r in rows if r.get("file_type") == "textbook"]
        guide_rows = [r for r in rows if r.get("file_type") == "marking_guide"]

        if len(textbook_rows) > MAX_TEXTBOOKS:
            logger.warning(
                "⚠️  %s has %s textbooks; only the %s most recent will be used",
                subject_code,
                len(textbook_rows),
                MAX_TEXTBOOKS,
            )
            textbook_rows = textbook_rows[:MAX_TEXTBOOKS]

        if len(guide_rows) > MAX_MARKING_GUIDES:
            logger.warning(
                "⚠️  %s has %s marking guides; using the most recent one",
                subject_code,
                len(guide_rows),
            )
        guide_rows = guide_rows[:MAX_MARKING_GUIDES]

        def _fetch(row: dict) -> str | None:
            storage_path = row.get("storage_path")
            file_name = row.get("file_name")
            if not storage_path or not file_name:
                logger.warning("⚠️  Skipping reference row with missing path/name: %s", row.get("id"))
                return None
            try:
                data = subject_client.storage.from_(REFERENCE_BUCKET).download(storage_path)
            except Exception as exc:  # noqa: BLE001 - skip a single bad object
                logger.warning("⚠️  Could not download %s for %s: %s", storage_path, subject_code, exc)
                return None
            if not data:
                logger.warning("⚠️  Empty reference object %s for %s", storage_path, subject_code)
                return None
            local_path = os.path.abspath(os.path.join(dest_dir, file_name))
            try:
                with open(local_path, "wb") as handle:
                    handle.write(data)
            except OSError as exc:
                logger.warning("⚠️  Could not write %s: %s", local_path, exc)
                return None
            logger.info(
                "⬇️  Reference downloaded: %s (%s KB)", file_name, round(len(data) / 1024, 1)
            )
            return local_path

        textbook_paths: list[str] = []
        for row in textbook_rows:
            local = _fetch(row)
            if local:
                textbook_paths.append(local)

        marking_guide_path: str | None = None
        for row in guide_rows:
            marking_guide_path = _fetch(row)

        if not textbook_paths:
            raise RuntimeError(
                f"No textbooks downloaded for subject {subject_code}: "
                "Gemini cannot generate without reference files"
            )

        logger.info(
            "✅ %s references ready: %s textbook(s), marking guide: %s",
            subject_code,
            len(textbook_paths),
            "yes" if marking_guide_path else "no",
        )
        return textbook_paths, marking_guide_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    import sys

    try:
        from supabase_router import SupabaseRouter  # type: ignore
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            from supabase_router import SupabaseRouter  # type: ignore
        except ImportError as exc:
            print(f"❌ Module 7 supabase_router.py not importable: {exc}")
            raise SystemExit(1) from exc

    router = SupabaseRouter()
    manager = StorageManager(router)
    # Touch the client getter first so rotation is applied before reading state.
    router.get_pdf_storage_client()
    active = router.get_pdf_storage_db_number()
    usage_mb = router.check_pdf_storage_usage(active)
    print(f"Active PDF storage project : PDF{active}")
    print(f"Usage                      : {round(float(usage_mb), 2)} MB / {STORAGE_THRESHOLD_MB} MB")
    print(f"Checked at                 : {datetime.now(timezone.utc).isoformat()}")
    print(f"StorageManager ready       : {manager is not None}")
