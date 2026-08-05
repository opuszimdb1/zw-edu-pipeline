# FILE: automation/scripts/pdf_generator.py
"""Opus Zim — branded A4 PDF renderer.

Turns the canonical project dict produced by Module 8 into a branded A4
document returned as raw PDF bytes. Built entirely on reportlab's bundled
Helvetica / Times-Roman families so the build works offline inside GitHub
Actions with no font files.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Branding
# --------------------------------------------------------------------------
PRIMARY = HexColor("#2E5EAA")
SECONDARY = HexColor("#4CC9C0")
ACCENT = HexColor("#FF7A59")
DARK = HexColor("#2B2D42")
LIGHT = HexColor("#F5F7FA")

BODY_FONT = "Helvetica"
BODY_FONT_BOLD = "Helvetica-Bold"
BODY_FONT_ITALIC = "Helvetica-Oblique"
DISPLAY_FONT = "Times-Roman"
DISPLAY_FONT_BOLD = "Times-Bold"

TAGLINE = "Empowering learners for quality results"
DISCLAIMER = "Generated projects are intended for reference and learning purposes only."

PAGE_MARGIN = 2.0 * cm
MAX_IMAGES = 2
MAX_IMAGE_HEIGHT = 9.0 * cm
MAX_LOGO_WIDTH = 4.0 * cm

STAGE_HEADINGS = (
    "1 INTRODUCTION / PROBLEM IDENTIFICATION",
    "2 RESEARCH / RELATED IDEAS",
    "3 POSSIBLE SOLUTIONS / CONCEPT DEVELOPMENT",
    "4 DEVELOPMENT",
    "5 PRESENTATION OF RESULTS",
    "6 EVALUATION, CONCLUSION & RECOMMENDATIONS",
)


def _esc(text: Any) -> str:
    """Escape text for reportlab's mini-markup parser."""
    if text is None:
        return ""
    value = str(text)
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class OpusZimPDFGenerator:
    """Renders the canonical Opus Zim project dict into a branded A4 PDF."""

    def __init__(self, logo_path: str | None = None) -> None:
        self.logo_path = logo_path
        self._project_title = ""
        self._styles = self._build_styles()

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------
    def _build_styles(self) -> dict[str, ParagraphStyle]:
        body = ParagraphStyle(
            "OZBody",
            fontName=BODY_FONT,
            fontSize=11,
            leading=15.4,
            alignment=TA_JUSTIFY,
            textColor=DARK,
            spaceAfter=10,
        )
        return {
            "body": body,
            "bullet": ParagraphStyle(
                "OZBullet",
                parent=body,
                spaceAfter=0,
            ),
            "sub": ParagraphStyle(
                "OZSub",
                fontName=BODY_FONT_BOLD,
                fontSize=12,
                leading=16,
                textColor=DARK,
                spaceAfter=8,
            ),
            "stage": ParagraphStyle(
                "OZStage",
                fontName=BODY_FONT_BOLD,
                fontSize=16,
                leading=20,
                textColor=PRIMARY,
                spaceAfter=14,
            ),
            "cover_brand": ParagraphStyle(
                "OZCoverBrand",
                fontName=DISPLAY_FONT_BOLD,
                fontSize=34,
                leading=40,
                alignment=TA_CENTER,
                textColor=PRIMARY,
                spaceAfter=8,
            ),
            "cover_tagline": ParagraphStyle(
                "OZCoverTagline",
                fontName=DISPLAY_FONT,
                fontSize=13,
                leading=18,
                alignment=TA_CENTER,
                textColor=SECONDARY,
                spaceAfter=10,
            ),
            "cover_title": ParagraphStyle(
                "OZCoverTitle",
                fontName=DISPLAY_FONT_BOLD,
                fontSize=22,
                leading=28,
                alignment=TA_CENTER,
                textColor=DARK,
                spaceAfter=18,
            ),
            "cover_detail": ParagraphStyle(
                "OZCoverDetail",
                fontName=BODY_FONT,
                fontSize=12,
                leading=18,
                alignment=TA_CENTER,
                textColor=DARK,
                spaceAfter=4,
            ),
            "cover_disclaimer": ParagraphStyle(
                "OZCoverDisclaimer",
                fontName=BODY_FONT_ITALIC,
                fontSize=9.5,
                leading=13,
                alignment=TA_CENTER,
                textColor=DARK,
                spaceAfter=0,
            ),
            "caption": ParagraphStyle(
                "OZCaption",
                fontName=BODY_FONT_ITALIC,
                fontSize=9.5,
                leading=13,
                alignment=TA_CENTER,
                textColor=DARK,
                spaceAfter=10,
            ),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def build(self, project_data: dict, image_paths: list[str]) -> bytes:
        """Build the PDF and return it as bytes."""
        data = project_data if isinstance(project_data, dict) else {}
        self._project_title = str(data.get("project_title") or "Untitled Project")

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=PAGE_MARGIN,
            rightMargin=PAGE_MARGIN,
            topMargin=PAGE_MARGIN,
            bottomMargin=PAGE_MARGIN,
            title=self._project_title,
            author="Opus Zim",
            subject=str(data.get("subject_name") or ""),
        )

        story: list[Any] = []
        story.extend(self._cover(data))
        story.append(PageBreak())

        sections = (
            self._stage1(data.get("stage1")),
            self._stage2(data.get("stage2")),
            self._stage3(data.get("stage3")),
            self._stage4(data.get("stage4")),
            self._stage5(data.get("stage5"), self._usable_images(image_paths), doc.width),
            self._stage6(data.get("stage6")),
        )
        for index, flowables in enumerate(sections):
            story.append(Paragraph(_esc(STAGE_HEADINGS[index]), self._styles["stage"]))
            story.extend(flowables)
            if index < len(sections) - 1:
                story.append(PageBreak())

        doc.build(story, onFirstPage=self._draw_cover_page, onLaterPages=self._draw_furniture)
        return buffer.getvalue()

    # ------------------------------------------------------------------
    # Cover
    # ------------------------------------------------------------------
    def _cover(self, data: dict) -> list[Any]:
        flowables: list[Any] = [Spacer(1, 1.2 * cm)]

        logo = self._logo_flowable()
        if logo is not None:
            flowables.append(logo)
            flowables.append(Spacer(1, 0.8 * cm))

        flowables.append(Paragraph("OPUS ZIM", self._styles["cover_brand"]))
        flowables.append(Paragraph(_esc(TAGLINE), self._styles["cover_tagline"]))
        flowables.append(_HorizontalRule(color=ACCENT))
        flowables.append(Spacer(1, 1.6 * cm))
        flowables.append(
            Paragraph(_esc(data.get("project_title") or "Untitled Project"), self._styles["cover_title"])
        )
        flowables.append(Spacer(1, 0.6 * cm))

        for label, key in (("Subject", "subject_name"), ("Level", "level"), ("Year", "year")):
            value = data.get(key)
            if value in (None, ""):
                continue
            flowables.append(
                Paragraph(f"{label}: {_esc(value)}", self._styles["cover_detail"])
            )

        flowables.append(Spacer(1, 5.0 * cm))
        flowables.append(Paragraph(_esc(DISCLAIMER), self._styles["cover_disclaimer"]))
        return flowables

    def _logo_flowable(self) -> Image | None:
        if not self.logo_path:
            return None
        if not os.path.isfile(self.logo_path):
            logger.warning("Logo file not found, skipping: %s", self.logo_path)
            return None
        try:
            reader = ImageReader(self.logo_path)
            src_width, src_height = reader.getSize()
        except Exception as exc:  # noqa: BLE001 - reportlab raises broadly on bad images
            logger.warning("Unreadable logo file %s: %s", self.logo_path, exc)
            return None
        if not src_width or not src_height:
            logger.warning("Logo has zero dimensions, skipping: %s", self.logo_path)
            return None
        width = min(MAX_LOGO_WIDTH, float(src_width))
        height = width * (float(src_height) / float(src_width))
        image = Image(self.logo_path, width=width, height=height)
        image.hAlign = "CENTER"
        return image

    # ------------------------------------------------------------------
    # Stage sections
    # ------------------------------------------------------------------
    def _stage1(self, items: Any) -> list[Any]:
        flowables: list[Any] = []
        for item in self._as_dict_list(items):
            block: list[Any] = []
            heading = item.get("heading")
            if heading:
                block.append(Paragraph(_esc(heading), self._styles["sub"]))
            paragraph = item.get("paragraph")
            if paragraph:
                block.append(Paragraph(_esc(paragraph), self._styles["body"]))
            if block:
                flowables.append(KeepTogether(block))
        return flowables

    def _stage2(self, concepts: Any) -> list[Any]:
        return self._titled_blocks(concepts)

    def _stage3(self, approaches: Any) -> list[Any]:
        return self._titled_blocks(approaches)

    def _titled_blocks(self, entries: Any) -> list[Any]:
        flowables: list[Any] = []
        for entry in self._as_dict_list(entries):
            block: list[Any] = []
            title = entry.get("title")
            if title:
                block.append(Paragraph(_esc(title), self._styles["sub"]))
            body = entry.get("body")
            if body:
                block.append(Paragraph(_esc(body), self._styles["body"]))
            for label, key in (("Merits", "merits"), ("Demerits", "demerits")):
                bullets = self._bullets(entry.get(key))
                if bullets is None:
                    continue
                block.append(Paragraph(label, self._styles["sub"]))
                block.append(bullets)
            if block:
                flowables.append(KeepTogether(block))
        return flowables

    def _stage4(self, stage4: Any) -> list[Any]:
        data = stage4 if isinstance(stage4, dict) else {}
        flowables: list[Any] = []
        chosen = data.get("chosen")
        if chosen:
            flowables.append(Paragraph("Chosen Approach", self._styles["sub"]))
            flowables.append(Paragraph(_esc(chosen), self._styles["body"]))
        bullets = self._bullets(data.get("refinements"))
        if bullets is not None:
            flowables.append(Paragraph("Refinements", self._styles["sub"]))
            flowables.append(bullets)
        return flowables

    def _stage5(self, stage5: Any, images: list[str], text_width: float) -> list[Any]:
        data = stage5 if isinstance(stage5, dict) else {}
        flowables: list[Any] = []
        intro = data.get("intro")
        if intro:
            flowables.append(Paragraph(_esc(intro), self._styles["body"]))

        if not data.get("needs_images", True) or not images:
            return flowables

        captions = data.get("captions")
        captions = captions if isinstance(captions, list) else []

        for index, path in enumerate(images):
            image = self._scaled_image(path, text_width)
            if image is None:
                continue
            number = index + 1
            caption = captions[index] if index < len(captions) and captions[index] else ""
            label = f"Figure {number}: {_esc(caption)}" if caption else f"Figure {number}"
            flowables.append(KeepTogether([image, Paragraph(label, self._styles["caption"])]))
        return flowables

    def _stage6(self, stage6: Any) -> list[Any]:
        data = stage6 if isinstance(stage6, dict) else {}
        flowables: list[Any] = []
        relevance = data.get("relevance")
        if relevance:
            flowables.append(
                Paragraph("Relevance of the Statement of Intent", self._styles["sub"])
            )
            flowables.append(Paragraph(_esc(relevance), self._styles["body"]))
        for label, key in (
            ("Challenges and Limitations", "challenges"),
            ("Recommendations and Conclusions", "recommendations"),
        ):
            bullets = self._bullets(data.get(key))
            if bullets is None:
                continue
            flowables.append(Paragraph(label, self._styles["sub"]))
            flowables.append(bullets)
        return flowables

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _as_dict_list(value: Any) -> list[dict]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _bullets(self, values: Any) -> ListFlowable | None:
        if not isinstance(values, list):
            return None
        items = [str(value) for value in values if value not in (None, "")]
        if not items:
            return None
        return ListFlowable(
            [
                ListItem(
                    Paragraph(_esc(item), self._styles["bullet"]),
                    leftIndent=0.6 * cm,
                    spaceAfter=4,
                )
                for item in items
            ],
            bulletType="bullet",
            start="\u2022",
            bulletFontName=BODY_FONT,
            bulletFontSize=11,
            leftIndent=0.6 * cm,
            spaceAfter=10,
        )

    @staticmethod
    def _usable_images(image_paths: Any) -> list[str]:
        if not isinstance(image_paths, list):
            return []
        paths = [str(path) for path in image_paths if path]
        if len(paths) > MAX_IMAGES:
            logger.warning(
                "Received %d images, only the first %d will be rendered.",
                len(paths),
                MAX_IMAGES,
            )
            paths = paths[:MAX_IMAGES]
        usable: list[str] = []
        for path in paths:
            if os.path.isfile(path):
                usable.append(path)
            else:
                logger.warning("Image file missing, skipping: %s", path)
        return usable

    @staticmethod
    def _scaled_image(path: str, text_width: float) -> Image | None:
        try:
            reader = ImageReader(path)
            src_width, src_height = reader.getSize()
        except Exception as exc:  # noqa: BLE001 - unreadable images must not crash the build
            logger.warning("Unreadable image file %s: %s", path, exc)
            return None
        if not src_width or not src_height:
            logger.warning("Image has zero dimensions, skipping: %s", path)
            return None
        ratio = float(src_height) / float(src_width)
        width = float(text_width)
        height = width * ratio
        if height > MAX_IMAGE_HEIGHT:
            height = MAX_IMAGE_HEIGHT
            width = height / ratio
        image = Image(path, width=width, height=height)
        image.hAlign = "CENTER"
        return image

    # ------------------------------------------------------------------
    # Page furniture
    # ------------------------------------------------------------------
    def _draw_cover_page(self, canvas: Any, doc: Any) -> None:
        """Cover page callback — deliberately bare (no header, no page number)."""
        canvas.saveState()
        canvas.setFillColor(LIGHT)
        canvas.rect(0, 0, doc.pagesize[0], 0.9 * cm, stroke=0, fill=1)
        canvas.restoreState()

    def _draw_furniture(self, canvas: Any, doc: Any) -> None:
        page_width, _page_height = doc.pagesize
        left = doc.leftMargin
        right = page_width - doc.rightMargin
        header_y = doc.pagesize[1] - doc.topMargin + 0.45 * cm
        title = self._project_title[:60]

        canvas.saveState()
        canvas.setFont(BODY_FONT_BOLD, 8)
        canvas.setFillColor(PRIMARY)
        canvas.drawString(left, header_y, "Opus Zim")
        canvas.setFont(BODY_FONT, 8)
        canvas.setFillColor(DARK)
        canvas.drawRightString(right, header_y, title)
        canvas.setStrokeColor(SECONDARY)
        canvas.setLineWidth(0.5)
        canvas.line(left, header_y - 0.18 * cm, right, header_y - 0.18 * cm)

        canvas.setFont(BODY_FONT, 8)
        canvas.setFillColor(DARK)
        canvas.drawCentredString(page_width / 2.0, 1.25 * cm, f"Page {canvas.getPageNumber()}")
        canvas.setFont(BODY_FONT, 6.5)
        canvas.drawCentredString(page_width / 2.0, 0.85 * cm, DISCLAIMER)
        canvas.restoreState()


