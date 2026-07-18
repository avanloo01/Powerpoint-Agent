"""
Build-slides Lambda: generates python-pptx code from a presentation structure
and saves it to S3. Runs concurrently with other batches; assembly is handled
by the assemble_slides Lambda once all batches report completion.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import textwrap
import time
from urllib import request as urlrequest

import boto3
from openai import OpenAI

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

S3_OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "")
QWEN_BASE_URL = "https://ws-2mo30drlt9wzxl3g.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen3.7-plus")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
ASSEMBLE_SLIDES_FUNCTION_NAME = os.environ.get("ASSEMBLE_SLIDES_FUNCTION_NAME", "")

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


# ─── ICON RENDERING ──────────────────────────────────────────────────────────

def _recolor_png(png_bytes: bytes, hex_color: str) -> bytes:
    """Recolor a single-colour PNG icon by replacing the RGB of all
    non-transparent pixels with the target hex colour, preserving alpha."""
    from io import BytesIO
    from PIL import Image

    img = Image.open(BytesIO(png_bytes)).convert("RGBA")
    data = bytearray(img.tobytes())
    w, h = img.size
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    for y in range(h):
        for x in range(w):
            idx = (y * w + x) * 4
            if data[idx + 3] > 0:  # non-transparent pixel
                data[idx] = r
                data[idx + 1] = g
                data[idx + 2] = b
    out = BytesIO()
    Image.frombytes("RGBA", (w, h), bytes(data)).save(out, format="PNG")
    out.seek(0)
    return out.read()


# ─── BUILD PROMPT ────────────────────────────────────────────────────────────

_BUILD_SYSTEM = textwrap.dedent("""\
You are an expert python-pptx developer. Write a Python 3.12 function called `build_presentation(prs, logo_bytes=None)` that adds all slides to the given python-pptx Presentation object `prs`. ONLY return the function code, no markdown.

CONSTRAINTS:
- prs: slide_width=Inches(13.33), slide_height=Inches(7.5). Use slide_layouts[6] (blank).
- Never call Presentation(). Never write import statements. Pre-injected globals:
  Inches, Pt, Emu, Cm, RGBColor, PP_ALIGN, ChartData, XL_CHART_TYPE, MSO_SHAPE,
  MSO_ANCHOR, MSO_AUTO_SIZE, io, math, json, BytesIO, Image, ImageEnhance,
  urlrequest, no_shadow, remove_outline, get_image_buf
- slides_data is injected as a FUNCTION PARAMETER (list of slide dicts). Iterate:
  `for slide in slides_data:` — NEVER re-define slides_data or embed slide data as Python dict/list literals.
- Always use enum constants, not integers: MSO_ANCHOR.MIDDLE not 2; PP_ALIGN.CENTER not 1.

CRITICAL RULES (violating these WILL crash):
- add_picture() needs a file-like object, not raw bytes:
  buf.seek(0); slide.shapes.add_picture(buf, ...)  ← always seek(0) after any BytesIO write.
- Images: NEVER define fetch_bg/get_bg/download_image or call urlrequest for images.
  Only pattern: buf = get_image_buf(url, darken=True/False); slide.shapes.add_picture(buf, ...)
  get_image_buf() is pre-injected, returns a seek'd BytesIO, handles failures gracefully.
- no_shadow(shape): call on every add_shape result. Never shape.shadow.inherit = False.
- remove_outline(shape): call on every filled shape to strip the default blue border.
  Never shape.line.fill.background() directly — crashes on charts/tables/GraphicFrame.
- paragraph.add_run() takes ZERO arguments. Always use the two-step pattern:
    run = paragraph.add_run()
    run.text = "your text here"
  NEVER do run = paragraph.add_run("text") or paragraph.add_run(f"text") — this WILL crash.

