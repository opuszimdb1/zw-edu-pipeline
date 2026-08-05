# FILE: automation/scripts/ai_client.py
"""Gemini content generation client for the Opus Zim automation pipeline.

Owns the multi-key fallback logic, PDF reference uploads and the raw text
return contract consumed by ``response_parser.parse_response``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import google.generativeai as genai

logger = logging.getLogger(__name__)

PRIMARY_MODEL = "gemini-2.0-flash"
FALLBACK_MODEL = "gemini-1.5-flash"

MAX_TEXTBOOKS = 6
MAX_ATTEMPTS_PER_KEY = 2
ATTEMPT_SLEEP_SECONDS = 5
UPLOAD_POLL_SECONDS = 2
UPLOAD_TIMEOUT_SECONDS = 60

GENERATION_CONFIG: dict[str, Any] = {
    "temperature": 0.7,
    "top_p": 0.95,
    "max_output_tokens": 8192,
}

QUOTA_MARKERS = ("quota", "429", "exhausted", "resource_exhausted")
MODEL_MISSING_MARKERS = ("not found", "not supported", "unsupported", "404")


def _is_quota_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in QUOTA_MARKERS)


def _is_model_missing_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in MODEL_MISSING_MARKERS)


class GeminiClient:
    """Generates raw delimiter-formatted project text using Gemini."""

    def __init__(self, api_key_manager):
        self.api_key_manager = api_key_manager

    # ------------------------------------------------------------------ public
    def generate_project(
        self,
        subject: str,
        level: str,
        project_title: str,
        textbook_paths: list[str],
        marking_guide_path: str | None,
        gemini_master_prompt: str,
    ) -> str:
        """Return the RAW Gemini response text for one project.

        Raises:
            RuntimeError: when every available API key fails, or when the
                response does not contain ``@@STAGE_START:1@@``.
        """
        prompt = self._build_prompt(gemini_master_prompt, subject, level, project_title)
        reference_paths = self._collect_reference_paths(textbook_paths, marking_guide_path)

        keys = self.api_key_manager.get_all_gemini_keys() or []
        if not keys:
            raise RuntimeError("No active Gemini API keys are available")

        uploaded_files: list[Any] = []
        tried = 0
        try:
            for key in keys:
                tried += 1
                key_id = key.get("id")
                api_key = key.get("api_key")
                logger.info("Attempting Gemini generation with key id=%s", key_id)
                try:
                    genai.configure(api_key=api_key)
                except Exception as exc:  # noqa: BLE001 - vendor lib raises broadly
                    logger.warning("Key id=%s could not be configured: %s", key_id, exc)
                    self.api_key_manager.mark_key_error(key_id)
                    continue

                if reference_paths and not uploaded_files:
                    uploaded_files = self._upload_references(reference_paths)

                text = self._generate_with_key(key_id, prompt, uploaded_files)
                if text is None:
                    continue

                if "@@STAGE_START:1@@" not in text:
                    raise RuntimeError(
                        "Gemini response did not contain @@STAGE_START:1@@"
                    )
                logger.info("Gemini generation succeeded with key id=%s", key_id)
                return text

            raise RuntimeError(
                f"Gemini generation failed after trying {tried} API key(s)"
            )
        finally:
            self._cleanup_uploads(uploaded_files)

    # ----------------------------------------------------------------- helpers
    def _build_prompt(
        self,
        gemini_master_prompt: str,
        subject: str,
        level: str,
        project_title: str,
    ) -> str:
        return (
            gemini_master_prompt.replace("{{SUBJECT}}", subject)
            .replace("{{LEVEL}}", level)
            .replace("{{PROJECT_TITLE}}", project_title)
        )

    def _collect_reference_paths(
        self, textbook_paths: list[str], marking_guide_path: str | None
    ) -> list[str]:
        books = list(textbook_paths or [])
        if len(books) > MAX_TEXTBOOKS:
            logger.warning(
                "Received %d textbooks; truncating to the first %d",
                len(books),
                MAX_TEXTBOOKS,
            )
            books = books[:MAX_TEXTBOOKS]
        if marking_guide_path:
            books.append(marking_guide_path)
        return books

    def _upload_references(self, paths: list[str]) -> list[Any]:
        uploaded: list[Any] = []
        for path in paths:
            try:
                handle = genai.upload_file(path=path, mime_type="application/pdf")
            except Exception as exc:  # noqa: BLE001 - vendor lib raises broadly
                logger.warning("Upload failed for %s: %s", path, exc)
                continue

            ready = self._wait_for_file(handle)
            if ready is None:
                continue
            uploaded.append(ready)
        logger.info("Uploaded %d/%d reference file(s)", len(uploaded), len(paths))
        return uploaded

    def _wait_for_file(self, handle: Any) -> Any | None:
        waited = 0
        current = handle
        while waited < UPLOAD_TIMEOUT_SECONDS:
            state = str(getattr(getattr(current, "state", None), "name", current and getattr(current, "state", "")))
            if state.upper().endswith("FAILED"):
                logger.warning(
                    "Uploaded file %s ended in FAILED state; skipping",
                    getattr(current, "name", "unknown"),
                )
                return None
            if "PROCESSING" not in state.upper():
                return current
            time.sleep(UPLOAD_POLL_SECONDS)
            waited += UPLOAD_POLL_SECONDS
            try:
                current = genai.get_file(getattr(current, "name", ""))
            except Exception as exc:  # noqa: BLE001 - vendor lib raises broadly
                logger.warning("Could not refresh uploaded file state: %s", exc)
                return None
        logger.warning(
            "Uploaded file %s still PROCESSING after %ds; skipping",
            getattr(current, "name", "unknown"),
            UPLOAD_TIMEOUT_SECONDS,
        )
        return None

    def _generate_with_key(
        self, key_id: Any, prompt: str, uploaded_files: list[Any]
    ) -> str | None:
        contents: list[Any] = list(uploaded_files) + [prompt]
        model_name = PRIMARY_MODEL

        for attempt in range(1, MAX_ATTEMPTS_PER_KEY + 1):
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(
                    contents, generation_config=GENERATION_CONFIG
                )
                text = (getattr(response, "text", "") or "").strip()
                if not text:
                    raise RuntimeError("Gemini returned an empty response")
                return text
            except Exception as exc:  # noqa: BLE001 - vendor lib raises broadly
                message = str(exc)
                if _is_quota_error(message):
                    logger.warning(
                        "Key id=%s hit a quota/rate limit; moving to next key",
                        key_id,
                    )
                    self.api_key_manager.mark_key_error(key_id)
                    return None
                if model_name == PRIMARY_MODEL and _is_model_missing_error(message):
                    logger.warning(
                        "Model %s unavailable; falling back to %s",
                        PRIMARY_MODEL,
                        FALLBACK_MODEL,
                    )
                    model_name = FALLBACK_MODEL
                    continue
                logger.warning(
                    "Generation attempt %d/%d failed for key id=%s: %s",
                    attempt,
                    MAX_ATTEMPTS_PER_KEY,
                    key_id,
                    message,
                )
                if attempt < MAX_ATTEMPTS_PER_KEY:
                    time.sleep(ATTEMPT_SLEEP_SECONDS)

        self.api_key_manager.mark_key_error(key_id)
        return None

    def _cleanup_uploads(self, uploaded_files: list[Any]) -> None:
        for handle in uploaded_files:
            name = getattr(handle, "name", None)
            if not name:
                continue
            try:
                genai.delete_file(name)
            except Exception as exc:  # noqa: BLE001 - vendor lib raises broadly
                logger.warning("Could not delete uploaded file %s: %s", name, exc)
