# FILE: automation/scripts/image_generator.py
"""Kie AI image generation client for the Opus Zim automation pipeline.

Images are optional in the pipeline: the caller (Module 11) catches
``RuntimeError`` and continues without figures.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

GENERATE_URL = "https://api.kie.ai/api/v1/gpt4o-image/generate"
RECORD_URL = "https://api.kie.ai/api/v1/gpt4o-image/record-info"

REQUEST_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60
POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 24
MAX_KEY_ATTEMPTS = 3

_COMPLETED_STATES = {"success", "succeeded", "completed", "done", "complete", "1"}
_FAILED_STATES = {"fail", "failed", "error", "canceled", "cancelled", "2", "3"}


class KieImageGenerator:
    """Generates a single figure image and returns raw PNG/JPEG bytes."""

    def __init__(self, api_key_manager):
        self.api_key_manager = api_key_manager

    # ------------------------------------------------------------------ public
    def generate_image(self, prompt: str, width: int = 1024, height: int = 768) -> bytes:
        """Return image bytes for ``prompt``.

        Raises:
            RuntimeError: when all key attempts are exhausted.
        """
        attempts = 0
        seen_key_ids: set[Any] = set()

        while attempts < MAX_KEY_ATTEMPTS:
            key = self.api_key_manager.get_kie_key()
            if not key:
                logger.warning("No usable Kie AI key returned by the key manager")
                break

            key_id = key.get("id")
            api_key = key.get("api_key")
            if key_id in seen_key_ids:
                logger.warning("Key manager returned key id=%s again; stopping", key_id)
                break
            seen_key_ids.add(key_id)
            attempts += 1

            try:
                image_bytes = self._run_once(api_key, key_id, prompt, width, height)
            except _AuthError:
                logger.warning("Key id=%s rejected (401/403); deactivating", key_id)
                self.api_key_manager.deactivate_key(key_id)
                self.api_key_manager.mark_key_error(key_id)
                continue
            except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
                logger.warning("Kie attempt with key id=%s failed: %s", key_id, exc)
                self.api_key_manager.mark_key_error(key_id)
                continue

            self.api_key_manager.mark_kie_success(key_id)
            logger.info(
                "Kie image generated with key id=%s (%d bytes)", key_id, len(image_bytes)
            )
            return image_bytes

        raise RuntimeError(
            f"Kie AI image generation failed after {attempts} key attempts"
        )

    # ----------------------------------------------------------------- helpers
    def _run_once(
        self, api_key: str, key_id: Any, prompt: str, width: int, height: int
    ) -> bytes:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "prompt": prompt,
            "size": f"{width}x{height}",
            "nVariants": 1,
        }

        response = requests.post(
            GENERATE_URL, json=body, headers=headers, timeout=REQUEST_TIMEOUT
        )
        self._raise_for_auth(response.status_code)
        response.raise_for_status()
        task_id = self._extract_task_id(response.json())
        logger.info("Kie task created: key id=%s task=%s", key_id, task_id)

        image_url = self._poll_for_url(headers, task_id)
        download = requests.get(image_url, timeout=DOWNLOAD_TIMEOUT)
        self._raise_for_auth(download.status_code)
        download.raise_for_status()
        if not download.content:
            raise RuntimeError(f"Kie task {task_id} returned an empty image body")
        return download.content

    def _poll_for_url(self, headers: dict[str, str], task_id: str) -> str:
        for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
            time.sleep(POLL_INTERVAL_SECONDS)
            response = requests.get(
                RECORD_URL,
                params={"taskId": task_id},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            self._raise_for_auth(response.status_code)
            response.raise_for_status()
            payload = response.json() or {}
            data = payload.get("data") or {}

            state = str(
                data.get("status")
                or data.get("state")
                or data.get("successFlag")
                or ""
            ).strip().lower()

            if state in _FAILED_STATES:
                raise RuntimeError(f"Kie task {task_id} reported state '{state}'")

            url = self._extract_image_url(data)
            if url and (state in _COMPLETED_STATES or not state):
                logger.info("Kie task %s completed on poll %d", task_id, attempt)
                return url
            if url:
                logger.info("Kie task %s completed on poll %d", task_id, attempt)
                return url

        raise RuntimeError(
            f"Kie task {task_id} did not complete after {MAX_POLL_ATTEMPTS} polls"
        )

    def _extract_task_id(self, payload: dict[str, Any]) -> str:
        data = (payload or {}).get("data") or {}
        task_id = data.get("taskId") or data.get("task_id")
        if not task_id:
            raise RuntimeError("Kie generate response did not include a task id")
        return str(task_id)

    def _extract_image_url(self, data: dict[str, Any]) -> str | None:
        nested = data.get("response") or {}
        candidates = [
            (nested.get("resultUrls") or [None])[0] if isinstance(nested, dict) else None,
            (data.get("resultUrls") or [None])[0],
            data.get("imageUrl"),
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return None

    def _raise_for_auth(self, status_code: int) -> None:
        if status_code in (401, 403):
            raise _AuthError(f"Kie AI returned HTTP {status_code}")


class _AuthError(RuntimeError):
    """Raised internally when a Kie key is rejected with 401/403."""