STYLE GUIDE:
- COLORS: PRIMARY = RGBColor(...) from prompt for all accents. ACCENT for chart series only.
    Also capture your primary R,G,B as: R1,G1,B1 = 0xC0,0x00,0x00 (the same values you used for PRIMARY).
    These are used by the pie-chart shading template to create progressive lighter shades.
- LAYOUT CONSTANTS (define once at top of each content-slide block):
    CONCLUSION_Y   = sh - Cm(2.4)
    CONCLUSION_H   = Cm(1.2)
    SOURCES_Y      = sh - Cm(0.9)
    CONTENT_BOTTOM = CONCLUSION_Y - Cm(0.2)
- title_slide: if image_url provided: get_image_buf(url, darken=True) → add_picture(buf,0,0,sw,sh).
  If image_url is null: add_shape(RECTANGLE,0,0,sw,sh) filled RGBColor(40,40,40), no_shadow, remove_outline.
  Title 54pt bold white centered.
- section_divider: if image_url provided: get_image_buf(url, darken=True) → add_picture(buf,0,0,sw,sh).
  If image_url is null: add_shape(RECTANGLE,0,0,sw,sh) filled RGBColor(40,40,40), no_shadow, remove_outline.
  Then:
    wy = sh - Cm(5.74)
    a) White rect (0, wy, sw, Cm(5.74))
    b) Number square (0, wy, Cm(5.74), Cm(5.74)): PRIMARY fill, "{n:02d}" white bold 48pt, CENTER+MIDDLE
    c) Title textbox (Cm(6.27), wy+Cm(0.8), sw-Cm(7.27), Cm(2.5)): bold black 32pt, word_wrap=True
       tf.auto_size = MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT  ← required so .height is accurate
    d) Section label: y = title_tb.top + title_tb.height + Cm(0.2), gray 16pt. NEVER hardcode y.
- content_slide: ALWAYS set `slide.background.fill.solid(); slide.background.fill.fore_color.rgb = RGBColor(255,255,255)` as the FIRST two lines of every content-slide block — skipping this leaves a grey default background. No white rect shape.
  FIXED positions (do NOT derive from margin — they must clear BOX_HEADER_Y=Cm(3.05)):
    Section label: x=margin, y=Cm(0.5), w=sw-2*margin, h=Cm(0.5), 9pt gray, word_wrap=True
    Title:         x=margin, y=Cm(1.1), w=sw-2*margin, h=Cm(1.8), 22pt bold black, word_wrap=True
- box_headers: FIXED coordinates — define once at the top of each content-slide block:
    BOX_HEADER_Y = Cm(3.05)
    BOX_HEADER_H = Cm(0.81)
  PRIMARY rect at (col_x+Inches(0.08), BOX_HEADER_Y, col_w-Inches(0.16), BOX_HEADER_H),
  white bold 12pt, margin_left=Inches(0.1).
  CRITICAL: Draw a box_header for EVERY column that has a non-null "box_header" string — this includes chart columns, bullet-list columns, and text columns alike.
- column subtitle: if a column's "subtitle" field is non-null, add a textbox
  (col_x+Inches(0.08), BOX_HEADER_Y+BOX_HEADER_H, col_w-Inches(0.16), Cm(0.4)): 8pt gray italic text.
  Then increment content_y: `content_y += Cm(0.4) + Inches(0.05)`.
  NOTE: content_y must be initialized ONCE per column, BEFORE any content_type rendering:
    `content_y = BOX_HEADER_Y + BOX_HEADER_H + Inches(0.1)`
  This applies to ALL columns — chart, bullet_list, text, etc.
- bullets: call the pre-injected `render_bullets(slide_shape, col_x, col_w, content_y, bullets, icons, PRIMARY)`.
  It handles icons (Pt(22) pictures or PRIMARY oval fallback), title (bold 10pt), description (8pt),
  auto-sizing, and spacing (Inches(0.6) per bullet). Returns the new content_y after the last bullet.
  slide_shape = your pptx Slide object (result of add_slide), NOT the data dict.
  CRITICAL: NEVER render Unicode symbols (↑ ↓ → ← ✓ ✗ ● ◆ •) as text characters in title or description.
