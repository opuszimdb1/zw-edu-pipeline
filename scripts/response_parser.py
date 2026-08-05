# FILE: automation/scripts/response_parser.py
"""Strict delimiter parser converting raw Gemini text into the canonical dict.

The output shape is a hard contract consumed by Module 9 (renderer) after
Module 11 fills in subject_name / level / project_title.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_IMAGES = 2

_BULLET_MARKERS = ("-", "•", "*", "–")

_STAGE_RE = r"@@STAGE_START:{n}@@(.*?)@@STAGE_END:{n}@@"
_SUBSECTION_RE = r"@@SUBSECTION:(.*?)@@(.*?)@@SUBSECTION_END@@"
_CONCEPT_RE = r"@@CONCEPT_START:(\d+)@@(.*?)@@CONCEPT_END:\1@@"
_APPROACH_RE = r"@@APPROACH_START:(\d+)@@(.*?)@@APPROACH_END:\1@@"
_TITLE_RE = r"@@{tag}_TITLE@@(.*?)@@"
_BODY_RE = r"@@{tag}_BODY@@(.*?)@@"
_MERITS_RE = r"@@MERITS_START@@(.*?)@@MERITS_END@@"
_DEMERITS_RE = r"@@DEMERITS_START@@(.*?)@@DEMERITS_END@@"
_INTRO_RE = r"@@INTRO_PARAGRAPH@@(.*?)@@"
_NEEDS_IMAGES_RE = r"@@NEEDS_IMAGES:(YES|NO)@@"
_IMAGE_PROMPT_RE = r"@@IMAGE_PROMPT:(\d+)@@(.*?)@@"
_CAPTION_RE = r"@@FIGURE_CAPTION:(\d+)@@(.*?)@@"


# --------------------------------------------------------------------- helpers
def _clean(text: str) -> str:
    """Strip delimiters/fences and collapse internal whitespace."""
    if not text:
        return ""
    value = text.replace("```", " ")
    value = value.strip()
    while value.endswith("@"):
        value = value[:-1].strip()
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _bullets(text: str) -> list[str]:
    """Split a bullet block into cleaned, non-empty bullet strings."""
    if not text:
        return []
    items: list[str] = []
    for raw_line in text.replace("```", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        while line.endswith("@"):
            line = line[:-1].strip()
        for marker in _BULLET_MARKERS:
            if line.startswith(marker):
                line = line[len(marker):].strip()
                break
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            items.append(line)
    return items


def _stage_body(raw: str, number: int) -> str:
    match = re.search(_STAGE_RE.format(n=number), raw, re.DOTALL)
    if not match:
        logger.warning("stage%d block was not found in the response", number)
        return ""
    return match.group(1)


def _subsections(stage_text: str) -> list[tuple[str, str]]:
    return [
        (_clean(name), body)
        for name, body in re.findall(_SUBSECTION_RE, stage_text, re.DOTALL)
    ]


def _slice_to_delimiters(raw: str) -> str:
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n")
    start = text.find("@@STAGE_START:1@@")
    end_marker = "@@STAGE_END:6@@"
    end = text.rfind(end_marker)
    if start == -1:
        logger.warning("@@STAGE_START:1@@ marker missing; parsing whole payload")
        start = 0
    if end == -1:
        logger.warning("@@STAGE_END:6@@ marker missing; parsing to end of payload")
        return text[start:]
    return text[start: end + len(end_marker)]


def _blocks(stage_text: str, pattern: str, tag: str) -> list[dict]:
    found = re.findall(pattern, stage_text, re.DOTALL)
    ordered = sorted(found, key=lambda item: int(item[0]))
    blocks: list[dict] = []
    for _number, body in ordered:
        title_match = re.search(_TITLE_RE.format(tag=tag), body, re.DOTALL)
        body_match = re.search(_BODY_RE.format(tag=tag), body, re.DOTALL)
        merits_match = re.search(_MERITS_RE, body, re.DOTALL)
        demerits_match = re.search(_DEMERITS_RE, body, re.DOTALL)
        blocks.append(
            {
                "title": _clean(title_match.group(1)) if title_match else "",
                "body": _clean(body_match.group(1)) if body_match else "",
                "merits": _bullets(merits_match.group(1)) if merits_match else [],
                "demerits": _bullets(demerits_match.group(1)) if demerits_match else [],
            }
        )
    return blocks


def _numbered_values(stage_text: str, pattern: str) -> list[str]:
    found = re.findall(pattern, stage_text, re.DOTALL)
    ordered = sorted(found, key=lambda item: int(item[0]))
    return [value for value in (_clean(text) for _n, text in ordered) if value]


def validate_parsed(data: dict) -> list[str]:
    """Return human-readable problems found in a parsed dict."""
    problems: list[str] = []
    if not data.get("stage1"):
        problems.append("stage1 has no subsections")
    if len(data.get("stage2") or []) < 2:
        problems.append("stage2 has fewer than 2 concepts")
    if len(data.get("stage3") or []) < 2:
        problems.append("stage3 has fewer than 2 approaches")
    if not (data.get("stage4") or {}).get("chosen"):
        problems.append("stage4 is missing the chosen approach")
    if not (data.get("stage4") or {}).get("refinements"):
        problems.append("stage4 has no refinements")
    stage5 = data.get("stage5") or {}
    if not stage5.get("intro"):
        problems.append("stage5 is missing the intro paragraph")
    if stage5.get("needs_images") and not stage5.get("image_prompts"):
        problems.append("stage5 requests images but has no image prompts")
    if stage5.get("needs_images") and len(stage5.get("image_prompts") or []) != len(
        stage5.get("captions") or []
    ):
        problems.append("stage5 image prompt and caption counts differ")
    stage6 = data.get("stage6") or {}
    if not stage6.get("relevance"):
        problems.append("stage6 is missing the relevance section")
    if not stage6.get("challenges"):
        problems.append("stage6 has no challenges")
    if not stage6.get("recommendations"):
        problems.append("stage6 has no recommendations")
    return problems


# ----------------------------------------------------------------------- entry
def parse_response(raw: str) -> dict:
    """Parse raw delimiter text into the canonical project dict."""
    text = _slice_to_delimiters(raw or "")

    stage1_text = _stage_body(text, 1)
    stage2_text = _stage_body(text, 2)
    stage3_text = _stage_body(text, 3)
    stage4_text = _stage_body(text, 4)
    stage5_text = _stage_body(text, 5)
    stage6_text = _stage_body(text, 6)

    stage1 = [
        {"heading": name, "paragraph": _clean(body)}
        for name, body in _subsections(stage1_text)
        if name or _clean(body)
    ]
    if not stage1:
        logger.warning("stage1 produced no heading/paragraph pairs")

    stage2 = _blocks(stage2_text, _CONCEPT_RE, "CONCEPT")
    if not stage2:
        logger.warning("stage2 produced no concept blocks")
    stage3 = _blocks(stage3_text, _APPROACH_RE, "APPROACH")
    if not stage3:
        logger.warning("stage3 produced no approach blocks")

    chosen = ""
    refinements: list[str] = []
    for name, body in _subsections(stage4_text):
        lowered = name.lower()
        if "chosen" in lowered:
            chosen = _clean(body)
        elif "refinement" in lowered:
            refinements = _bullets(body)
    if not chosen:
        logger.warning("stage4 has no 'Chosen' subsection")
    if not refinements:
        logger.warning("stage4 has no 'Refinement' subsection")

    intro_match = re.search(_INTRO_RE, stage5_text, re.DOTALL)
    intro = _clean(intro_match.group(1)) if intro_match else ""
    if not intro:
        logger.warning("stage5 has no @@INTRO_PARAGRAPH@@")

    needs_match = re.search(_NEEDS_IMAGES_RE, stage5_text)
    needs_images = bool(needs_match) and needs_match.group(1).upper() == "YES"
    if not needs_match:
        logger.warning("stage5 has no @@NEEDS_IMAGES@@ flag; defaulting to False")

    image_prompts = _numbered_values(stage5_text, _IMAGE_PROMPT_RE)
    captions = _numbered_values(stage5_text, _CAPTION_RE)
    if len(image_prompts) > MAX_IMAGES:
        logger.warning(
            "stage5 had %d image prompts; truncating to %d",
            len(image_prompts),
            MAX_IMAGES,
        )
        image_prompts = image_prompts[:MAX_IMAGES]
    if len(captions) > MAX_IMAGES:
        logger.warning(
            "stage5 had %d figure captions; truncating to %d", len(captions), MAX_IMAGES
        )
        captions = captions[:MAX_IMAGES]
    if not needs_images:
        image_prompts = []
        captions = []

    relevance = ""
    challenges: list[str] = []
    recommendations: list[str] = []
    for name, body in _subsections(stage6_text):
        lowered = name.lower()
        if "relevance" in lowered:
            relevance = _clean(body)
        elif "challenge" in lowered:
            challenges = _bullets(body)
        elif "recommendation" in lowered or "conclusion" in lowered:
            recommendations = _bullets(body)
    if not relevance:
        logger.warning("stage6 has no 'Relevance' subsection")
    if not challenges:
        logger.warning("stage6 has no 'Challenges' subsection")
    if not recommendations:
        logger.warning("stage6 has no 'Recommendations'/'Conclusion' subsection")

    data: dict = {
        "subject_name": "",
        "level": "",
        "project_title": "",
        "year": datetime.now(timezone.utc).year,
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3,
        "stage4": {"chosen": chosen, "refinements": refinements},
        "stage5": {
            "intro": intro,
            "needs_images": needs_images,
            "image_prompts": image_prompts,
            "captions": captions,
        },
        "stage6": {
            "relevance": relevance,
            "challenges": challenges,
            "recommendations": recommendations,
        },
    }

    for problem in validate_parsed(data):
        logger.warning("Validation: %s", problem)

    return data


_SAMPLE = """
Here is your project.
```
@@STAGE_START:1@@
@@SUBSECTION:Problem Identification@@
The local clinic records patient visits on paper, causing long queues.
@@SUBSECTION_END@@
@@SUBSECTION:Statement of Intent@@
A digital register will be designed to shorten waiting times.
@@SUBSECTION_END@@
@@STAGE_END:1@@
@@STAGE_START:2@@
@@CONCEPT_START:1@@
@@CONCEPT_TITLE@@Paper Register@@
@@CONCEPT_BODY@@A bound book used by the receptionist.@@
@@MERITS_START@@
- Very cheap
- No power needed
@@MERITS_END@@
@@DEMERITS_START@@
- Slow to search
@@DEMERITS_END@@
@@CONCEPT_END:1@@
@@CONCEPT_START:2@@
@@CONCEPT_TITLE@@Spreadsheet Register@@
@@CONCEPT_BODY@@Records typed into a shared spreadsheet.@@
@@MERITS_START@@
• Easy sorting
@@MERITS_END@@
@@DEMERITS_START@@
* Needs a computer
@@DEMERITS_END@@
@@CONCEPT_END:2@@
@@STAGE_END:2@@
@@STAGE_START:3@@
@@APPROACH_START:1@@
@@APPROACH_TITLE@@Offline Desktop App@@
@@APPROACH_BODY@@A local application storing data on one machine.@@
@@MERITS_START@@
- Works without internet
@@MERITS_END@@
@@DEMERITS_START@@
- Single point of failure
@@DEMERITS_END@@
@@APPROACH_END:1@@
@@APPROACH_START:2@@
@@APPROACH_TITLE@@Web Based Register@@
@@APPROACH_BODY@@A browser application backed by a shared database.@@
@@MERITS_START@@
- Accessible from any desk
@@MERITS_END@@
@@DEMERITS_START@@
– Requires connectivity
@@DEMERITS_END@@
@@APPROACH_END:2@@
@@STAGE_END:3@@
@@STAGE_START:4@@
@@SUBSECTION:Chosen Approach@@
The web based register was chosen for shared access across desks.
@@SUBSECTION_END@@
@@SUBSECTION:Refinements@@
- Add offline caching
- Add printable daily summary
@@SUBSECTION_END@@
@@STAGE_END:4@@
@@STAGE_START:5@@
@@INTRO_PARAGRAPH@@The register was built in three development sprints.@@
@@NEEDS_IMAGES:YES@@
@@IMAGE_PROMPT:1@@Clean flowchart of a clinic patient check-in process@@
@@IMAGE_PROMPT:2@@Simple wireframe of a patient register form@@
@@FIGURE_CAPTION:1@@Figure 1: Patient check-in flowchart@@
@@FIGURE_CAPTION:2@@Figure 2: Register form wireframe@@
@@STAGE_END:5@@
@@STAGE_START:6@@
@@SUBSECTION:Relevance@@
The solution directly reduces queueing time at the clinic reception.
@@SUBSECTION_END@@
@@SUBSECTION:Challenges@@
- Intermittent power supply
- Limited staff training time
@@SUBSECTION_END@@
@@SUBSECTION:Recommendations@@
- Install a backup power supply
- Train two staff members per shift
@@SUBSECTION_END@@
@@STAGE_END:6@@
```
"""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parsed = parse_response(_SAMPLE)
    print(json.dumps(parsed, indent=2))
