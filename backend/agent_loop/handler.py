"""
Agent-loop Lambda: orchestrates Research → Structure → Build for a PowerPoint presentation.
Invoked asynchronously by start_job. Updates a Supabase `jobs` row at each stage.
"""
from __future__ import annotations

import builtins as _builtins
import io
import json
import math
import os
import re
import textwrap
import uuid
from urllib import error as urlerror
from urllib import request as urlrequest

import boto3
from openai import OpenAI

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

S3_OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "")
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-plus")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# ─── SUPABASE HELPERS ─────────────────────────────────────────────────────────

def _supabase_request(method: str, url: str, headers: dict, body: dict | None = None) -> object:
    payload = None
    request_headers = dict(headers)
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url=url, method=method, data=payload, headers=request_headers)
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 – best-effort status updates must never crash the loop
        return {}


def _update_job(job_id: str, **fields: object) -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/jobs?id=eq.{job_id}"
    _supabase_request(
        "PATCH",
        url,
        {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Prefer": "return=minimal",
        },
        fields,  # type: ignore[arg-type]
    )


# ─── RESEARCH AGENT ──────────────────────────────────────────────────────────

def _research(prompt: str, client: OpenAI, job_id: str) -> str:
    """Stage 1 – Use Qwen with web search to gather current facts and data."""
    _update_job(job_id, status="researching", stage_message="Researching your topic\u2026")

    system = (
        "You are a research analyst preparing materials for a business presentation. "
        "Use web search to gather current facts, statistics, trends and examples. "
        "Produce a thorough markdown document covering: key facts and data, recent trends, "
        "notable examples or case studies, and key takeaways. "
        "Include specific numbers and dates where available."
    )
    response = client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Research this topic for a business presentation:\n\n{prompt}"},
        ],
        extra_body={"enable_search": True},
        max_tokens=4000,
    )
    return response.choices[0].message.content or ""


# ─── STRUCTURE AGENT ─────────────────────────────────────────────────────────

_STRUCTURE_SCHEMA = textwrap.dedent("""\
Return ONLY a valid JSON object matching this schema (no markdown fences):
{
  "presentation_title": "string",
  "slides": [
    {
      "slide_title": "string",
      "section_label": "string",
      "layout": "two_columns | three_columns | full_width",
      "columns": [
        {
          "width_ratio": <0.33 | 0.5 | 0.67 | 1.0>,
          "box_header": "string or null",
          "subtitle": "string or null",
          "content_type": "chart | bullet_list | text | news_cards | icon_grid",
          "chart": {
            "chart_type": "bar | grouped_bar | line | pie",
            "title": "string",
            "x_labels": ["string"],
            "series": [{"name": "string", "values": [<number>]}]
          },
          "bullets": [
            {"icon": "globe | shopping-bag | target | chart-bar | users | lightning | star | arrow-right",
             "title": "string", "description": "string"}
          ],
          "news_cards": [
            {"date": "string", "source": "string", "headline": "string", "subtext": "string"}
          ],
          "text": "string"
        }
      ],
      "column_separator": "line | causal_line | none",
      "conclusion_box": "string or null",
      "sources": "string or null"
    }
  ]
}
Rules:
- two_columns: exactly 2 columns, width_ratios must sum to 1.0
- three_columns: exactly 3 columns, each width_ratio = 0.33
- full_width: exactly 1 column, width_ratio = 1.0
- For charts: always include realistic data matching the research findings
- Use 4–8 slides total; group related slides under the same section_label
""")


def _structure(prompt: str, research_md: str, client: OpenAI, job_id: str) -> dict:
    """Stage 2 – Design the presentation structure as a validated JSON blueprint."""
    _update_job(job_id, status="structuring", stage_message="Building presentation structure\u2026")

    system = (
        "You are a McKinsey-trained presentation designer. "
        "Based on the research findings, design a compelling, insight-driven PowerPoint presentation. "
        + _STRUCTURE_SCHEMA
    )
    response = client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Topic: {prompt}\n\nResearch:\n{research_md}"},
        ],
        response_format={"type": "json_object"},
        max_tokens=6000,
    )
    raw = response.choices[0].message.content or "{}"
    return json.loads(raw)


# ─── BUILD AGENT ─────────────────────────────────────────────────────────────

_BUILD_SYSTEM = textwrap.dedent("""\
You are an expert python-pptx developer. Write a Python function called
`build_presentation(prs)` that adds all slides to the given python-pptx
Presentation object `prs`.

CONSTRAINTS:
- `prs` already has slide_width = Inches(13.33), slide_height = Inches(7.5)
- Use `prs.slide_layouts[6]` (blank layout) for every slide
- DO NOT call Presentation() yourself — use the `prs` argument
- Available names in scope: Inches, Pt, Emu, Cm, RGBColor, PP_ALIGN,
  ChartData, XL_CHART_TYPE, io, math, json
- Return ONLY the Python function code, no markdown fences, no other text
- The function must be syntactically valid Python 3.12

STYLE GUIDE:
- Slide background: white
- Section label: top-left, 9 pt, RGB(128,128,128)
- Slide title: bold, 22 pt, black, positioned below section label
- Box headers: filled rectangles with white bold text (12 pt); use primary color
- Column separators: thin vertical line (0.5 pt, light gray) centered between columns
- Causal separator: same vertical line + small filled triangle pointing right
- Bullet lists: icon placeholder (filled circle) + bold title + description text
- Charts: use ChartData + slide.shapes.add_chart() for bar/line/pie charts
- Conclusion box: thin-bordered rectangle (1 pt, primary color), centered italic text 11 pt
- Sources: bottom-left, 8 pt, RGB(128,128,128)
- Logo: if logo_url is provided pass it as a parameter; otherwise skip
""")