- separators: usable = sw - 2*margin - gap; col_w = int(usable * width_ratio).  ← MUST use int() — float EMU values corrupt the PPTX XML.
    LIGHT_GRAY = RGBColor(211, 211, 211)
    sep_x = col1_x + col1_w + gap/2 - Pt(0.25)
    line_top = min(col_tops); line_bottom = CONTENT_BOTTOM
    Draw Pt(0.5) × (line_bottom - line_top) LIGHT_GRAY rect at sep_x.
    "line": rect only. No triangle.
    "causal_line": rect + right-pointing triangle whose base sits on the separator line:
      # After rotation=90 the visual base is at tri_cx-Pt(4); shift tri_cx right so base lands on the line.
      tri_cx = sep_x + Pt(4.25)  # base at tri_cx-Pt(4) = sep_x+Pt(0.25) ← on the line
      tri_cy = (line_top + line_bottom) / 2
      tri = add_shape(ISOSCELES_TRIANGLE, tri_cx-Pt(6), tri_cy-Pt(4), Pt(12), Pt(8))
      tri.rotation = 90  # apex points right; visual width=Pt(8), visual height=Pt(12)
      tri.fill.solid(); tri.fill.fore_color.rgb = LIGHT_GRAY; no_shadow(tri); remove_outline(tri)
- conclusion_box: if non-null, draw BEFORE sources using MSO_SHAPE.RECTANGLE (NEVER ROUNDED_RECTANGLE):
    box = add_shape(RECTANGLE, margin, CONCLUSION_Y, sw-2*margin, CONCLUSION_H)
    box.fill.background(); no_shadow(box)  ← do NOT remove_outline — border is intentional
    box.line.color.rgb = PRIMARY; box.line.width = Pt(1.0)
    tf: word_wrap=True, MIDDLE anchor, 0.15in margins, 9pt black PP_ALIGN.CENTER text
- sources: draw LAST at SOURCES_Y. 8pt gray left-aligned.
  RENDER ORDER: columns → separator → conclusion_box → sources → logo
- logo: top-right, ~0.6in tall, BytesIO(logo_bytes).
- charts: Use EXACTLY this pattern — all four types are handled via a dict map with fallback:
    _CHART_TYPE_MAP = {
        'bar': XL_CHART_TYPE.BAR_CLUSTERED,
        'grouped_bar': XL_CHART_TYPE.COLUMN_CLUSTERED,
        'line': XL_CHART_TYPE.LINE,
        'pie': XL_CHART_TYPE.PIE,
    }
    ct = _CHART_TYPE_MAP.get(ch['chart_type'], XL_CHART_TYPE.COLUMN_CLUSTERED)
    cd = ChartData()
    cd.categories = ch['x_labels']
    for s in ch['series']:
        cd.add_series(s['name'], s['values'])
    content_y = BOX_HEADER_Y + BOX_HEADER_H + Inches(0.1)
    chart_h = max(CONTENT_BOTTOM - content_y - Inches(0.1), Inches(2.5))
    # if column also has bullets below chart: chart_h = (CONTENT_BOTTOM - content_y) * 0.55
    cf = slide.shapes.add_chart(ct, col_x+Inches(0.08), content_y, col_w-Inches(0.16), chart_h, cd)
    ...
    content_y += chart_h + Inches(0.1)
    # if column also has bullets: content_y = render_bullets(<slide_shape>, col_x, col_w, content_y, col["bullets"], icons, PRIMARY)
    chart_obj = cf.chart
    chart_obj.chart_style = 2
    if ch['chart_type'] in ('bar', 'grouped_bar'):
        chart_obj.value_axis.has_major_gridlines = False
        chart_obj.value_axis.visible = False
        plot = chart_obj.plots[0]
        plot.has_data_labels = True
        plot.data_labels.show_value = True
        for i, ser in enumerate(chart_obj.series):
            ser.format.fill.solid()
            ser.format.fill.fore_color.rgb = PRIMARY if i == 0 else ACCENT
    elif ch['chart_type'] == 'line':
        chart_obj.value_axis.has_major_gridlines = False
        for i, ser in enumerate(chart_obj.series):
            ser.format.line.color.rgb = PRIMARY if i == 0 else ACCENT
    elif ch['chart_type'] == 'pie':
        plot = chart_obj.plots[0]
        plot.has_data_labels = True
        plot.data_labels.show_category_name = True
        plot.data_labels.show_percentage = True
        # Add a legend at the bottom so slice categories are visible
        chart_obj.has_legend = True
        chart_obj.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart_obj.legend.include_in_layout = False
        # Color slices: progressive lighter shades of PRIMARY (blend toward white).
        # Use the numeric RGB components from your PRIMARY definition (e.g. R1=0xC0, G1=0x00, B1=0x00).
        for j, pt in enumerate(chart_obj.series[0].points):
            t = (j + 1) / (len(chart_obj.series[0].points) + 1)
            shade = RGBColor(
                int(R1 + (255 - R1) * t),
                int(G1 + (255 - G1) * t),
                int(B1 + (255 - B1) * t),
            )
            pt.format.fill.solid()
            pt.format.fill.fore_color.rgb = shade
  Never call chart.replace_data() — build all data via ChartData at creation time.
