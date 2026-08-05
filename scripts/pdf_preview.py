# FILE: automation/scripts/pdf_preview.py
"""Opus Zim — PDF preview generator.

Renders page 1 of a PDF to an 800px-wide watermarked JPEG. Imports nothing
from pdf_generator at module scope so the two modules stay independently
importable.
"""

from __future__ import annotations

import io
import logging

import fitz
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

PREVIEW_WIDTH = 800
JPEG_QUALITY = 80
WATERMARK_TEXT = "PREVIEW"
WATERMARK_FILL = (170, 170, 170, 64)  # light grey at ~25% opacity


def _watermark_font(image_width: int) -> ImageFont.ImageFont:
    """Best-effort large font for the watermark, falling back to the default."""
    target = max(40, int(image_width * 0.14))
    for candidate in (
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial.ttf",
        "arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, target)
        except OSError:
            continue
    logger.warning("No TrueType font available; using the scalable default font.")
    try:
        return ImageFont.load_default(size=target)
    except TypeError:  # Pillow < 10.1 has no sizeable default font
        return ImageFont.load_default()


def _apply_watermark(image: Image.Image) -> Image.Image:
    """Composite a diagonal semi-transparent PREVIEW watermark onto the image."""
    width, height = image.size
    font = _watermark_font(width)

    scratch = Image.new("RGBA", (width * 2, height * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    draw.text(
        ((scratch.width - text_width) / 2, (scratch.height - text_height) / 2),
        WATERMARK_TEXT,
        font=font,
        fill=WATERMARK_FILL,
    )

    rotated = scratch.rotate(30, resample=Image.BICUBIC, expand=False)
    overlay = rotated.crop(
        (
            (rotated.width - width) // 2,
            (rotated.height - height) // 2,
            (rotated.width - width) // 2 + width,
            (rotated.height - height) // 2 + height,
        )
    )

    composited = Image.alpha_composite(image.convert("RGBA"), overlay)
    return composited.convert("RGB")


def generate_preview(pdf_bytes: bytes) -> bytes:
    """Render page 1 of ``pdf_bytes`` as an 800px-wide watermarked JPEG."""
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if document.page_count < 1:
            raise RuntimeError("Cannot generate a preview: the PDF has zero pages.")
        page = document.load_page(0)
        page_width = float(page.rect.width)
        if page_width <= 0:
            raise RuntimeError("Cannot generate a preview: page 1 has zero width.")
        zoom = PREVIEW_WIDTH / page_width
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        png_bytes = pix.tobytes("png")
    finally:
        document.close()

    with Image.open(io.BytesIO(png_bytes)) as raw:
        image = raw.convert("RGB")

    image = _apply_watermark(image)

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=JPEG_QUALITY)
    return output.getvalue()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from pdf_generator import OpusZimPDFGenerator  # noqa: PLC0415 - self-test only

    sample_project = {
        "subject_name": "Design & Technology",
        "level": "Form 4 (ZIMSEC O Level)",
        "project_title": "A Low-Cost Solar Water Heater for Rural Households",
        "year": 2025,
        "stage1": [
            {
                "heading": "Background to the Problem",
                "paragraph": (
                    "Rural households spend several hours each week gathering firewood to "
                    "heat bathing water, which increases labour and reduces tree cover near "
                    "the homestead."
                ),
            },
            {
                "heading": "Statement of Intent",
                "paragraph": (
                    "I intend to design a solar water heater that supplies twenty litres of "
                    "warm water daily using locally available materials."
                ),
            },
        ],
        "stage2": [
            {
                "title": "Flat Plate Collector Systems",
                "body": "Water circulates through pipes bonded to a dark absorber plate.",
                "merits": ["High efficiency", "Proven design"],
                "demerits": ["Costly copper tubing", "Needs precise fabrication"],
            },
            {
                "title": "Batch Storage Heaters",
                "body": "The storage vessel is also the collector, so no pump is required.",
                "merits": ["Simple to build", "No moving parts"],
                "demerits": ["Overnight heat loss", "Heavy when full"],
            },
        ],
        "stage3": [
            {
                "title": "Concept A: Painted Drum Heater",
                "body": "A matt black steel drum sits inside a glazed plywood box.",
                "merits": ["Very cheap", "Local materials"],
                "demerits": ["Slow warm-up", "Small capacity"],
            },
            {
                "title": "Concept B: Coiled Hosepipe Collector",
                "body": "Black hosepipe is coiled on a reflective board to absorb heat.",
                "merits": ["Rapid temperature rise", "Light weight"],
                "demerits": ["Hose degrades in sun", "Low storage volume"],
            },
        ],
        "stage4": {
            "chosen": (
                "A hybrid unit was chosen: a hose coil pre-heats water that drains into an "
                "insulated black drum, meeting both the volume and temperature targets "
                "within budget."
            ),
            "refinements": [
                "Insulate the drum with dry grass between plywood skins.",
                "Fit a hinged glazing panel for cleaning.",
                "Add a draw-off tap at the drum base.",
            ],
        },
        "stage5": {
            "intro": (
                "The unit was assembled in the school workshop and tested over five clear "
                "days, with hourly temperature readings recorded and plotted."
            ),
            "needs_images": True,
            "image_prompts": [
                "Photograph of the assembled solar water heater",
                "Graph of water temperature against time",
            ],
            "captions": [
                "The assembled hybrid solar water heater in the test position",
                "Recorded water temperature against time of day",
            ],
        },
        "stage6": {
            "relevance": (
                "The heater delivered twenty-two litres at 48 degrees Celsius for a material "
                "cost of US$34, fulfilling the statement of intent."
            ),
            "challenges": [
                "Overcast days halved the temperature rise.",
                "The salvaged glazing pane cracked in transport.",
            ],
            "recommendations": [
                "Add a night cover to cut overnight losses.",
                "Use toughened glass where funds allow.",
            ],
        },
    }

    generated_pdf = OpusZimPDFGenerator().build(sample_project, [])
    preview_jpeg = generate_preview(generated_pdf)

    with open("/tmp/opuszim_sample.pdf", "wb") as handle:
        handle.write(generated_pdf)
    with open("/tmp/opuszim_sample.jpg", "wb") as handle:
        handle.write(preview_jpeg)

    sample_doc = fitz.open(stream=generated_pdf, filetype="pdf")
    try:
        total_pages = sample_doc.page_count
    finally:
        sample_doc.close()

    with Image.open(io.BytesIO(preview_jpeg)) as preview_image:
        preview_size = preview_image.size

    print(f"PDF bytes:     {len(generated_pdf)}")
    print(f"JPEG bytes:    {len(preview_jpeg)}")
    print(f"Page count:    {total_pages}")
    print(f"Preview size:  {preview_size[0]}x{preview_size[1]}")
    print("Wrote /tmp/opuszim_sample.pdf and /tmp/opuszim_sample.jpg")
