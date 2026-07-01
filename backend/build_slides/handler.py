"""
Build-slides Lambda: generates python-pptx code from a presentation structure,
executes it, and uploads the resulting PPTX to S3.
Invoked asynchronously by agent_loop after research & structure are complete.
"""
from __future__ import annotations

import builtins as _builtins
import io
import json
import math
import os
import re
import textwrap
from urllib import request as urlrequest

import boto3
from openai import OpenAI

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

S3_OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "")
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen3.6-plus")
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
    except Exception:  # noqa: BLE001
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


# ─── BUILD AGENT ─────────────────────────────────────────────────────────────

_BUILD_SYSTEM = textwrap.dedent("""\
You are an expert python-pptx developer. Write a Python function called
`build_presentation(prs, logo_bytes=None)` that adds all slides to the given
python-pptx Presentation object `prs`. ONLY return the function code, no markdown.

CONSTRAINTS:
- `prs` has slide_width=Inches(13.33), slide_height=Inches(7.5). Use slide_layouts[6] (blank).
- DO NOT call Presentation() — use the `prs` argument.
- Available: Inches, Pt, Emu, Cm, RGBColor, PP_ALIGN, ChartData, XL_CHART_TYPE, io, math, json,
  urlrequest (urllib.request), Image, ImageEnhance (PIL), BytesIO
- Return ONLY valid Python 3.12 function code (no fences, no extra text).

STYLE GUIDE (concise):
- title_slide: download image, darken (ImageEnhance.Brightness, factor 0.6), insert as full-slide bg. Title bold 54pt white, centered.
- section_divider: download & darken image as full-slide bg, then in order:
  1. White rect: x=0, y=sh-Cm(5.74), w=sw, h=Cm(5.74)
  2. Accent square (primary fill): x=0, same y, w=h=Cm(5.74), section number bold white 48pt centered
  3. Title textbox: x=Cm(6.27), y=Cm(14.7), w=sw-Cm(6.27), h=Cm(3.5), bold black 32pt
  4. Subtitle textbox: x=Cm(6.27), y=Cm(16.6), section label RGB(128,128,128) 16pt
- Slide bg: white. Section label: top-left 9pt RGB(128,128,128). Slide title: bold 22pt black below label.
- Box headers: primary-fill rects, white bold 12pt text, with padding (don't span full column width).
- Column separators: 0.5pt light-gray line centered between columns. Causal: add triangle arrow mid-line.
- Bullets: filled circle + bold title + description text.
- Charts: ChartData + slide.shapes.add_chart().
- Conclusion: 1pt primary border, centered italic 11pt, width = single-column width.
- Sources: bottom-left 8pt RGB(128,128,128).
- Logo: if logo_bytes, place on EVERY slide top-right (0.5-0.7in tall, right edge aligns with rightmost column, top with section label/title). Use BytesIO(logo_bytes) + add_picture().
""")


def _build_code(structure: dict, settings: dict, client: OpenAI, job_id: str, has_logo: bool = False) -> str:
    """Generate python-pptx code from the structure blueprint."""
    primary = settings.get("primary_color", "#C00000")
    accent = settings.get("accent_color", "#A6CAEC")
    logo_note = "A logo_bytes parameter WILL be provided, place the logo on every slide." if has_logo else "No logo will be provided."
    user_msg = (
        f"Primary color: {primary}\n"
        f"Accent color: {accent}\n"
        f"Logo: {logo_note}\n\n"
        f"Presentation structure:\n{json.dumps(structure, indent=2)}"
    )
    prompt_len = len(_BUILD_SYSTEM) + len(user_msg)
    print(f"[{job_id}] API call starting (prompt ~{prompt_len} chars, max_tokens=5000)...")
    response = client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": _BUILD_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=5000,
    )
    code = response.choices[0].message.content or ""
    print(f"[{job_id}] API call complete, received {len(code)} chars of code")
    return code


# ─── EXECUTE GENERATED CODE ───────────────────────────────────────────────────

