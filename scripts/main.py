# FILE: automation/scripts/main.py
"""Opus Zim — main generation entrypoint.

Reads its payload from environment variables only, runs the full 10-step
generation pipeline and persists the result. Designed to be invoked by the
`generate_pdf.yml` GitHub Actions workflow with working-directory `scripts`.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# Allow `python main.py` from any working directory.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from supabase_client import SupabaseRouter  # noqa: E402
from api_key_manager import APIKeyManager  # noqa: E402
from ai_client import GeminiClient  # noqa: E402
from image_generator import KieImageGenerator  # noqa: E402
from response_parser import parse_response  # noqa: E402
from pdf_generator import OpusZimPDFGenerator  # noqa: E402
from pdf_preview import generate_preview  # noqa: E402
from storage_manager import StorageManager  # noqa: E402
from email_sender import EmailSender  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("main")

RETENTION_HOURS = 24

REQUIRED_VARS = (
    "PROJECT_ID",
    "USER_ID",
    "SUBJECT_CODE",
    "LEVEL",
    "PROJECT_TITLE",
    "USER_EMAIL",
)

IMAGE_TYPE_BY_FAMILY = {
    "MATH": "diagram/graph",
    "PHYS": "diagram/graph",
    "CHEM": "diagram/graph",
    "BIO": "diagram/graph",
    "GEOG": "map/chart",
    "COMP": "flowchart/diagram",
    "AGRI": "flowchart/diagram",
    "HIST": "illustration",
    "TTD": "illustration",
}


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat()


def mask_email(email: str) -> str:
    """Mask an email address so it is safe to log."""
    if not email or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def safe_slug(text: str, max_length: int = 60) -> str:
    """Filesystem-safe slug: alphanumerics and underscores only."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug:
        slug = "project"
    return slug[:max_length].strip("_") or "project"


def read_payload() -> dict[str, str]:
    """Read and validate the six payload environment variables."""
    payload = {name: (os.environ.get(name) or "").strip() for name in REQUIRED_VARS}
    missing = [name for name in REQUIRED_VARS if not payload[name]]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)
    return payload


