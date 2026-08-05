[//]: # (FILE: automation/README.md)

# Opus Zim — Automation Repository

This repository is the compute layer of the Opus Zim system. It receives
`repository_dispatch` events from the main site, generates a complete student
project as a PDF using Gemini (text) and Kie (images), stores the PDF in one of
four Supabase Generated-PDFs projects, emails it to the student via Resend, and
deletes it again within 24 hours.

## ⚠️ This repository is PUBLIC

**No secret may ever be committed to this repository.** Every credential —
Supabase URLs and keys, Resend keys, site URLs, AI keys — lives in
**GitHub Secrets** (`Settings → Secrets and variables → Actions`) and is injected
into the workflow `env:` at run time. Never write a key into a script, a YAML
file, a README, or a commit message. If a key is ever committed, rotate it
immediately.

## Workflows

| Workflow | File | Trigger |
| --- | --- | --- |
| **Generate PDF** | `.github/workflows/generate_pdf.yml` | `repository_dispatch` type `generate-pdf`, plus `workflow_dispatch` for manual re-runs. Timeout 30 min, concurrency keyed on `project_id`. |
| **Cleanup expired PDFs** | `.github/workflows/cleanup_pdfs.yml` | `schedule` cron `0 */6 * * *` (every 6 hours), plus `workflow_dispatch`. Timeout 20 min. |
| **Delete PDF after email** | `.github/workflows/delete_on_download.yml` | `repository_dispatch` type `delete-pdf-after-email`, plus `workflow_dispatch`. Timeout 10 min. |

All three jobs run on `ubuntu-latest` with Python 3.11 and install
`scripts/requirements.txt` with pip caching. Run steps use
`working-directory: scripts`.

## Script inventory

| Script | Description |
| --- | --- |
| `scripts/main.py` | The 10-step generation entrypoint: resolve subject, read master prompts, download references, call Gemini, parse, generate images, build the PDF, upload, email, mark the project completed and lock the title. |
| `scripts/cleanup.py` | Scheduled sweep over PDF projects 1–4 that deletes every `generated_files` row whose `scheduled_deletion_at` has passed and reports storage usage. |
| `scripts/delete_pdf.py` | Deletes a single PDF object on demand, marks the file row deleted and advances the project to `downloaded`. |
| `scripts/requirements.txt` | Pinned Python dependencies shared by every script. |
| `scripts/supabase_client.py` | Multi-project Supabase router and `system_config` access. |
| `scripts/api_key_manager.py` | Gemini/Kie API key rotation and health tracking. |
| `scripts/ai_client.py` | Gemini project-content generation. |
| `scripts/image_generator.py` | Kie image generation. |
| `scripts/response_parser.py` | Parses the raw AI response into structured stage data. |
| `scripts/pdf_generator.py` | Renders the structured data into a branded PDF. |
| `scripts/pdf_preview.py` | Renders a preview image of page one. |
| `scripts/storage_manager.py` | Upload/download/delete of PDFs, previews and reference files. |
| `scripts/email_sender.py` | Resend delivery of the finished PDF. |

## Re-running a single project manually

1. Open the **Actions** tab.
2. Select **Generate PDF** in the left sidebar.
3. Click **Run workflow**.
4. Fill in `project_id`, `user_id`, `subject_code`, `level`, `project_title` and
   `user_email` exactly as they appear on the `projects` row.
5. Click **Run workflow** and follow the numbered `STEP n/10` log lines.

The same pattern works for **Cleanup expired PDFs** (no inputs) and
**Delete PDF after email** (`project_id`, `storage_path`, `db_num`).

## 24-hour retention policy

Every generated PDF is written with `scheduled_deletion_at = now + 24 hours`.
The cleanup workflow runs every 6 hours and permanently removes any object past
that deadline, setting `deleted_at` on the `generated_files` row. A PDF may also
be removed earlier by the delete workflow once the student has received it.
Nothing is kept beyond 24 hours — students must save the copy emailed to them.