def _execute(code: str, logo_bytes: bytes | None = None) -> bytes:
    """Execute AI-generated build_presentation() in a restricted namespace, return PPTX bytes."""
    import pptx
    import pptx.chart.data
    import pptx.dml.color
    import pptx.enum.chart
    import pptx.enum.text
    import pptx.util
    from io import BytesIO
    from PIL import Image, ImageEnhance
    from pptx import Presentation as _Prs

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
        "Inches": pptx.util.Inches,
        "Pt": pptx.util.Pt,
        "Emu": pptx.util.Emu,
        "Cm": pptx.util.Cm,
        "RGBColor": pptx.dml.color.RGBColor,
        "PP_ALIGN": pptx.enum.text.PP_ALIGN,
        "ChartData": pptx.chart.data.ChartData,
        "XL_CHART_TYPE": pptx.enum.chart.XL_CHART_TYPE,
        "io": io,
        "math": math,
        "json": json,
        "urlrequest": urlrequest,
        "Image": Image,
        "ImageEnhance": ImageEnhance,
        "BytesIO": BytesIO,
    }

    exec(code, namespace)  # noqa: S102
    print("[_execute] exec() completed, looking for build_presentation...")

    build_fn = namespace.get("build_presentation")
    if not callable(build_fn):
        raise ValueError("Generated code does not define a callable 'build_presentation'")

    print("[_execute] Creating Presentation and running build_presentation...")
    prs = _Prs()
    prs.slide_width = pptx.util.Inches(13.33)
    prs.slide_height = pptx.util.Inches(7.5)
    build_fn(prs, logo_bytes=logo_bytes)
    print("[_execute] build_presentation() completed, saving PPTX...")

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    print(f"[_execute] PPTX saved ({buf.getbuffer().nbytes} bytes)")
    return buf.read()


# ─── MAIN HANDLER ─────────────────────────────────────────────────────────────

def handler(event: dict, context) -> None:  # noqa: ANN001
    """Entry point – invoked asynchronously by agent_loop."""
    job_id: str = event["job_id"]
    structure: dict = event["structure"]
    settings: dict = event.get("settings", {})
    api_key: str = settings.get("api_key", "")
    logo_url: str = settings.get("logo_url", "")

    print(f"[{job_id}] Build-slides started. {len(structure.get('slides', []))} slides to build.")

    client = OpenAI(api_key=api_key, base_url=QWEN_BASE_URL, timeout=600.0)
    s3 = boto3.client("s3")

    try:
        # ── Stage 0: Download logo ─────────────────────────────────────────
        print(f"[{job_id}] Stage 0: Checking for logo...")
        logo_bytes: bytes | None = None
        if logo_url:
            try:
                req = urlrequest.Request(url=logo_url, method="GET")
                with urlrequest.urlopen(req, timeout=15) as resp:
                    logo_bytes = resp.read()
                print(f"[{job_id}] Stage 0: Logo downloaded ({len(logo_bytes)} bytes)")
            except Exception:  # noqa: BLE001
                logo_bytes = None

        # ── Stage 1: Generate code ─────────────────────────────────────────
        print(f"[{job_id}] Stage 1: Generating PPTX code...")
        _update_job(job_id, status="building", stage_message="Building your presentation\u2026")
        pptx_code = _build_code(structure, settings, client, job_id, has_logo=logo_bytes is not None)
        _update_job(job_id, pptx_code=pptx_code)

        # ── Stage 2: Execute with up to 3 self-correction attempts ─────────
        print(f"[{job_id}] Stage 2: Executing generated code...")
        pptx_bytes: bytes | None = None
        last_error = ""

        for attempt in range(3):
            try:
                pptx_bytes = _execute(pptx_code, logo_bytes=logo_bytes)
                print(f"[{job_id}] Stage 2: Execution succeeded on attempt {attempt + 1}")
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[{job_id}] Stage 2: Attempt {attempt + 1} failed: {exc}")
                last_error = str(exc)
                if attempt < 2:
                    _update_job(job_id, stage_message=f"Fixing code error (attempt {attempt + 2}/3)\u2026")
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
                        max_tokens=5000,
                    )
                    pptx_code = fix_response.choices[0].message.content or pptx_code
                    _update_job(job_id, pptx_code=pptx_code)

        if pptx_bytes is None:
            raise RuntimeError(f"Code execution failed after 3 attempts. Last error: {last_error}")

        # ── Stage 3: Upload to S3 ──────────────────────────────────────────
        print(f"[{job_id}] Stage 3: Uploading to S3...")
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
        print(f"[{job_id}] DONE. Download: {download_url}")

    except Exception as exc:  # noqa: BLE001
        print(f"[{job_id}] ERROR: {exc}")
        _update_job(
            job_id,
            status="error",
            stage_message="Something went wrong.",
            error_message=str(exc),
        )
        raise