def resolve_logo_path() -> Optional[str]:
    """Resolve the Opus Zim logo path, or None when unavailable."""
    env_path = os.environ.get("OPUSZIM_LOGO_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path
    default_path = os.path.join(_SCRIPT_DIR, "assets", "opuszim-logo.png")
    if os.path.isfile(default_path):
        return default_path
    return None


def image_type_for(subject_code: str) -> str:
    """Derive the image type descriptor from the subject family."""
    code = (subject_code or "").upper()
    for family, image_type in IMAGE_TYPE_BY_FAMILY.items():
        if code.startswith(family):
            return image_type
    return "diagram/graph"


def fetch_subject_name(subject_client: Any, subject_code: str, level: str) -> str:
    """Read subject_name from the subjects table, falling back to the code."""
    try:
        result = (
            subject_client.table("subjects")
            .select("subject_name")
            .eq("subject_code", subject_code)
            .eq("level", level)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if rows and rows[0].get("subject_name"):
            return str(rows[0]["subject_name"])
    except Exception as exc:  # noqa: BLE001 - non fatal lookup
        logger.warning("Could not read subjects row for %s: %s", subject_code, exc)
    fallback = subject_code.replace("_", " ").replace("-", " ").title()
    logger.warning("subjects row missing for %s — using fallback name '%s'", subject_code, fallback)
    return fallback


def fetch_master_prompt(subject_client: Any, subject_code: str, provider: str) -> Optional[str]:
    """Read a master prompt for the given provider from the subject project."""
    result = (
        subject_client.table("master_prompts")
        .select("prompt_text")
        .eq("subject_code", subject_code)
        .eq("ai_provider", provider)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if rows and rows[0].get("prompt_text"):
        return str(rows[0]["prompt_text"])
    return None


def set_project_status(users_client: Any, project_id: str, values: dict[str, Any]) -> None:
    """Update a projects row on the active Users-Accounts project."""
    users_client.table("projects").update(values).eq("id", project_id).execute()


def lock_title(project_id: str, title_id: Any) -> None:
    """Mark a title as taken in BOTH Users-Accounts projects."""
    from supabase import create_client

    timestamp = now_iso()
    for prefix in ("USERS1", "USERS2"):
        url = os.environ.get(f"SB_{prefix}_URL")
        key = os.environ.get(f"SB_{prefix}_SERVICE_KEY")
        if not url or not key:
            logger.warning("Skipping title lock on %s — credentials not configured", prefix)
            continue
        try:
            client = create_client(url, key)
            client.table("available_titles").update(
                {
                    "is_available": False,
                    "locked_by": project_id,
                    "locked_at": timestamp,
                }
            ).eq("id", title_id).execute()
            logger.info("Locked title %s on %s", title_id, prefix)
        except Exception as exc:  # noqa: BLE001 - non fatal bookkeeping
            logger.warning("Could not lock title %s on %s: %s", title_id, prefix, exc)


def generate_images(
    subject_client: Any,
    api_key_manager: APIKeyManager,
    data: dict[str, Any],
    payload: dict[str, str],
    subject_name: str,
    temp_dir: str,
) -> list[str]:
    """Best-effort image generation; returns an empty list on any failure."""
    image_paths: list[str] = []
    try:
        stage5 = data.get("stage5") or {}
        if not stage5.get("needs_images"):
            logger.info("STEP 8/10 no images required for this project")
            return []

        kie_prompt = fetch_master_prompt(subject_client, payload["SUBJECT_CODE"], "kie")
        if not kie_prompt:
            logger.warning("STEP 8/10 no KIE master prompt found — skipping images")
            return []

        specific_prompts = stage5.get("image_prompts") or stage5.get("prompts") or []
        if isinstance(specific_prompts, str):
            specific_prompts = [specific_prompts]
        specific_prompts = [str(p) for p in specific_prompts if p][:2]
        if not specific_prompts:
            logger.warning("STEP 8/10 needs_images was set but no prompts supplied")
            return []

        generator = KieImageGenerator(api_key_manager)
        image_type = image_type_for(payload["SUBJECT_CODE"])

        for index, specific in enumerate(specific_prompts, start=1):
            full_prompt = (
                kie_prompt.replace("{{SUBJECT}}", subject_name)
                .replace("{{LEVEL}}", payload["LEVEL"])
                .replace("{{PROJECT_TITLE}}", payload["PROJECT_TITLE"])
                .replace("{{SPECIFIC_IMAGE_PROMPT}}", specific)
                .replace("{{IMAGE_TYPE}}", image_type)
            )
            image_bytes = generator.generate_image(full_prompt, width=1024, height=768)
            if not image_bytes:
                logger.warning("STEP 8/10 image %s returned no bytes", index)
                continue
            image_path = os.path.join(temp_dir, f"image_{index}.png")
            with open(image_path, "wb") as handle:
                handle.write(image_bytes)
            image_paths.append(image_path)
            logger.info("STEP 8/10 image %s written to %s", index, image_path)

        return image_paths
    except Exception as exc:  # noqa: BLE001 - images are never fatal
        logger.warning("STEP 8/10 image generation failed (continuing without images): %s", exc)
        return []


def run() -> None:
    """Execute the full generation pipeline."""
    payload = read_payload()
    project_id = payload["PROJECT_ID"]
    logger.info(
        "Starting generation for project %s (subject=%s level=%s, email=%s)",
        project_id,
        payload["SUBJECT_CODE"],
        payload["LEVEL"],
        mask_email(payload["USER_EMAIL"]),
    )

    temp_dir: Optional[str] = None
    router: Optional[SupabaseRouter] = None
    users_client: Any = None

    try:
        # STEP 1 — clients
        logger.info("STEP 1/10 building Supabase router and API key manager")
        router = SupabaseRouter()
        users_client = router.get_users_client()
        api_keys_client = router.get_api_keys_client()
        api_key_manager = APIKeyManager(api_keys_client)
        storage = StorageManager(router)
        logger.info("STEP 1/10 active users db: %s", router.get_users_db_number())

        # STEP 2 — mark generating
        logger.info("STEP 2/10 setting project status to 'generating'")
        set_project_status(
            users_client,
            project_id,
            {"status": "generating", "generation_requested_at": now_iso()},
        )

        # STEP 3 — resolve subject
        logger.info("STEP 3/10 resolving subject project")
        subject_client = router.get_subject_client(payload["SUBJECT_CODE"], payload["LEVEL"])
        subject_name = fetch_subject_name(subject_client, payload["SUBJECT_CODE"], payload["LEVEL"])
        logger.info("STEP 3/10 subject resolved: %s", subject_name)

        # STEP 4 — master prompt
        logger.info("STEP 4/10 fetching Gemini master prompt")
        gemini_master_prompt = fetch_master_prompt(subject_client, payload["SUBJECT_CODE"], "gemini")
        if not gemini_master_prompt:
            raise RuntimeError(
                f"No gemini master_prompts row for subject_code={payload['SUBJECT_CODE']}"
            )

        # STEP 5 — reference files
        logger.info("STEP 5/10 downloading reference files")
        temp_dir = tempfile.mkdtemp(prefix="opuszim_")
        textbook_paths, marking_guide_path = storage.download_reference_files(
            subject_client, payload["SUBJECT_CODE"], temp_dir
        )
        logger.info(
            "STEP 5/10 downloaded %s textbook(s), marking guide: %s",
            len(textbook_paths or []),
            "yes" if marking_guide_path else "no",
        )

        # STEP 6 — generation
        logger.info("STEP 6/10 generating project content with Gemini")
        raw = GeminiClient(api_key_manager).generate_project(
            subject_name,
            payload["LEVEL"],
            payload["PROJECT_TITLE"],
            textbook_paths,
            marking_guide_path,
            gemini_master_prompt,
        )

        # STEP 7 — parse
        logger.info("STEP 7/10 parsing AI response")
        data = parse_response(raw)
        data["subject_name"] = subject_name
        data["level"] = payload["LEVEL"]
        data["project_title"] = payload["PROJECT_TITLE"]

        # STEP 8 — images (best effort)
        logger.info("STEP 8/10 generating images (best effort)")
        image_paths = generate_images(
            subject_client, api_key_manager, data, payload, subject_name, temp_dir
        )

        # STEP 9 — build PDF
        logger.info("STEP 9/10 building PDF")
        pdf_bytes = OpusZimPDFGenerator(logo_path=resolve_logo_path()).build(data, image_paths)
        safe_title = safe_slug(payload["PROJECT_TITLE"])
        pdf_filename = f"{safe_title}_project.pdf"
        logger.info("STEP 9/10 built %s (%s bytes)", pdf_filename, len(pdf_bytes))

        # STEP 10 — persist and deliver
        logger.info("STEP 10/10 persisting and delivering")
        path, db_num = storage.upload_pdf(pdf_bytes, project_id, pdf_filename)
        logger.info("STEP 10/10 uploaded PDF to db %s at %s", db_num, path)

        preview_path: Optional[str] = None
        try:
            preview_path = storage.upload_preview(generate_preview(pdf_bytes), project_id)
            logger.info("STEP 10/10 preview uploaded to %s", preview_path)
        except Exception as exc:  # noqa: BLE001 - preview is optional
            logger.warning("STEP 10/10 preview generation/upload failed: %s", exc)

        pdf_client = router.get_pdf_client_by_number(db_num)
        scheduled_deletion_at = (
            datetime.now(timezone.utc) + timedelta(hours=RETENTION_HOURS)
        ).isoformat()
        inserted = (
            pdf_client.table("generated_files")
            .insert(
                {
                    "project_id": project_id,
                    "user_id": payload["USER_ID"],
                    "file_name": pdf_filename,
                    "storage_path": path,
                    "preview_path": preview_path,
                    "pdf_storage_db": db_num,
                    "email_sent": False,
                    "scheduled_deletion_at": scheduled_deletion_at,
                }
            )
            .execute()
        )
        logger.info("STEP 10/10 generated_files row created (deletion at %s)", scheduled_deletion_at)

        from_email = router.get_config("resend_from_email") or os.environ.get("RESEND_FROM_EMAIL")
        email_ok = False
        try:
            email_ok = EmailSender(os.environ.get("RESEND_API_KEY"), from_email).send_pdf_email(
                payload["USER_EMAIL"], payload["PROJECT_TITLE"], pdf_bytes, pdf_filename
            )
        except Exception as exc:  # noqa: BLE001 - email failure must not lose the PDF
            logger.warning("STEP 10/10 email send raised: %s", exc)

        if email_ok:
            logger.info("STEP 10/10 email delivered to %s", mask_email(payload["USER_EMAIL"]))
            update_query = pdf_client.table("generated_files").update(
                {"email_sent": True, "email_sent_at": now_iso()}
            )
            rows = inserted.data or []
            if rows and rows[0].get("id") is not None:
                update_query.eq("id", rows[0]["id"]).execute()
            else:
                update_query.eq("project_id", project_id).execute()
        else:
            logger.warning("STEP 10/10 email was NOT sent for project %s", project_id)

        set_project_status(
            users_client,
            project_id,
            {
                "status": "completed",
                "generation_completed_at": now_iso(),
                "pdf_url": path,
                "preview_url": preview_path,
                "pdf_storage_db": db_num,
                "error_message": None,
            },
        )
        logger.info("STEP 10/10 projects row marked 'completed'")

        try:
            project_row = (
                users_client.table("projects")
                .select("title_id")
                .eq("id", project_id)
                .limit(1)
                .execute()
            )
            rows = project_row.data or []
            title_id = rows[0].get("title_id") if rows else None
            if title_id:
                lock_title(project_id, title_id)
            else:
                logger.info("STEP 10/10 no title_id on project — nothing to lock")
        except Exception as exc:  # noqa: BLE001 - non fatal bookkeeping
            logger.warning("STEP 10/10 title lock step failed: %s", exc)

        logger.info("Generation finished successfully for project %s", project_id)

    except Exception as exc:  # noqa: BLE001 - top level guard
        logger.error("Generation FAILED for project %s: %s", project_id, exc)
        logger.error("%s", traceback.format_exc())
        if users_client is not None:
            try:
                set_project_status(
                    users_client,
                    project_id,
                    {"status": "failed", "error_message": str(exc)[:500]},
                )
            except Exception as inner:  # noqa: BLE001
                logger.error("Could not mark project as failed: %s", inner)
        sys.exit(1)
    finally:
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info("Temp directory removed: %s", temp_dir)


if __name__ == "__main__":
    run()
    sys.exit(0)
