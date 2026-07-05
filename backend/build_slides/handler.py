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
import time
import traceback
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


# ─── ICON RENDERING (Pillow-only, no cairo needed) ────────────────────────────

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


# ─── BUILD AGENT ─────────────────────────────────────────────────────────────

_BUILD_SYSTEM = textwrap.dedent("""\
You are an expert python-pptx developer. Write a Python function called
`build_presentation(prs, logo_bytes=None)` that adds all slides to the given
python-pptx Presentation object `prs`. ONLY return the function code, no markdown.

CONSTRAINTS:
- `prs` has slide_width=Inches(13.33), slide_height=Inches(7.5). Use slide_layouts[6] (blank).
- DO NOT call Presentation() — use the `prs` argument.
- DO NOT write ANY import statements. The following names are already injected as global variables and can be used directly without importing:
  Inches, Pt, Emu, Cm, RGBColor, PP_ALIGN, ChartData, XL_CHART_TYPE, MSO_SHAPE, MSO_ANCHOR, MSO_AUTO_SIZE, io, math, json, urlrequest (urllib.request), Image, ImageEnhance (PIL), BytesIO, no_shadow (safe shadow-disabler)
- Return ONLY valid Python 3.12 function code (no fences, no extra text).

CRITICAL RULES (violating these WILL crash):
- add_picture() expects a FILE-LIKE object (BytesIO or file path). NEVER pass raw bytes.
  WRONG: img_bytes = buf.getvalue(); slide.shapes.add_picture(img_bytes, ...)
  RIGHT: buf.seek(0); slide.shapes.add_picture(buf, ...)
- After writing to a BytesIO, ALWAYS call buf.seek(0) before using it.
- NEVER write import statements. All names (Inches, Pt, BytesIO, Image, etc.) are pre-injected.
- NEVER call Presentation() — use the `prs` argument.
- Image backgrounds: use the pre-injected `get_image_buf(url, darken=False)` function. It returns a BytesIO (already seek'd to 0) ready for add_picture(). Do NOT use urlrequest to download images — all images referenced in the structure are pre-downloaded and available via get_image_buf(). If darken=True, the image is already darkened with ImageEnhance(0.6).
  NEVER redefine no_shadow(). It is pre-injected and already safely handles NotImplementedError for shapes that don't support .shadow (tables, charts, GraphicFrame).
  Just call no_shadow(shape) directly on every add_shape result.
  WRONG: text_frame.vertical_anchor = 2        WRONG: paragraph.alignment = 1
  RIGHT: text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
  RIGHT: paragraph.alignment = PP_ALIGN.CENTER
  Same for MSO_SHAPE, XL_CHART_TYPE, etc.

STYLE GUIDE:
- COLOR CONSTANTS: At the top of your function, define PRIMARY = RGBColor(...) using the primary color from the prompt. Use PRIMARY for all colored elements.
  Only define ACCENT = RGBColor(...) if you add charts — use it for chart series only.
- title_slide: bg=darkened image (ImageEnhance 0.6). Title 54pt bold white centered, word_wrap=True.
- section_divider: bg=darkened image, then:
  white_rect_y = prs.slide_height - Cm(5.74)
  a) White rect: 0, white_rect_y, sw, Cm(5.74). b) Number square: 0, white_rect_y, Cm(5.74)×Cm(5.74),
     PRIMARY fill, f"{sn:02d}" white bold 48pt, PP_ALIGN.CENTER + MSO_ANCHOR.MIDDLE.
  c) Title: Cm(6.27), white_rect_y+Cm(0.8), sw-Cm(7.27), h=Cm(2.5), word_wrap, bold black 32pt.
     THEN tf.auto_size = MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT (so .height reflects real text).
  d) Section Label: y = title_tb.top+title_tb.height+Cm(0.2), same x/w, h=Cm(0.6), gray 16pt.
     NEVER hardcode y — derive from title_tb.
- content_slide: use slide.background.fill.solid(); slide.background.fill.fore_color.rgb = RGBColor(255,255,255). Do NOT add a white rectangle shape.
  Section label 9pt gray top-left. Title 22pt bold black below, word_wrap=True.
- box_headers: PRIMARY rect, white bold 12pt. Inset: x=col_x+Inches(0.08), w=col_w-Inches(0.16),
  margin_left=Inches(0.1).
- bullets: icons dict → BytesIO → add_picture Pt(22)×Pt(22). Stack title (bold 10pt) then description (8pt) below it with a small gap. Vertically center icon with the title+desc block: icon_y = bullet_top + (title_h+desc_h+gap - Pt(22))/2.
  Fallback only if icon missing: MSO_SHAPE.OVAL Pt(10), PRIMARY fill.
- separators: FIRST compute column widths correctly: usable = sw - 2*margin - gap, then col_w = usable * width_ratio. This ensures the gap is real space between columns.
  Pt(0.5) rect LIGHT_GRAY at sep_x = col1_x + col1_w + gap/2 - Pt(0.25).
  line_top = min(col_tops). line_bottom = Cm(15.93) - Inches(0.15).
  Causal: MSO_SHAPE.ISOSCELES_TRIANGLE Pt(12)×Pt(8), rotation=90, left=sep_x (base on line), same gray fill, vertically centered at line midpoint.
- SHAPE OUTLINES: After creating any filled shape, call remove_outline(shape) to remove the default blue border. This is pre-injected and safely skips shapes that don't support .line (charts, tables, GraphicFrame). NEVER call shape.line.fill.background() directly — it crashes on charts.
- sources: bottom-left 8pt gray.
- logo: top-right ~0.6in tall, BytesIO(logo_bytes).
- charts: Use python-pptx native charts (ChartData + add_chart). Simple, clean styling:
  Remove gridlines: chart.value_axis.has_major_gridlines = False.
  Remove chart border: chart.element.get_or_add_cTChartSpace().get_or_add_cTChart().get_or_add_cTPlotArea().get_or_add_cTPlotArea().spPr is not present by default, so just set chart.chart_style = 2 for a clean look.
  Colors: series.format.fill.solid(); series.format.fill.fore_color.rgb = PRIMARY.
  For second series use ACCENT. Data labels: plot.has_data_labels = True;
  data_labels = plot.data_labels; data_labels.show_value = True.
  Bar charts: hide value axis via chart.value_axis.visible = False;
  category axis stays visible. Pie charts: data_labels.show_percentage = True.
  NEVER call chart.replace_data() or replace_series_data() — always build all chart data through ChartData at creation time and pass it directly to add_chart().
- no_shadow() on EVERY add_shape result. NEVER shape.shadow.inherit = False.
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
        f"include it in your signature: def {fn_name}(prs, logo_bytes=None, icons=None). "
        "Use icons[bullet['icon']] as a BytesIO source for add_picture()."
        if has_icons else "No icons are provided — your function does NOT need an `icons` parameter. Use MSO_SHAPE.OVAL Pt(10), PRIMARY fill as fallback."
    )
    batch_structure: dict = {"slides": batch_slides}
    structure_json = json.dumps(batch_structure, indent=2)
    user_msg = (
        f"Primary color: {primary}\n"
        f"Accent color: {accent}\n"
        f"Logo: {logo_note}\n"
        f"Icons: {icon_note}\n"
        f"IMPORTANT: Name your function `{fn_name}`. Your function signature must be "
        f"`def {fn_name}(prs, logo_bytes=None, icons=None):` (omit logo_bytes/icons only if told they're not provided).\n\n"
        f"Presentation structure:\n{structure_json}"
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


# ─── EXECUTE GENERATED CODE ───────────────────────────────────────────────────

def _make_namespace(image_buffers: dict[str, bytes] | None = None) -> dict:
    """Create the restricted execution namespace with pre-injected globals."""
    import pptx
    import pptx.chart.data
    import pptx.dml.color
    import pptx.enum.chart
    import pptx.enum.text
    import pptx.enum.shapes
    import pptx.util
    from io import BytesIO
    from PIL import Image, ImageEnhance

    # ── Patch pptx to accept float coordinates (AI often divides EMU ints) ──
    import pptx.oxml.simpletypes as _simpletypes
    _orig_validate_int = _simpletypes.BaseSimpleType.validate_int

    @staticmethod
    def _lenient_validate_int(value: int | float) -> None:
        """Validate but auto-convert float → int (safe: EMU rounding is sub-pixel)."""
        if isinstance(value, float):
            value = int(value)
        return _orig_validate_int(value)

    _simpletypes.BaseSimpleType.validate_int = _lenient_validate_int

    def _no_shadow(shape):
        """Safely disable shadows — silently skips shapes that don't support .shadow (pictures, charts, tables)."""
        try:
            shape.shadow.inherit = False
        except (NotImplementedError, AttributeError):
            pass

    def _remove_outline(shape):
        """Safely remove shape outline — silently skips shapes that don't support .line (charts, tables, GraphicFrame)."""
        try:
            shape.line.fill.background()
        except (NotImplementedError, AttributeError):
            pass

    _SAFE_NAMES = (
        "abs", "bool", "dict", "enumerate", "float", "globals", "hasattr", "int",
        "isinstance", "len", "list", "max", "min", "print", "range",
        "round", "set", "str", "sum", "tuple", "zip",
        "Exception", "ValueError", "TypeError", "KeyError",
        "IndexError", "AttributeError", "RuntimeError", "StopIteration",
    )
    safe_builtins = {
        name: getattr(_builtins, name)
        for name in _SAFE_NAMES
        if hasattr(_builtins, name)
    }

    ns: dict = {
        "__builtins__": safe_builtins,
        "__image_buffers__": image_buffers or {},
        "Inches": pptx.util.Inches,
        "Pt": pptx.util.Pt,
        "Emu": pptx.util.Emu,
        "Cm": pptx.util.Cm,
        "RGBColor": pptx.dml.color.RGBColor,
        "PP_ALIGN": pptx.enum.text.PP_ALIGN,
        "ChartData": pptx.chart.data.ChartData,
        "XL_CHART_TYPE": pptx.enum.chart.XL_CHART_TYPE,
        "MSO_SHAPE": pptx.enum.shapes.MSO_SHAPE,
        "MSO_ANCHOR": pptx.enum.text.MSO_ANCHOR,
        "MSO_AUTO_SIZE": pptx.enum.text.MSO_AUTO_SIZE,
        "io": io,
        "math": math,
        "json": json,
        "urlrequest": urlrequest,
        "Image": Image,
        "ImageEnhance": ImageEnhance,
        "BytesIO": BytesIO,
        "no_shadow": _no_shadow,
        "remove_outline": _remove_outline,
    }
    if image_buffers:
        ns["get_image_buf"] = _get_image_buf_factory(image_buffers)
    return ns


def _get_image_buf_factory(image_buffers: dict[str, bytes]):
    """Return a get_image_buf(url, darken=False) closure that looks up
    pre-downloaded images, wrapping them in BytesIO and optionally darkening."""
    from io import BytesIO
    from PIL import Image, ImageEnhance

    def _get_image_buf(url: str, darken: bool = False):
        data = image_buffers.get(url)
        if data is None:
            # Image failed to download — return a dark placeholder so the slide renders
            placeholder = Image.new("RGB", (1920, 1080), (50, 50, 50))
            buf = BytesIO()
            placeholder.save(buf, format="PNG")
            buf.seek(0)
            return buf
        img = Image.open(BytesIO(data))
        if darken:
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(0.6)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    return _get_image_buf


def _execute_batch(
    code: str, prs: object, namespace: dict, logo_bytes: bytes | None, fn_name: str,
    icons: dict[str, bytes] | None = None,
) -> None:
    """Execute AI-generated batch code, adding slides to the existing prs."""
    code = re.sub(r"```(?:python)?\s*", "", code).strip().rstrip("`").strip()
    exec(code, namespace)  # noqa: S102

    build_fn = namespace.get(fn_name)
    if not callable(build_fn):
        available = [k for k, v in namespace.items() if callable(v) and not k.startswith("_")]
        raise ValueError(
            f"Generated code does not define '{fn_name}'. "
            f"Available callables: {available}"
        )

    kwargs = {"logo_bytes": logo_bytes, "icons": icons or {}}
    # Only pass image_buffers if the function signature accepts it
    import inspect
    sig = inspect.signature(build_fn)
    if "image_buffers" in sig.parameters:
        kwargs["image_buffers"] = namespace.get("__image_buffers__", {})
    build_fn(prs, **kwargs)


# ─── MAIN HANDLER ─────────────────────────────────────────────────────────────

def handler(event: dict, context) -> None:  # noqa: ANN001
    """Entry point – invoked asynchronously. Each invocation processes one batch
    of slides, then chains to itself for the next batch via async Lambda invoke."""
    job_id: str = event["job_id"]
    structure: dict = event["structure"]
    settings: dict = event.get("settings", {})
    api_key: str = settings.get("api_key", "")
    logo_url: str = settings.get("logo_url", "")

    # Lambda-chaining state — absent on the first invocation
    batch_num: int = event.get("batch_num", 1)
    total_batches: int = event.get("total_batches", 0)   # computed on first call
    wip_s3_key: str | None = event.get("wip_s3_key")

    is_first = batch_num == 1
    if is_first:
        print(f"[{job_id}] Build-slides started. {len(structure.get('slides', []))} slides.")
    else:
        print(f"[{job_id}] Batch {batch_num}/{total_batches} invocation started.")

    client = OpenAI(api_key=api_key, base_url=QWEN_BASE_URL, timeout=600.0)
    s3 = boto3.client("s3")

    try:
        # Pre-compute which slides belong to this batch so downloads are scoped
        slides: list = structure.get("slides", [])
        batch_slides = slides[(batch_num - 1) * BATCH_SIZE : batch_num * BATCH_SIZE]

        # ── Always: download logo ──────────────────────────────────────────
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

        # ── Always: pre-download background images ─────────────────────────
        print(f"[{job_id}] Stage 0.2: Pre-downloading background images...")
        image_urls: set[str] = set()
        for slide in batch_slides:
            url = slide.get("image_url")
            if url:
                image_urls.add(url)
        image_buffers: dict[str, bytes] = {}
        if image_urls:
            print(f"[{job_id}] Stage 0.2: Downloading {len(image_urls)} unique images...")
            for url in sorted(image_urls):
                try:
                    req = urlrequest.Request(url=url, method="GET")
                    with urlrequest.urlopen(req, timeout=15) as resp:
                        image_buffers[url] = resp.read()
                except Exception:  # noqa: BLE001
                    print(f"[{job_id}] Stage 0.2: Failed to download image {url[:80]}..., skipping")
            print(f"[{job_id}] Stage 0.2: Downloaded {len(image_buffers)}/{len(image_urls)} images")
        else:
            print(f"[{job_id}] Stage 0.2: No background images in structure")

        # ── Always: download icons ─────────────────────────────────────────
        print(f"[{job_id}] Stage 0.5: Collecting icons from structure...")
        icons: dict[str, bytes] = {}
        icon_names: set[str] = set()
        for slide in batch_slides:
            for col in slide.get("columns", []):
                for bullet in col.get("bullets", []):
                    icon_name = bullet.get("icon", "")
                    if icon_name:
                        icon_names.add(icon_name)
        if icon_names:
            primary_color = settings.get("primary_color", "#C00000")
            print(f"[{job_id}] Stage 0.5: Downloading {len(icon_names)} unique icons (PNG)...")
            for name in sorted(icon_names):
                try:
                    buf = io.BytesIO()
                    png_name = name.replace(".svg", ".png")
                    s3.download_fileobj(S3_OUTPUT_BUCKET, f"icons/{png_name}", buf)
                    # Recolour PNG to the user's primary colour
                    recolored = _recolor_png(buf.getvalue(), primary_color)
                    icons[name] = recolored
                except Exception:  # noqa: BLE001
                    print(f"[{job_id}] Stage 0.5: Failed to download icon {name}, skipping")
            print(f"[{job_id}] Stage 0.5: Downloaded {len(icons)}/{len(icon_names)} icons")
        else:
            print(f"[{job_id}] Stage 0.5: No icons referenced in structure")
        has_icons = len(icons) > 0

        # ── First invocation only: one-time setup ─────────────────────────
        if is_first:
            # Null out any image URLs that failed so the AI won't reference them
            for slide in slides:
                url = slide.get("image_url")
                if url and url not in image_buffers:
                    slide["image_url"] = None

            # Assign section numbers to divider slides
            section_num = 0
            for slide in slides:
                if slide.get("layout") == "section_divider":
                    section_num += 1
                    slide["section_number"] = section_num

            total_slides = len(slides)
            total_batches = math.ceil(total_slides / BATCH_SIZE)
            print(f"[{job_id}] {total_slides} slides → {total_batches} batches of {BATCH_SIZE}")
            _update_job(job_id, status="building", stage_message=f"Building slides 1-{min(BATCH_SIZE, total_slides)} of {total_slides}…")

        # ── Load or create prs ─────────────────────────────────────────────
        from pptx import Presentation as _Prs
        import pptx.util
        if wip_s3_key:
            print(f"[{job_id}] Loading WIP pptx from {wip_s3_key}...")
            wip_buf = io.BytesIO()
            s3.download_fileobj(S3_OUTPUT_BUCKET, wip_s3_key, wip_buf)
            wip_buf.seek(0)
            prs = _Prs(wip_buf)
        else:
            prs = _Prs()
            prs.slide_width = pptx.util.Inches(13.33)
            prs.slide_height = pptx.util.Inches(7.5)

        # ── Process this batch ─────────────────────────────────────────────
        total_slides = len(slides)
        fn_name = f"build_batch_{batch_num}"
        start_slide = (batch_num - 1) * BATCH_SIZE + 1
        end_slide = min(batch_num * BATCH_SIZE, total_slides)
        has_logo = logo_bytes is not None
        namespace = _make_namespace(image_buffers)

        print(f"[{job_id}] Batch {batch_num}/{total_batches}: slides {start_slide}-{end_slide}")
        _update_job(job_id, stage_message=f"Building slides {start_slide}-{end_slide} of {total_slides}…")

        batch_code = _build_batch_code(
            batch_slides, batch_num, total_batches, settings, client, job_id, has_logo, has_icons,
        )

        # Execute with up to 3 self-correction attempts
        batch_ok = False
        last_error = ""
        for attempt in range(3):
            try:
                _execute_batch(batch_code, prs, namespace, logo_bytes, fn_name, icons)
                print(f"[{job_id}] Batch {batch_num}/{total_batches}: executed on attempt {attempt + 1}")
                batch_ok = True
                break
            except Exception as exc:  # noqa: BLE001
                tb = traceback.format_exc()
                print(f"[{job_id}] Batch {batch_num}/{total_batches}: attempt {attempt + 1} FAILED")
                print(f"[{job_id}] Error: {exc}")
                print(f"[{job_id}] Traceback:\n{tb}")
                # Log first 500 chars of failing code for pattern analysis
                code_preview = batch_code[:500].replace("\n", "\\n")
                print(f"[{job_id}] Failing code preview: {code_preview}...")
                last_error = str(exc)
                if attempt < 2:
                    _update_job(job_id, stage_message=f"Fixing batch {batch_num} (attempt {attempt + 2}/3)…")
                    print(f"[{job_id}] Batch {batch_num}/{total_batches}: requesting fix from API...")
                    fix_code = batch_code
                    fix_exc = None
                    for fix_retry in range(3):
                        try:
                            print(f"[{job_id}] Batch {batch_num}/{total_batches}: fix API call (attempt {fix_retry+1}/3)...")
                            fix_response = client.chat.completions.create(
                                model=QWEN_MODEL,
                                messages=[
                                    {"role": "system", "content": _BUILD_SYSTEM},
                                    {"role": "user", "content": (
                                        f"The following code raised an error:\n\n{batch_code}\n\n"
                                        f"Error: {last_error}\n\n"
                                        f"Fix the code and return only the corrected function named `{fn_name}`.\n"
                                        "REMINDER: All modules are already injected as global variables \u2014 "
                                        "remove any import statements and use the pre-injected names directly."
                                    )},
                                ],
                                max_tokens=16000,
                                timeout=600.0,
                            )
                            fix_code = fix_response.choices[0].message.content or batch_code
                            print(f"[{job_id}] Batch {batch_num}/{total_batches}: fix received ({len(fix_code)} chars)")
                            fix_exc = None
                            break
                        except Exception as fix_err:  # noqa: BLE001
                            fix_exc = fix_err
                            print(f"[{job_id}] Batch {batch_num}/{total_batches}: fix API attempt {fix_retry+1} failed: {fix_err}")
                            if fix_retry < 2:
                                wait = (fix_retry + 1) * 10
                                print(f"[{job_id}] Waiting {wait}s before fix retry...")
                                time.sleep(wait)
                    if fix_exc is not None:
                        raise RuntimeError(
                            f"Fix API call failed after 3 attempts: {fix_exc}"
                        ) from fix_exc
                    batch_code = fix_code

        if not batch_ok:
            raise RuntimeError(
                f"Batch {batch_num}/{total_batches} failed after 3 attempts. Last error: {last_error}"
            )

        # ── Save batch code to S3 ──────────────────────────────────────────
        s3.put_object(
            Bucket=S3_OUTPUT_BUCKET,
            Key=f"wip/{job_id}/code_{batch_num}.txt",
            Body=batch_code.encode(),
        )

        # ── Serialize current prs to bytes ─────────────────────────────────
        prs_buf = io.BytesIO()
        prs.save(prs_buf)
        prs_bytes = prs_buf.getvalue()

        if batch_num < total_batches:
            # ── More batches: save WIP and chain to next invocation ────────
            new_wip_key = f"wip/{job_id}/pptx.pptx"
            s3.put_object(Bucket=S3_OUTPUT_BUCKET, Key=new_wip_key, Body=prs_bytes)
            print(f"[{job_id}] WIP saved ({len(prs_bytes)} bytes). Invoking batch {batch_num + 1}/{total_batches}...")
            boto3.client("lambda").invoke(
                FunctionName=context.function_name,
                InvocationType="Event",
                Payload=json.dumps({
                    "job_id": job_id,
                    "structure": structure,
                    "settings": settings,
                    "batch_num": batch_num + 1,
                    "total_batches": total_batches,
                    "wip_s3_key": new_wip_key,
                }).encode(),
            )
            print(f"[{job_id}] Invoked self for batch {batch_num + 1}/{total_batches}")

        else:
            # ── Last batch: collect code, upload final PPTX, mark done ────
            all_code: list[str] = []
            for i in range(1, total_batches + 1):
                try:
                    resp = s3.get_object(Bucket=S3_OUTPUT_BUCKET, Key=f"wip/{job_id}/code_{i}.txt")
                    all_code.append(resp["Body"].read().decode())
                except Exception:  # noqa: BLE001
                    pass
            _update_job(job_id, pptx_code="\n\n# --- BATCH ---\n\n".join(all_code))
            print(f"[{job_id}] Final batch done. Uploading PPTX ({len(prs_bytes)} bytes)...")
            key = f"presentations/{job_id}.pptx"
            s3.put_object(
                Bucket=S3_OUTPUT_BUCKET,
                Key=key,
                Body=prs_bytes,
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

            # Clean up WIP files
            for i in range(1, total_batches + 1):
                try:
                    s3.delete_object(Bucket=S3_OUTPUT_BUCKET, Key=f"wip/{job_id}/code_{i}.txt")
                except Exception:  # noqa: BLE001
                    pass
            try:
                s3.delete_object(Bucket=S3_OUTPUT_BUCKET, Key=f"wip/{job_id}/pptx.pptx")
            except Exception:  # noqa: BLE001
                pass

    except Exception as exc:  # noqa: BLE001
        print(f"[{job_id}] ERROR: {exc}")
        _update_job(
            job_id,
            status="error",
            stage_message="Something went wrong.",
            error_message=str(exc),
        )
        raise