def _build_code(structure: dict, settings: dict, client: OpenAI, job_id: str) -> str:
    """Stage 3a – Generate python-pptx code from the structure blueprint."""
    _update_job(job_id, status="building", stage_message="Building your presentation\u2026")

    primary = settings.get("primary_color", "#C00000")
    accent = settings.get("accent_color", "#A6CAEC")
    user_msg = (
        f"Primary color: {primary}\n"
        f"Accent color: {accent}\n\n"
        f"Presentation structure:\n{json.dumps(structure, indent=2)}"
    )
    response = client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": _BUILD_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=8000,
    )
    return response.choices[0].message.content or ""


# ─── EXECUTE GENERATED CODE ───────────────────────────────────────────────────

def _execute(code: str) -> bytes:
    """
    Execute the AI-generated build_presentation(prs) function in a restricted
    namespace and return the resulting PPTX bytes.

    Security notes:
    - __builtins__ is replaced with a curated allowlist; __import__, open,
      eval, and exec are excluded.
    - os, sys, socket are not injected, preventing system/network access.
    - python-pptx itself runs in its own module scope with full builtins, which
      is fine because it is a vetted dependency, not user-supplied code.
    """
    import pptx
    import pptx.chart.data
    import pptx.dml.color
    import pptx.enum.chart
    import pptx.enum.text
    import pptx.util
    from pptx import Presentation as _Prs

    # Strip markdown fences the model may have added
    code = re.sub(r"```(?:python)?\s*", "", code).strip().rstrip("`").strip()

    _SAFE_NAMES = (
        "abs", "bool", "dict", "enumerate", "float", "hasattr", "int",
        "isinstance", "len", "list", "max", "min", "print", "range",
        "round", "set", "str", "sum", "tuple", "zip",
    )
    safe_builtins = {
        name: getattr(_builtins, name)
        for name in _SAFE_NAMES
        if hasattr(_builtins, name)
    }

    namespace: dict = {
        "__builtins__": safe_builtins,
        # pptx utilities
        "Inches": pptx.util.Inches,
        "Pt": pptx.util.Pt,
        "Emu": pptx.util.Emu,
        "Cm": pptx.util.Cm,
        "RGBColor": pptx.dml.color.RGBColor,
        "PP_ALIGN": pptx.enum.text.PP_ALIGN,
        "ChartData": pptx.chart.data.ChartData,
        "XL_CHART_TYPE": pptx.enum.chart.XL_CHART_TYPE,
        # safe stdlib modules
        "io": io,
        "math": math,
        "json": json,
    }

    exec(code, namespace)  # noqa: S102

    build_fn = namespace.get("build_presentation")
    if not callable(build_fn):
        raise ValueError("Generated code does not define a callable 'build_presentation'")

    prs = _Prs()
    prs.slide_width = pptx.util.Inches(13.33)
    prs.slide_height = pptx.util.Inches(7.5)
    build_fn(prs)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf.read()


# ─── MAIN HANDLER ─────────────────────────────────────────────────────────────

def handler(event: dict, context) -> None:  # noqa: ANN001
    """Entry point – invoked asynchronously; no HTTP response required."""
    job_id: str = event["job_id"]
    prompt: str = event["prompt"]
    settings: dict = event.get("settings", {})
    api_key: str = settings.get("api_key", "")

    client = OpenAI(api_key=api_key, base_url=QWEN_BASE_URL)
    s3 = boto3.client("s3")

    try:
        # ── Stage 1: Research ───────────────────────────────────────────────
        research_md = _research(prompt, client, job_id)
        _update_job(job_id, research_md=research_md)

        # ── Stage 2: Structure ──────────────────────────────────────────────
        structure = _structure(prompt, research_md, client, job_id)
        _update_job(job_id, structure_md=json.dumps(structure, indent=2))

        # ── Stage 3a: Generate code ─────────────────────────────────────────
        pptx_code = _build_code(structure, settings, client, job_id)
        _update_job(job_id, pptx_code=pptx_code)

        # ── Stage 3b: Execute with up to 3 self-correction attempts ─────────
        pptx_bytes: bytes | None = None
        last_error = ""

        for attempt in range(3):
            try:
                pptx_bytes = _execute(pptx_code)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if attempt < 2:
                    fix_response = client.chat.completions.create(
                        model=QWEN_MODEL,
                        messages=[
                            {"role": "system", "content": _BUILD_SYSTEM},
                            {"role": "user", "content": (
                                f"The following code raised an error:\n\n{pptx_code}\n\n"
                                f"Error: {last_error}\n\n"
                                "Fix the code and return only the corrected function."
                            )},
                        ],
                        max_tokens=8000,
                    )
                    pptx_code = fix_response.choices[0].message.content or pptx_code
                    _update_job(job_id, pptx_code=pptx_code)

        if pptx_bytes is None:
            raise RuntimeError(f"Code execution failed after 3 attempts. Last error: {last_error}")

        # ── Upload to S3 ────────────────────────────────────────────────────
        key = f"presentations/{job_id}.pptx"
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

        _update_job(
            job_id,
            status="done",
            stage_message="Your presentation is ready!",
            download_url=download_url,
        )

    except Exception as exc:  # noqa: BLE001
        _update_job(
            job_id,
            status="error",
            stage_message="Something went wrong.",
            error_message=str(exc),
        )
        raise
