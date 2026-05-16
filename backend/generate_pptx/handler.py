"""
Lambda handler for generating a PowerPoint presentation using Qwen AI and python-pptx.
"""
from __future__ import annotations

import io
import json
import os
import re
import uuid

import boto3
from openai import OpenAI
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

S3_OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "")
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-turbo")


def _hex_to_rgb(hex_color: str) -> RGBColor:
    """Convert a CSS hex color (e.g. '#4f46e5') to an RGBColor."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return RGBColor(r, g, b)


def _build_presentation(
    slides_data: list[dict],
    primary_color: str,
    accent_color: str,
    logo_url: str | None,
) -> bytes:
    """Build a PPTX in memory and return the raw bytes."""
    prs = Presentation()

    primary_rgb = _hex_to_rgb(primary_color)
    accent_rgb = _hex_to_rgb(accent_color)

    for i, slide_data in enumerate(slides_data):
        # Use title+content layout for body slides, title-only for the first
        layout = prs.slide_layouts[0] if i == 0 else prs.slide_layouts[1]
        slide = prs.slides.add_slide(layout)

        # Title
        title_shape = slide.shapes.title
        if title_shape:
            title_shape.text = slide_data.get("title", "")
            for para in title_shape.text_frame.paragraphs:
                for run in para.runs:
                    run.font.color.rgb = primary_rgb
                    run.font.bold = True

        # Body / content
        if len(slide.placeholders) > 1:
            body_shape = slide.placeholders[1]
            tf = body_shape.text_frame
            tf.clear()
            content = slide_data.get("content", "")
            lines = content.split("\n") if content else []
            for j, line in enumerate(lines):
                para = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
                para.text = line.strip("- ").strip()
                for run in para.runs:
                    run.font.color.rgb = accent_rgb if j % 2 == 1 else RGBColor(0x11, 0x18, 0x27)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


def _parse_slides(raw: str) -> list[dict]:
    """
    Parse the AI response into a list of slide dicts.
    Accepts either a JSON array directly or JSON embedded in a markdown code block.
    """
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "slides" in data:
            return data["slides"]
    except json.JSONDecodeError:
        pass

    # Fallback: create a single slide with the raw text
    return [{"title": "Presentation", "content": raw}]


def handler(event: dict, context) -> dict:  # noqa: ANN001
    """AWS Lambda entry point."""
    try:
        body = json.loads(event.get("body") or "{}")
        prompt: str = body.get("prompt", "").strip()
        api_key: str = body.get("apiKey", "").strip()
        primary_color: str = body.get("primaryColor", "#4f46e5")
        accent_color: str = body.get("accentColor", "#f59e0b")
        logo_url: str | None = body.get("logoUrl")

        if not prompt:
            return _response(400, {"error": "prompt is required"})
        if not api_key:
            return _response(400, {"error": "apiKey is required"})

        # --- Generate slide content via Qwen ---
        client = OpenAI(api_key=api_key, base_url=QWEN_BASE_URL)

        system_prompt = (
            "You are a helpful assistant that creates PowerPoint presentations. "
            "Return ONLY a JSON array where each element has exactly two keys: "
            '"title" (string) and "content" (string with bullet points separated by newlines). '
            "Do not include any other text, markdown, or explanation."
        )
        completion = client.chat.completions.create(
            model=QWEN_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Create a presentation about: {prompt}"},
            ],
        )
        raw_content = completion.choices[0].message.content or ""
        slides_data = _parse_slides(raw_content)

        # --- Build PPTX ---
        pptx_bytes = _build_presentation(slides_data, primary_color, accent_color, logo_url)

        # --- Upload to S3 ---
        s3 = boto3.client("s3")
        key = f"presentations/{uuid.uuid4()}.pptx"
        s3.put_object(
            Bucket=S3_OUTPUT_BUCKET,
            Key=key,
            Body=pptx_bytes,
            ContentType=(
                "application/vnd.openxmlformats-officedocument"
                ".presentationml.presentation"
            ),
        )

        download_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_OUTPUT_BUCKET, "Key": key},
            ExpiresIn=3600,
        )

        return _response(200, {"downloadUrl": download_url})

    except Exception as exc:  # noqa: BLE001
        return _response(500, {"error": str(exc)})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body),
    }