""")


BATCH_SIZE = 4


def _build_batch_code(
    batch_slides: list,
    batch_num: int,
    total_batches: int,
    settings: dict,
    client: OpenAI,
    job_id: str,
    has_logo: bool = False,
    has_icons: bool = False,
) -> str:
    """Generate python-pptx code for a single batch of slides."""
    primary = settings.get("primary_color", "#C00000")
    accent = settings.get("accent_color", "#A6CAEC")
    fn_name = f"build_batch_{batch_num}"
    logo_note = (
        "A `logo_bytes` PARAMETER will be passed to your function — include it in your signature. "
        "Place the logo on every slide."
        if has_logo else "No logo will be provided — your function does NOT need a `logo_bytes` parameter."
    )
    icon_note = (
        "An `icons` dict (filename → PNG bytes) is passed as a PARAMETER to your function — "
        f"include it in your signature: def {fn_name}(prs, logo_bytes=None, icons=None, slides_data=None). "
        "Use icons[bullet['icon']] as a BytesIO source for add_picture()."
        if has_icons else "No icons are provided — omit the `icons` parameter. Use MSO_SHAPE.OVAL Pt(10), PRIMARY fill as fallback."
    )
    batch_structure: dict = {"slides": batch_slides}
    structure_json = json.dumps(batch_structure, indent=2)
    user_msg = (
        f"Primary color: {primary}\n"
        f"Accent color: {accent}\n"
        f"Logo: {logo_note}\n"
        f"Icons: {icon_note}\n"
        f"IMPORTANT: Name your function `{fn_name}`. Your function signature MUST include `slides_data=None`: "
        f"`def {fn_name}(prs, logo_bytes=None, icons=None, slides_data=None):` — omit logo_bytes/icons only if told they're not provided, but ALWAYS include slides_data.\n"
        f"The batch slides are injected as `slides_data` (a Python list). Iterate: `for slide in slides_data:`. "
        f"DO NOT re-define slides_data or encode any slide data as Python dict/list literals in your code body.\n\n"
        f"Presentation structure (reference only — iterate slides_data at runtime, do not copy this JSON into code):\n{structure_json}"
    )
    prompt_len = len(_BUILD_SYSTEM) + len(user_msg)
    last_exc = None
    for retry in range(3):
        try:
            print(f"[{job_id}] Batch {batch_num}/{total_batches}: API call ({prompt_len} chars, timeout=600s, attempt {retry+1}/3)...")
            response = client.chat.completions.create(
                model=QWEN_MODEL,
                messages=[
                    {"role": "system", "content": _BUILD_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=16000,
                timeout=600.0,
            )
            code = response.choices[0].message.content or ""
            print(f"[{job_id}] Batch {batch_num}/{total_batches}: received {len(code)} chars")
            return code
        except Exception as exc:
            last_exc = exc
            print(f"[{job_id}] Batch {batch_num}/{total_batches}: API attempt {retry+1} failed: {exc}")
            if retry < 2:
                wait = (retry + 1) * 30
                print(f"[{job_id}] Waiting {wait}s before retry...")
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ─── MAIN HANDLER ─────────────────────────────────────────────────────────────

def handler(event: dict, context) -> None:  # noqa: ANN001
    """Entry point — generates python-pptx code for one batch of slides,
    saves it to S3, and triggers assembly when all batches are done."""
    job_id: str = event["job_id"]
    structure: dict = event["structure"]
    settings: dict = event.get("settings", {})
    api_key: str = settings.get("api_key", "")
    logo_url: str = settings.get("logo_url", "")
    batch_num: int = event["batch_num"]
    total_batches: int = event["total_batches"]

    print(f"[{job_id}] Build-slides batch {batch_num}/{total_batches} started.")

    client = OpenAI(api_key=api_key, base_url=QWEN_BASE_URL, timeout=600.0)
    s3 = boto3.client("s3")

    try:
        # ── Get this batch's slides ────────────────────────────────────────
        slides: list = structure.get("slides", [])
        batch_slides = slides[(batch_num - 1) * BATCH_SIZE : batch_num * BATCH_SIZE]
        total_slides = len(slides)
        start_slide = (batch_num - 1) * BATCH_SIZE + 1
        end_slide = min(batch_num * BATCH_SIZE, total_slides)

        _update_job(job_id, stage_message=f"Building slides {start_slide}-{end_slide} of {total_slides}…")

        # ── Download logo (for prompt context) ─────────────────────────────
        logo_bytes: bytes | None = None
        if logo_url:
            try:
                req = urlrequest.Request(url=logo_url, method="GET")
                with urlrequest.urlopen(req, timeout=15) as resp:
                    logo_bytes = resp.read()
                print(f"[{job_id}] Logo downloaded ({len(logo_bytes)} bytes)")
            except Exception:  # noqa: BLE001
                logo_bytes = None

        # ── Pre-download background images for this batch ──────────────────
        image_urls: set[str] = set()
        for slide in batch_slides:
            url = slide.get("image_url")
            if url:
                image_urls.add(url)
        image_buffers: dict[str, bytes] = {}
        if image_urls:
            print(f"[{job_id}] Downloading {len(image_urls)} unique images for batch...")
            for url in sorted(image_urls):
                try:
                    req = urlrequest.Request(url=url, method="GET")
                    with urlrequest.urlopen(req, timeout=15) as resp:
                        image_buffers[url] = resp.read()
                except Exception:  # noqa: BLE001
                    print(f"[{job_id}] Failed to download image {url[:80]}..., skipping")
            print(f"[{job_id}] Downloaded {len(image_buffers)}/{len(image_urls)} images")

        # ── Download icons for this batch ──────────────────────────────────
        icons: dict[str, bytes] = {}
        icon_names: set[str] = set()
        for slide in batch_slides:
            for col in slide.get("columns", []):
                for bullet in col.get("bullets") or []:
                    icon_name = bullet.get("icon", "")
                    if icon_name:
                        icon_names.add(icon_name)
        if icon_names:
            primary_color = settings.get("primary_color", "#C00000")
            print(f"[{job_id}] Downloading {len(icon_names)} unique icons for batch...")
            for name in sorted(icon_names):
                try:
                    buf = io.BytesIO()
                    png_name = name.replace(".svg", ".png")
                    s3.download_fileobj(S3_OUTPUT_BUCKET, f"icons/{png_name}", buf)
                    recolored = _recolor_png(buf.getvalue(), primary_color)
                    icons[name] = recolored
                except Exception:  # noqa: BLE001
                    print(f"[{job_id}] Failed to download icon {name}, skipping")
            print(f"[{job_id}] Downloaded {len(icons)}/{len(icon_names)} icons")

        has_logo = logo_bytes is not None
        has_icons = len(icons) > 0

        # ── Generate code via AI ───────────────────────────────────────────
        batch_code = _build_batch_code(
            batch_slides, batch_num, total_batches, settings, client, job_id, has_logo, has_icons,
        )

        # ── Save code to S3 ────────────────────────────────────────────────
        s3.put_object(
            Bucket=S3_OUTPUT_BUCKET,
            Key=f"wip/{job_id}/code_{batch_num}.txt",
            Body=batch_code.encode(),
        )
        print(f"[{job_id}] Batch {batch_num}/{total_batches}: code saved to S3 ({len(batch_code)} chars)")

        # ── Write done marker ──────────────────────────────────────────────
        s3.put_object(
            Bucket=S3_OUTPUT_BUCKET,
            Key=f"wip/{job_id}/done_{batch_num}",
            Body=b"",
        )

        # ── Trigger assembly if all batches have reported ──────────────────
        done_objs = s3.list_objects_v2(
            Bucket=S3_OUTPUT_BUCKET,
            Prefix=f"wip/{job_id}/done_",
        )
        done_count = len(done_objs.get("Contents", []))

        failed_objs = s3.list_objects_v2(
            Bucket=S3_OUTPUT_BUCKET,
            Prefix=f"wip/{job_id}/failed_",
        )
        failed_count = len(failed_objs.get("Contents", []))

        print(f"[{job_id}] Batch {batch_num}/{total_batches}: {done_count} done, {failed_count} failed of {total_batches} total")

        if done_count + failed_count >= total_batches:
            print(f"[{job_id}] All batches reported. Invoking assemble_slides...")
            boto3.client("lambda").invoke(
                FunctionName=ASSEMBLE_SLIDES_FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps({
                    "job_id": job_id,
                    "structure": structure,
                    "settings": settings,
                    "total_batches": total_batches,
                }).encode(),
            )
            _update_job(job_id, stage_message="Assembling your presentation…")

    except Exception as exc:  # noqa: BLE001
        print(f"[{job_id}] Batch {batch_num}/{total_batches} ERROR: {exc}")
        # Write failed marker so assembly can still proceed with remaining batches
        try:
            s3.put_object(
                Bucket=S3_OUTPUT_BUCKET,
                Key=f"wip/{job_id}/failed_{batch_num}",
                Body=str(exc).encode(),
            )
        except Exception:  # noqa: BLE001
            pass

        # Check if this was the last batch to report (including failures)
        try:
            done_objs = s3.list_objects_v2(
                Bucket=S3_OUTPUT_BUCKET,
                Prefix=f"wip/{job_id}/done_",
            )
            done_count = len(done_objs.get("Contents", []))
            failed_objs = s3.list_objects_v2(
                Bucket=S3_OUTPUT_BUCKET,
                Prefix=f"wip/{job_id}/failed_",
            )
            failed_count = len(failed_objs.get("Contents", []))
            if done_count + failed_count >= total_batches:
                print(f"[{job_id}] All batches reported (with failures). Invoking assemble_slides...")
                boto3.client("lambda").invoke(
                    FunctionName=ASSEMBLE_SLIDES_FUNCTION_NAME,
                    InvocationType="Event",
                    Payload=json.dumps({
                        "job_id": job_id,
                        "structure": structure,
                        "settings": settings,
                        "total_batches": total_batches,
                    }).encode(),
                )
                _update_job(job_id, stage_message="Assembling slides (some batches may be incomplete)…")
        except Exception:  # noqa: BLE001
            pass

        raise
