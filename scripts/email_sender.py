# FILE: automation/scripts/email_sender.py
"""Opus Zim — Module 10: Resend email sender.

Delivers the finished project PDF as an attachment through the Resend HTTP API
(``requests`` only — the ``resend`` SDK is deliberately not used).

This module never writes to ``generated_files``; Module 11's ``main.py`` records
``email_sent`` / ``email_sent_at``.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 3          # initial attempt + 2 retries
RETRY_SLEEP_SECONDS = 5
TEMPLATE_ID = "opuszim-project-delivery"

# Brand palette
COLOR_PRIMARY = "#2E5EAA"
COLOR_SECONDARY = "#4CC9C0"
COLOR_DARK = "#2B2D42"
COLOR_LIGHT = "#F5F7FA"

TAGLINE = "Empowering learners for quality results"
DISCLAIMER = "Generated projects are intended for reference and learning purposes only."
RETENTION_HOURS = 24


def _mask_email(address: str) -> str:
    """Mask a recipient address for logging: ``j***@example.com``."""
    if not address or "@" not in address:
        return "***"
    local, _, domain = address.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


class EmailSender:
    """Sends the branded Opus Zim delivery email with the PDF attached."""

    def __init__(self, resend_api_key: str, from_email: str):
        """Store credentials. The API key is never logged."""
        self.resend_api_key = resend_api_key
        self.from_email = from_email

    # ---------------------------------------------------------------- rendering

    def _build_subject(self, project_title: str) -> str:
        return f"Your Opus Zim Project: {project_title}"

    def _build_html(self, project_title: str, pdf_filename: str) -> str:
        """Render the inline-styled, mobile-first, single-column HTML body."""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{self._build_subject(project_title)}</title>
</head>
<body style="margin:0; padding:0; background-color:{COLOR_LIGHT}; color:{COLOR_DARK}; font-family:Arial, Helvetica, sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{COLOR_LIGHT}; padding:16px 0;">
    <tr>
      <td align="center" style="padding:0 12px;">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%; max-width:600px; background-color:#FFFFFF; border-radius:8px; overflow:hidden;">
          <tr>
            <td style="background-color:{COLOR_PRIMARY}; padding:24px;" align="left">
              <div style="font-size:26px; font-weight:bold; color:#FFFFFF; letter-spacing:0.5px;">Opus Zim</div>
              <div style="font-size:13px; color:#DCE7F7; padding-top:6px;">{TAGLINE}</div>
            </td>
          </tr>
          <tr>
            <td style="height:4px; background-color:{COLOR_SECONDARY}; line-height:4px; font-size:0;">&nbsp;</td>
          </tr>
          <tr>
            <td style="padding:24px;">
              <p style="margin:0 0 16px 0; font-size:16px; line-height:1.5; color:{COLOR_DARK};">Hello,</p>
              <p style="margin:0 0 16px 0; font-size:16px; line-height:1.5; color:{COLOR_DARK};">
                Your project is ready:
              </p>
              <p style="margin:0 0 20px 0; font-size:18px; font-weight:bold; line-height:1.4; color:{COLOR_PRIMARY};">
                {project_title}
              </p>
              <p style="margin:0 0 20px 0; font-size:16px; line-height:1.5; color:{COLOR_DARK};">
                The completed PDF is attached to this email as
                <strong>{pdf_filename}</strong>.
              </p>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{COLOR_LIGHT}; border-left:4px solid {COLOR_SECONDARY}; border-radius:4px;">
                <tr>
                  <td style="padding:14px 16px; font-size:15px; line-height:1.5; color:{COLOR_DARK};">
                    &#9888;&#65039; This file will be deleted from our servers
                    {RETENTION_HOURS} hours after generation. Please save it to
                    your device now.
                  </td>
                </tr>
              </table>
              <p style="margin:20px 0 0 0; font-size:13px; line-height:1.5; color:#5A5F73;">
                {DISCLAIMER}
              </p>
            </td>
          </tr>
          <tr>
            <td style="background-color:{COLOR_DARK}; padding:16px 24px; font-size:12px; line-height:1.5; color:#C9CDDA;" align="center">
              Opus Zim &mdash; {TAGLINE}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    def _build_text(self, project_title: str, pdf_filename: str) -> str:
        """Plaintext fallback body."""
        return (
            f"Opus Zim — {TAGLINE}\n\n"
            "Hello,\n\n"
            f"Your project is ready: {project_title}\n\n"
            f"The completed PDF is attached to this email as {pdf_filename}.\n\n"
            f"WARNING: This file will be deleted from our servers {RETENTION_HOURS} "
            "hours after generation. Please save it to your device now.\n\n"
            f"{DISCLAIMER}\n"
        )

    # ------------------------------------------------------------------ sending

    def send_pdf_email(
        self,
        to_email: str,
        project_title: str,
        pdf_bytes: bytes,
        pdf_filename: str,
    ) -> bool:
        """Send the delivery email with the PDF attached.

        Retries twice with a 5 second sleep on HTTP 429 or 5xx. Other 4xx
        responses are not retried.

        Returns:
            ``True`` on a 200/201 response, ``False`` otherwise.
        """
        masked = _mask_email(to_email)
        payload = {
            "from": self.from_email,
            "to": [to_email],
            "subject": self._build_subject(project_title),
            "html": self._build_html(project_title, pdf_filename),
            "text": self._build_text(project_title, pdf_filename),
            "attachments": [
                {
                    "filename": pdf_filename,
                    "content": base64.b64encode(pdf_bytes).decode(),
                }
            ],
            "tags": [{"name": "template", "value": TEMPLATE_ID}],
        }
        headers = {
            "Authorization": f"Bearer {self.resend_api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = requests.post(
                    RESEND_ENDPOINT,
                    json=payload,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "⚠️  Resend request error for %s (attempt %s/%s): %s",
                    masked,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                )
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_SLEEP_SECONDS)
                    continue
                return False

            status = response.status_code
            if status in (200, 201):
                logger.info(
                    "✅ Email sent to %s at %s",
                    masked,
                    datetime.now(timezone.utc).isoformat(),
                )
                return True

            body = (response.text or "")[:500]
            retryable = status == 429 or 500 <= status < 600
            logger.error(
                "❌ Resend returned %s for %s (attempt %s/%s): %s",
                status,
                masked,
                attempt,
                MAX_ATTEMPTS,
                body,
            )
            if retryable and attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
                continue
            return False

        return False


def _dummy_pdf() -> bytes:
    """Build a minimal valid one-page PDF for the send test."""
    objects = [
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        "4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    stream = "BT /F1 18 Tf 72 760 Td (Opus Zim test document) Tj ET"
    objects.append(
        f"5 0 obj\n<< /Length {len(stream)} >>\nstream\n{stream}\nendstream\nendobj\n"
    )

    out = "%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(out))
        out += obj
    xref_start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n"
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    )
    return out.encode("latin-1")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    api_key = os.environ.get("RESEND_API_KEY", "")
    from_email = os.environ.get("RESEND_FROM_EMAIL", "noreply@example.com")
    sample_title = "The Impact of Soil pH on Maize Germination"
    sample_filename = "opuszim-project.pdf"

    sender = EmailSender(api_key, from_email)
    html = sender._build_html(sample_title, sample_filename)
    text = sender._build_text(sample_title, sample_filename)

    print(f"Subject      : {sender._build_subject(sample_title)}")
    print(f"From         : {from_email}")
    print(f"Template tag : {TEMPLATE_ID}")
    print(f"HTML length  : {len(html)} characters")
    print(f"Text length  : {len(text)} characters")
    print(f"Rendered at  : {datetime.now(timezone.utc).isoformat()}")

    test_to = os.environ.get("OPUSZIM_EMAIL_TEST_TO")
    if test_to:
        if not api_key:
            print("❌ RESEND_API_KEY is not set; cannot send the test email")
            raise SystemExit(1)
        print(f"Sending test email to {_mask_email(test_to)} ...")
        ok = sender.send_pdf_email(test_to, sample_title, _dummy_pdf(), sample_filename)
        print("✅ Test email sent" if ok else "❌ Test email failed")
        raise SystemExit(0 if ok else 1)

    print("✅ Render-only mode complete (set OPUSZIM_EMAIL_TEST_TO to send)")