class _HorizontalRule(Spacer):
    """A thin full-width rule flowable in the given colour."""

    def __init__(self, width: float = 0, height: float = 0.45 * cm, color: Any = ACCENT) -> None:
        super().__init__(width, height)
        self.rule_color = color

    def draw(self) -> None:
        self.canv.saveState()
        self.canv.setStrokeColor(self.rule_color)
        self.canv.setLineWidth(1.4)
        y = self.height / 2.0
        self.canv.line(0, y, self.width, y)
        self.canv.restoreState()

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        self.width = available_width
        return available_width, self.height


def _sample_project() -> dict:
    return {
        "subject_name": "Design & Technology",
        "level": "Form 4 (ZIMSEC O Level)",
        "project_title": "A Low-Cost Solar Water Heater for Rural Households",
        "year": 2025,
        "stage1": [
            {
                "heading": "Background to the Problem",
                "paragraph": (
                    "Many rural households in Zimbabwe heat water using firewood, a practice "
                    "that consumes several hours of labour each week and contributes to "
                    "deforestation around homesteads. Learners in the community reported that "
                    "water for bathing is frequently heated over an open fire before school."
                ),
            },
            {
                "heading": "Statement of Intent",
                "paragraph": (
                    "I intend to design and construct a low-cost solar water heater that "
                    "delivers at least twenty litres of warm water each afternoon using only "
                    "locally available materials and no electrical supply."
                ),
            },
            {
                "heading": "Design Specification",
                "paragraph": (
                    "The device must cost under US$40, be assembled with hand tools only, "
                    "resist rain and wind, and raise water temperature by at least 25 degrees "
                    "Celsius on a clear day (a > b in efficiency terms)."
                ),
            },
        ],
        "stage2": [
            {
                "title": "Flat Plate Collector Systems",
                "body": (
                    "Commercial flat plate collectors circulate water through copper pipes "
                    "bonded to a dark absorber plate inside an insulated glazed box."
                ),
                "merits": ["High thermal efficiency", "Well documented performance data"],
                "demerits": ["Copper tubing is expensive", "Requires precise fabrication"],
            },
            {
                "title": "Batch (Integral Collector Storage) Heaters",
                "body": (
                    "A batch heater stores and heats the water in the same insulated vessel, "
                    "removing the need for pumps or circulation piping."
                ),
                "merits": ["Very simple construction", "No moving parts to maintain"],
                "demerits": ["Heat loss overnight", "Heavy when full of water"],
            },
        ],
        "stage3": [
            {
                "title": "Concept A: Painted Drum Batch Heater",
                "body": (
                    "A recycled 20-litre steel drum is painted matt black and mounted inside "
                    "a plywood box glazed with a salvaged window pane."
                ),
                "merits": ["Cheapest option", "Materials available locally"],
                "demerits": ["Slow morning warm-up", "Limited capacity"],
            },
            {
                "title": "Concept B: Coiled Hosepipe Collector",
                "body": (
                    "A black polythene hosepipe is coiled on a reflective board so that water "
                    "gains heat as it passes slowly through the coil."
                ),
                "merits": ["Fast temperature rise", "Light and easy to mount"],
                "demerits": ["Hose degrades in sunlight", "Low storage volume"],
            },
            {
                "title": "Concept C: Hybrid Drum and Coil Unit",
                "body": (
                    "A coiled hose pre-heats water that then drains into an insulated black "
                    "drum acting as the storage tank."
                ),
                "merits": ["Good heat retention", "Meets the volume specification"],
                "demerits": ["More joints to seal", "Slightly higher cost"],
            },
        ],
        "stage4": {
            "chosen": (
                "Concept C was selected because it satisfies both the temperature rise and "
                "the twenty-litre volume requirements while remaining inside the budget. The "
                "hybrid arrangement separates the heating surface from the storage volume, "
                "which allows the collector area to be increased without replacing the tank."
            ),
            "refinements": [
                "Insulate the drum with dry grass packed between two plywood skins.",
                "Add a hinged glazing panel so the collector can be cleaned.",
                "Fit a tap at the drum base for controlled draw-off.",
                "Seal all hose joints with silicone and hose clamps.",
            ],
        },
        "stage5": {
            "intro": (
                "The completed unit was assembled at the school workshop and tested over five "
                "consecutive clear days. Water temperature was recorded hourly with a "
                "laboratory thermometer and the results were plotted against time."
            ),
            "needs_images": True,
            "image_prompts": [
                "Photograph of a plywood solar water heater box with a glazed lid",
                "Line graph of water temperature against time of day",
            ],
            "captions": [
                "The assembled hybrid solar water heater in the test position",
                "Recorded water temperature against time of day",
            ],
        },
        "stage6": {
            "relevance": (
                "The finished heater fulfils the statement of intent: it delivered twenty-two "
                "litres of water at 48 degrees Celsius on a clear afternoon at a total "
                "material cost of US$34, removing the need for firewood on sunny days."
            ),
            "challenges": [
                "Overcast days reduced the temperature rise by more than half.",
                "The salvaged glazing pane cracked during transport and had to be replaced.",
                "Overnight heat loss remained noticeable despite the grass insulation.",
            ],
            "recommendations": [
                "Add a removable night cover to reduce overnight losses.",
                "Use toughened glass for the glazing where funds allow.",
                "Trial a larger collector coil to improve winter performance.",
            ],
        },
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from pdf_preview import generate_preview  # noqa: PLC0415 - self-test only

    sample = _sample_project()
    pdf_bytes = OpusZimPDFGenerator().build(sample, [])
    jpeg_bytes = generate_preview(pdf_bytes)

    with open("/tmp/opuszim_sample.pdf", "wb") as handle:
        handle.write(pdf_bytes)
    with open("/tmp/opuszim_sample.jpg", "wb") as handle:
        handle.write(jpeg_bytes)

    import fitz  # noqa: PLC0415 - self-test only

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_count = document.page_count
    finally:
        document.close()

    print(f"PDF bytes:     {len(pdf_bytes)}")
    print(f"JPEG bytes:    {len(jpeg_bytes)}")
    print(f"Page count:    {page_count}")
    print("Wrote /tmp/opuszim_sample.pdf and /tmp/opuszim_sample.jpg")
