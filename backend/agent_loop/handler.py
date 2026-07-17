"""
Agent-loop Lambda: orchestrates Research → Structure → Build for a PowerPoint presentation.
Invoked asynchronously by start_job. Updates a Supabase `jobs` row at each stage.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import textwrap
from urllib import request as urlrequest

import boto3
from openai import OpenAI

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

S3_OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "")
QWEN_BASE_URL = "https://ws-2mo30drlt9wzxl3g.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen3.7-plus")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BUILD_SLIDES_FUNCTION_NAME = os.environ.get("BUILD_SLIDES_FUNCTION_NAME", "")

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


# ─── IMAGE URL FILTER ────────────────────────────────────────────────────────
def _check_image_url(url: str, job_id: str) -> bool:
    """Return True only if the URL is reachable and serves image content.
    Tries HEAD first (lightweight); falls back to GET for servers that reject HEAD.
    Also verifies Content-Type starts with 'image/' to reject HTML error pages."""
    for method in ("HEAD", "GET"):
        try:
            req = urlrequest.Request(url=url, method=method)
            with urlrequest.urlopen(req, timeout=8) as resp:
                if resp.status >= 400:
                    print(f"[{job_id}] Filtered image URL (HTTP {resp.status}): {url[:80]}")
                    return False
                ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
                if ct and not ct.startswith("image/"):
                    print(f"[{job_id}] Filtered image URL (non-image Content-Type: {ct}): {url[:80]}")
                    return False
                return True
        except Exception:  # noqa: BLE001
            if method == "HEAD":
                continue  # HEAD rejected by server — retry with GET
            print(f"[{job_id}] Filtered image URL (unreachable): {url[:80]}")
            return False
    return False


def _filter_image_urls(research_md: str, job_id: str) -> str:
    """Remove IMG_URL lines that are unreachable, return 4xx, or don't serve image content."""
    lines = research_md.split("\n")
    filtered: list[str] = []
    removed = 0
    for line in lines:
        if not line.startswith("IMG_URL: "):
            filtered.append(line)
            continue
        url = line[9:].strip()
        if _check_image_url(url, job_id):
            filtered.append(line)
        else:
            removed += 1
    if removed:
        print(f"[{job_id}] Filtered {removed} inaccessible image URL(s)")
    return "\n".join(filtered)


# ─── RESEARCH AGENT ──────────────────────────────────────────────────────────

def _research(prompt: str, client: OpenAI, job_id: str) -> str:
    """Stage 1 – Use Qwen with web search to gather current facts and data."""
    _update_job(job_id, status="researching", stage_message="Researching your topic\u2026")

    system = (
        "You are a research analyst preparing materials for a business presentation. "
        "Use web search to gather current facts, statistics, trends and examples. "
        "Produce a thorough markdown document with these sections:\n"
        "1. **Chart-Ready Data Tables** (MOST IMPORTANT): For every major finding, include a "
        "structured markdown table with numerical data suitable for charts (bar, line, pie, grouped_bar). "
        "Each table must have clear column headers and multiple rows of comparable data. "
        "Look for: time-series trends, market-share breakdowns, regional comparisons, "
        "financial metrics, survey results, rankings, before/after comparisons. "
        "Aim for at least 4-6 distinct data tables covering different dimensions of the topic. "
        "Look hard for sources that specifically include this data. If your sources do not cover "
        "your topic exactly, mention that they are used as a proxy, but this should only be a "
        "last resort. ALWAYS cite your sources in this section too (e.g., [2][8]).\n"
        "2. **Key Facts & Statistics**: bullet-point summary of the most important numbers.\n"
        "3. **Notable Examples & Case Studies**: concrete real-world examples with specific data.\n"
        "4. **Key Takeaways**: 3-5 actionable insights.\n"
        "5. **Sources**: list all sources used, with full URLs.\n"
        "Always include specific numbers, dates, percentages, and units. "
        "When data is scarce on one dimension, search for close proxy or adjacent data "
        "that still supports the narrative (e.g., focusing on the regional industry or "
        "market one step up).\n\n"
        "List every source you used, numbered, in this EXACT format (one per line):\n\n"
        "[1] https://full-specific-url.com/path/to/article | Source Name or Title (in English)\n"
        "[2] https://another-full-url.com/report-page | Another Source Title (in English)\n\n"
        "CRITICAL: When including a URL, you must ensure it works. NEVER include URLs "
        "that do not cover the cited information, or URLs that lead to a 404."
    )
    response = client.responses.create(
        model=QWEN_MODEL,
        input=f"Research this topic for a business presentation:\n\n{prompt}",
        instructions=system,
        tools=[{"type": "web_search"}],
        extra_body={"enable_thinking": True},
    )
    research_md = response.output_text or ""

    # ── Image search for title / section-divider backgrounds ───────────────
    img_system = (
        "You are a visual researcher. Find real, publicly accessible image URLs "
        "suitable as full-slide backgrounds for a presentation. "
        "For every image you find, output its direct URL on its own line prefixed "
        "with exactly 'IMG_URL: ' (nothing else on that line)."
    )
    img_user = (
        f"Find 10 background images for a business presentation about: '{prompt}'.\n"
        f"Requirements:\n"
        f"- Images must be DIRECTLY relevant to the topic — no generic offices, consultants, arrows ...\n"
        f"- Prefer dramatic, high-contrast visuals that look good darkened to 60% brightness\n"
        f"- Identify the most iconic visual element of the topic (e.g., a company name or product) and search for that specifically\n"
        f"  Example: 'JD.com' → 'China logistics warehouse', 'e-commerce delivery fleet'\n"
        f"  Example: 'electric vehicles' → 'EV charging station', 'electric car factory'\n"
        f"Output format — one URL per line:\n"
        f"IMG_URL: https://...\n"
    )
    img_text = ""
    try:
        # Primary path: Qwen web_search_image (responses API)
        img_resp = client.responses.create(
            model=QWEN_MODEL,
            input=img_user,
            instructions=img_system,
            tools=[{"type": "web_search_image"}],
        )
        img_text = getattr(img_resp, "output_text", "") or ""
    except Exception:  # noqa: BLE001
        pass

    if img_text:
        research_md += "\n\n## Background Image URLs\n\n" + img_text
        # Filter dead image URLs now so the structure agent never sees them
        research_md = _filter_image_urls(research_md, job_id)

    return research_md

# ─── STRUCTURE AGENT ─────────────────────────────────────────────────────────

_STRUCTURE_SCHEMA = textwrap.dedent("""\
Return ONLY a valid JSON object matching this schema (no markdown fences):
{
  "presentation_title": "string",
  "slides": [
    {
      "slide_title": "string",
      "section_label": "string",
      "layout": "two_columns | three_columns | full_width | title_slide | section_divider",
      "image_url": "string or null",
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
            {"icon": "acorn.png | address-book.png | air-traffic-control.png | airplane-landing.png | airplane-takeoff.png | airplane-tilt.png | airplay.png | alarm.png | alien.png | align-bottom.png | align-center-horizontal.png | align-center-vertical.png | align-left.png | align-right.png | align-top.png | amazon-logo.png | ambulance.png | anchor-simple.png | android-logo.png | angle.png | aperture.png | app-store-logo.png | apple-logo.png | approximate-equals.png | archive.png | armchair.png | arrow-arc-left.png | arrow-arc-right.png | arrow-bend-down-left.png | arrow-bend-down-right.png | arrow-bend-left-down.png | arrow-bend-left-up.png | arrow-bend-right-down.png | arrow-bend-right-up.png | arrow-bend-up-left.png | arrow-bend-up-right.png | arrow-clockwise.png | arrow-counter-clockwise.png | arrow-down-left.png | arrow-down-right.png | arrow-down.png | arrow-fat-down.png | arrow-fat-left.png | arrow-fat-right.png | arrow-fat-up.png | arrow-left.png | arrow-up-left.png | arrow-up-right.png | arrow-up.png | arrows-clockwise.png | arrows-down-up.png | arrows-horizontal.png | arrows-in-line-horizontal.png | arrows-in-line-vertical.png | arrows-in.png | arrows-left-right.png | arrows-merge.png | arrows-out-cardinal.png | arrows-out-line-horizontal.png | arrows-out-line-vertical.png | arrows-out.png | arrows-split.png | arrows-vertical.png | asterisk.png | at.png | atom.png | axe.png | baby-carriage.png | baby.png | backspace.png | bag.png | balloon.png | bandaids.png | bank.png | barbell.png | barcode.png | barn.png | barricade.png | baseball-helmet.png | basket.png | basketball.png | bathtub.png | battery-charging.png | battery-high.png | battery-warning.png | beanie.png | bed.png | beer-bottle.png | beer-stein.png | bell-ringing.png | bell-slash.png | bell.png | belt.png | bezier-curve.png | bicycle.png | binary.png | binoculars.png | biohazard.png | bird.png | blueprint.png | bluetooth-slash.png | bluetooth.png | boat.png | bomb.png | bone.png | book-bookmark.png | book.png | books.png | bounding-box.png | bowl-steam.png | bowling-ball.png | box-arrow-down.png | box-arrow-up.png | boxing-glove.png | brackets-angle.png | brackets-curly.png | brackets-round.png | brackets-square.png | brain.png | brandy.png | bread.png | bridge.png | broadcast.png | broom.png | bug.png | buildings.png | bulldozer.png | butterfly.png | cable-car.png | cactus.png | cake.png | calculator.png | calendar-blank.png | calendar-check.png | calendar-minus.png | calendar-plus.png | calendar-slash.png | call-bell.png | camera-plus.png | camera-rotate.png | camera-slash.png | camera.png | car-profile.png | carrot.png | cash-register.png | cassette-tape.png | castle-turret.png | cat.png | cell-signal-full.png | cell-signal-slash.png | cell-tower.png | certificate.png | chalkboard-simple.png | champagne.png | charging-station.png | chart-bar.png | chart-line-down.png | chart-line-up.png | chart-line.png | chart-pie.png | chart-scatter.png | chat-centered-dots.png | chat-centered-slash.png | chat-text.png | chats.png | cheers.png | cheese.png | chef-hat.png | cherries.png | church.png | cigarette-slash.png | cigarette.png | circle.png | circuitry.png | city.png | clipboard-text.png | clock-clockwise.png | clock-counter-clockwise.png | clock.png | cloud-arrow-down.png | cloud-arrow-up.png | cloud-check.png | cloud-lightning.png | cloud-moon.png | cloud-rain.png | cloud-slash.png | cloud-snow.png | cloud-sun.png | cloud.png | clover.png | club.png | coat-hanger.png | code-simple.png | code.png | coffee.png | coins.png | columns-plus-left.png | columns-plus-right.png | columns.png | compass-rose.png | compass-tool.png | compass.png | confetti.png | contactless-payment.png | cookie.png | cooking-pot.png | copy.png | copyright.png | corners-in.png | corners-out.png | couch.png | cow.png | cowboy-hat.png | cpu.png | crane-tower.png | cricket.png | crop.png | cross.png | crosshair-simple.png | crown-simple.png | cube-focus.png | cube.png | currency-btc.png | currency-cny.png | currency-dollar-simple.png | currency-dollar.png | currency-eth.png | currency-eur.png | currency-gbp.png | currency-inr.png | currency-jpy.png | currency-krw.png | currency-kzt.png | currency-ngn.png | currency-rub.png | cursor-text.png | cursor.png | database.png | desktop-tower.png | detective.png | device-mobile-slash.png | device-mobile.png | device-rotate.png | diamond.png | diamonds-four.png | dice-six.png | disco-ball.png | discord-logo.png | divide.png | dna.png | dog.png | door.png | download-simple.png | dress.png | dresser.png | drone.png | drop-simple.png | drop-slash.png | dropbox-logo.png | egg-crack.png | eject.png | envelope-simple-open.png | envelope-simple.png | equalizer.png | equals.png | eraser.png | escalator-down.png | escalator-up.png | exclamation-mark.png | export.png | eye-slash.png | eye.png | eyedropper.png | face-mask.png | facebook-logo.png | factory.png | faders.png | fan.png | fast-forward.png | feather.png | figma-logo.png | file-archive.png | file-arrow-down.png | file-arrow-up.png | file-cloud.png | file-code.png | file-pdf.png | file-text.png | film-slate.png | fingerprint.png | fire-extinguisher.png | fire-simple.png | first-aid-kit.png | fish-simple.png | flag.png | flashlight.png | flask.png | flower-tulip.png | flying-saucer.png | folder-simple.png | football.png | fork-knife.png | function.png | funnel.png | game-controller.png | gas-pump.png | gavel.png | gear-six.png | gender-female.png | gender-intersex.png | gender-male.png | gender-neuter.png | gender-nonbinary.png | gender-transgender.png | ghost.png | gift.png | git-branch.png | git-commit.png | git-diff.png | git-fork.png | git-merge.png | git-pull-request.png | github-logo.png | gitlab-logo-simple.png | globe-hemisphere-east.png | globe-hemisphere-west.png | globe.png | google-chrome-logo.png | google-drive-logo.png | google-logo.png | google-photos-logo.png | google-play-logo.png | graduation-cap.png | graph.png | graphics-card.png | greater-than-or-equal.png | greater-than.png | guitar.png | hair-dryer.png | hamburger.png | hammer.png | hand-eye.png | hand-fist.png | hand-heart.png | hand-peace.png | hand-pointing.png | hand-soap.png | hand-waving.png | hand.png | handbag-simple.png | hands-clapping.png | hands-praying.png | handshake.png | hard-hat.png | hash.png | head-circuit.png | headlights.png | headphones.png | heart-break.png | heart.png | heartbeat.png | hexagon.png | high-heel.png | highlighter.png | hockey.png | horse.png | hospital.png | hourglass-medium.png | house.png | ice-cream.png | identification-card.png | image.png | infinity.png | info.png | instagram-logo.png | intersect-three.png | island.png | jar-label.png | joystick.png | kanban.png | key.png | keyhole.png | knife.png | ladder-simple.png | lamp.png | layout.png | leaf.png | less-than-or-equal.png | less-than.png | lightbulb.png | lightning-slash.png | lightning.png | line-segments.png | link-break.png | link.png | linkedin-logo.png | linux-logo.png | list-bullets.png | list-checks.png | lock-simple-open.png | lock-simple.png | magnet-straight.png | magnifying-glass.png | mailbox.png | map-pin.png | martini.png | mask-happy.png | mask-sad.png | math-operations.png | medal-military.png | megaphone-simple.png | memory.png | messenger-logo.png | meta-logo.png | metronome.png | microphone-slash.png | microphone.png | microscope.png | minus.png | money.png | moon.png | moped.png | mosque.png | mountains.png | mouse.png | music-notes-simple.png | navigation-arrow.png | newspaper.png | not-equals.png | note-blank.png | nuclear-plant.png | number-eight.png | number-five.png | number-four.png | number-nine.png | number-one.png | number-seven.png | number-six.png | number-three.png | number-two.png | number-zero.png | numpad.png | octagon.png | office-chair.png | open-ai-logo.png | oven.png | package.png | palette.png | paper-plane-right.png | paperclip.png | parallelogram.png | park.png | pause.png | paw-print.png | paypal-logo.png | peace.png | pen-nib.png | pencil-simple-slash.png | pencil-simple.png | pentagon.png | pentagram.png | pepper.png | percent.png | person-simple-bike.png | person-simple-hike.png | person-simple-run.png | person-simple-ski.png | person-simple-snowboard.png | person-simple-swim.png | person-simple-tai-chi.png | person-simple-throw.png | person.png | phone-call.png | phone-disconnect.png | phone-incoming.png | phone-outgoing.png | phone-slash.png | phone.png | pi.png | piano-keys.png | picnic-table.png | picture-in-picture.png | piggy-bank.png | pill.png | ping-pong.png | pint-glass.png | pipe-wrench.png | pipe.png | pizza.png | placeholder.png | planet.png | plant.png | play.png | playlist.png | plug.png | plugs.png | plus-minus.png | plus.png | poker-chip.png | polygon.png | popcorn.png | popsicle.png | potted-plant.png | power.png | prescription.png | presentation-chart.png | printer.png | prohibit.png | pulse.png | push-pin-slash.png | push-pin.png | puzzle-piece.png | qr-code.png | question-mark.png | quotes.png | rabbit.png | racquet.png | radical.png | radio.png | radioactive.png | rainbow-cloud.png | ranking.png | receipt.png | record.png | rectangle-dashed.png | rectangle.png | recycle.png | reddit-logo.png | repeat-once.png | repeat.png | resize.png | rewind.png | road-horizon.png | robot.png | rocket-launch.png | rows-plus-bottom.png | rows-plus-top.png | rows.png | rss.png | ruler.png | sailboat.png | scales.png | scan-smiley.png | scissors.png | scooter.png | screencast.png | screwdriver.png | seal-check.png | seat.png | security-camera.png | share-network.png | shield-check.png | shield-plus.png | shield-slash.png | shield-star.png | shield-warning.png | shield.png | shirt-folded.png | shopping-cart-simple.png | shovel.png | shower.png | shrimp.png | shuffle-simple.png | sigma.png | sign-in.png | sign-out.png | signature.png | siren.png | skip-back.png | skip-forward.png | skull.png | skype-logo.png | slack-logo.png | sliders.png | smiley-angry.png | smiley-meh.png | smiley-melting.png | smiley-nervous.png | smiley-sad.png | smiley-wink.png | smiley-x-eyes.png | smiley.png | snapchat-logo.png | sneaker.png | snowflake.png | soccer-ball.png | sock.png | solar-panel.png | soundcloud-logo.png | spade.png | sparkle.png | speaker-hifi.png | speaker-simple-high.png | speaker-simple-none.png | speaker-simple-slash.png | speaker-simple-x.png | speedometer.png | spinner-gap.png | spiral.png | spotify-logo.png | spray-bottle.png | square-logo.png | square.png | stack-overflow-logo.png | stack.png | star-and-crescent.png | star-of-david.png | star.png | steam-logo.png | steps.png | stethoscope.png | stool.png | storefront.png | strategy.png | stripe-logo.png | student.png | suitcase-rolling.png | sun.png | sunglasses.png | swimming-pool.png | sword.png | synagogue.png | syringe.png | t-shirt.png | table.png | tag.png | taxi.png | telegram-logo.png | television-simple.png | tennis-ball.png | tent.png | terminal.png | test-tube.png | textbox.png | thermometer-simple.png | threads-logo.png | thumbs-down.png | thumbs-up.png | ticket.png | tiktok-logo.png | tilde.png | tip-jar.png | tipi.png | toggle-right.png | toilet-paper.png | toilet.png | tooth.png | tractor.png | trademark-registered.png | trademark.png | traffic-cone.png | traffic-signal.png | train-simple.png | translate.png | trash.png | tree-evergreen.png | tree-palm.png | tree-structure.png | tree-view.png | tree.png | trend-down.png | trend-up.png | triangle-dashed.png | triangle.png | trophy.png | truck.png | tumblr-logo.png | twitch-logo.png | twitter-logo.png | umbrella-simple.png | upload-simple.png | upload.png | usb.png | user-check.png | user-focus.png | user-list.png | user-minus.png | user-plus.png | user.png | users-three.png | vault.png | vector-three.png | vector-two.png | vibrate.png | video-camera-slash.png | video-camera.png | video.png | vignette.png | vinyl-record.png | virus.png | voicemail.png | volleyball.png | wall.png | wallet.png | warning.png | washing-machine.png | watch.png | wave-sawtooth.png | wave-sine.png | wave-square.png | waves.png | webcam-slash.png | webcam.png | wechat-logo.png | whatsapp-logo.png | wheelchair.png | wifi-high.png | wifi-slash.png | wind.png | windmill.png | windows-logo.png | wine.png | wrench.png | x-logo.png | x.png | yarn.png | yin-yang.png | youtube-logo.png",
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
      "sources": "string or null",
      "notes": "string or null"
    }
  ]
}
Rules:
- two_columns: exactly 2 columns, width_ratios (plus padding) must sum to 1.0
- three_columns: exactly 3 columns, each width_ratio (plus padding) = 0.33
- full_width: exactly 1 column, width_ratio (no padding here) = 1.0
- title_slide: the title of this slide is the presentation title. No other content is needed.
- section_divider: for the title of the slide, write an interesting question that covers the content of that section; for the section_label, give the name of the section. No other content is needed.
- image_url: look for lines in the research document's "Background Image URLs" section that start with "IMG_URL: ". Use those URLs for title slides and section dividers. You MUST assign an image_url to EVERY title_slide and EVERY section_divider. Reuse URLs across slides; a reused image is infinitely better than null. Set image_url to null ONLY when the "Background Image URLs" section is entirely absent from the research document.
- CHART PRIORITY (critical): Use charts as your DEFAULT content type whenever numerical data exists in the research. At least 40-50% of content slides should contain a chart. Only use bullet_list or text when the data genuinely cannot be charted or you refer to an actual list. Prefer line for trends, pie for composition, bar for rankings, grouped_bar for comparisons.
- For charts: always include ACTUAL data matching the research findings. Every chart must have at least 3 data points (x_labels) to be meaningful.
- BULLET LIST PRIORITY: Use content_type 'bullet_list' for any column where content consists of distinct items, actions, or attributes (3+ points). Only use 'text' for continuous multi-paragraph prose that genuinely cannot be structured as bullets. When in doubt, always choose 'bullet_list' over 'text'.
- CONCLUSION BOXES: Add conclusion_box to content slides as much as possible. Each conclusion box should capture the causal "so what?". Frame it as a decisive, forward-looking statement (1-2 sentences). This creates a causal thread connecting slides throughout the presentation.
- For sources, write a short readable label (author, org, or report name) in the sources field — NEVER use citation brackets like [1][4][6] in sources or notes. For notes, find the matching entry in the research document's references section (lines starting with [n]) and copy the full URL verbatim (starting with https://). If multiple citations apply, list the URLs separated by " | ". If you cannot find an explicit URL, write the source name only — never write bare [n] markers.
- ICONS (exhaustive list): The icon list above is COMPLETE AND EXHAUSTIVE. Every filename ends in .png — NEVER use any other extension. You MUST pick from this exact list. NEVER invent names ("chart-line-up.png" is valid; "growth.png" and "chart-line-up.svg" are NOT). If unsure, you MUST choose the closest match in the list. ALWAYS pick a name from the list, NEVER invent. For example, brain.png for AI/ML topics, globe.png for international/global topics, coins.png or calculator.png for financial topics.
- BULLET ICON REQUIRED: The "icon" field is MANDATORY for every bullet item. An empty or missing icon is invalid. You MUST always select one .png filename from the exhaustive list above. NEVER put Unicode arrow or symbol characters (↑ ↓ → ← ✓ ✗ ● ◆ • etc.) in the title or description fields as visual markers — those fields are for plain text only. Plain currency signs (¥ $ €) and standard punctuation are fine.
- Use 12–15 slides total; group related slides under the same section_label
""")


# ─── CITATION HELPERS ───────────────────────────────────────────────────────
def _extract_citation_map(research_md: str) -> dict[str, str]:
    """Parse numbered citations from research output → {num_str: url}.
    Handles [1] URL | Title, [1]: URL, [^1]: URL, [1] Title | URL, 1. URL, etc.
    Also handles the Qwen web-search citation format: [1] URL  and  [^1]: URL."""
    result: dict[str, str] = {}
    # More permissive URL pattern — stops at whitespace, |, or trailing punctuation but
    # allows query params, fragments, and common path characters.
    url_pat = re.compile(r'https?://[^\s\)\]\}>"\|]+')
    for line in research_md.split("\n"):
        stripped = line.strip().lstrip('- *>#')
        # Match [n], [^n], [n]:, [n]., [n]), n., n:, n) at line start
        m = re.match(r'^\[?\^?(\d+)\]?[:.)]?\s+', stripped)
        if m:
            urls = url_pat.findall(stripped)
            if urls:
                result[m.group(1)] = urls[0].rstrip('.,;)>]"\' ')
    return result


def _resolve_citations(structure: dict, citation_map: dict[str, str]) -> None:
    """Replace [n] citation markers in slide notes/sources with actual URLs (in-place).
    Handles: [1], [1][2][3], [1,2,3], [1-3] patterns."""
    if not citation_map:
        return

    def _resolve(text: str) -> str:
        # First handle comma/space-separated groups: [1,2,3] or [1, 2, 3]
        text = re.sub(
            r'\[(\d+(?:\s*,\s*\d+)*)\]',
            lambda m: ' | '.join(
                citation_map.get(n.strip(), f'[{n.strip()}]')
                for n in m.group(1).split(',')
            ),
            text,
        )
        # Then handle adjacent single brackets: [1][2][3]
        text = re.sub(
            r'\[(\d+)\]',
            lambda m: citation_map.get(m.group(1), m.group(0)),
            text,
        )
        # Collapse runs of " | " that may result from adjacent resolved markers
        text = re.sub(r'(\s*\|\s*)+', ' | ', text)
        return text

    for slide in structure.get("slides", []):
        if slide.get("notes"):
            slide["notes"] = _resolve(slide["notes"])
        if slide.get("sources"):
            slide["sources"] = _resolve(slide["sources"])


# ─── ICON VALIDATION ──────────────────────────────────────────────────────────
# Extract the valid icon list from _STRUCTURE_SCHEMA at import time
_VALID_ICONS: set[str] = set()
_icon_match = re.search(
    r'"icon": "([^"]+(?:\\.png(?:\s*\|\s*[^"]+\\.png)*))"',
    _STRUCTURE_SCHEMA,
)
if _icon_match:
    _VALID_ICONS = {name.strip() for name in _icon_match.group(1).split("|")}


def _closest_icon(name: str, cutoff: float = 0.6) -> str | None:
    """Return the closest valid icon name via fuzzy matching, or None if no good match."""
    if not name.endswith(".png"):
        name = name.rsplit(".", 1)[0] + ".png" if "." in name else name + ".png"
    match = difflib.get_close_matches(name, _VALID_ICONS, n=1, cutoff=cutoff)
    return match[0] if match else None


def _validate_and_fix_icons(structure: dict, job_id: str) -> int:
    """Walk the structure and fix any invalid icon names. Returns count of fixes."""
    fixes = 0
    for slide in structure.get("slides", []):
        for col in slide.get("columns", []):
            for bullet in col.get("bullets") or []:
                icon = bullet.get("icon", "")
                if not icon:
                    continue
                # Step 1: if _VALID_ICONS is populated and icon is valid, skip
                if _VALID_ICONS and icon in _VALID_ICONS:
                    continue

                # Step 2: try fuzzy matching against the known valid set
                fallback = _smart_fallback_icon(icon, bullet)
                if fallback:
                    print(f"[{job_id}] Icon fix (fallback): '{icon}' → '{fallback}'")
                    bullet["icon"] = fallback
                    fixes += 1
                else:
                    fixed = _closest_icon(icon) if _VALID_ICONS else None
                    print(f"[{job_id}] Icon fix (fuzzy): '{icon}' → '{fixed}'")
                    bullet["icon"] = fixed
                    fixes += 1                    
                    
    if fixes:
        print(f"[{job_id}] Fixed {fixes} invalid icon(s)")
    return fixes   

def _smart_fallback_icon(name: str, bullet: dict) -> str:
    """Choose a safe fallback icon when fuzzy matching fails entirely."""
    text = (bullet.get("title", "") + " " + bullet.get("description", "") + " " + name).lower()
    keyword_map = {
        "money": "currency-dollar.png", "finance": "currency-dollar.png",
        "revenue": "currency-dollar.png", "cost": "currency-dollar.png",
        "folder": "folder-simple.png", "profit": "chart-line-up.png", "growth": "chart-line-up.png", "trend": "chart-line-up.png", "increase": "chart-line-up.png",
        "decrease": "chart-line-down.png", "decline": "chart-line-down.png",
        "market": "chart-bar.png", "share": "chart-pie.png",
        "percent": "chart-pie.png", "percentage": "chart-pie.png", "lightbulb-on.png": "lightbulb.png", "megaphone.png": "megaphone-simple.png",
        "data": "chart-bar.png", "statistic": "chart-bar.png",
        "ai": "brain.png", "ml": "brain.png", "intelligence": "brain.png",
        "tech": "cpu.png", "technology": "cpu.png", "digital": "cpu.png",
        "drug": "pill.png", "pharma": "pill.png", "medicine": "pill.png",
        "health": "heartbeat.png", "medical": "heartbeat.png",
        "hospital": "hospital.png", "clinic": "hospital.png",
        "patient": "person.png", "consumer": "person.png",
        "customer": "person.png", "people": "users-three.png",
        "supply": "package.png", "chain": "link.png",
        "logistic": "truck.png", "delivery": "truck.png",
        "efficiency": "gear-six.png", "operation": "gear-six.png",
        "strategy": "strategy.png", "plan": "strategy.png",
        "regulation": "gavel.png", "compliance": "gavel.png",
        "global": "globe.png", "world": "globe.png",
        "europe": "globe-hemisphere-east.png", "belgium": "globe-hemisphere-east.png",
        "risk": "shield-warning.png", "warning": "shield-warning.png",
        "check": "shield-check.png", "quality": "shield-check.png",
        "innovation": "lightbulb.png", "idea": "lightbulb.png",
        "research": "microscope.png", "science": "flask.png",
        "environment": "tree.png", "green": "leaf.png",
        "time": "clock.png", "schedule": "calendar-blank.png",
        "team": "users-three.png", "collaboration": "users-three.png",
        "communication": "chat-text.png", "message": "chat-text.png",
        "shopping": "shopping-cart-simple.png", "retail": "storefront.png",
        "store": "storefront.png", "shop": "storefront.png",
        "sale": "tag.png", "price": "tag.png",
        "target": "crosshair-simple.png", "goal": "target.png",
        "star": "star.png", "favorite": "star.png",
        "lock": "lock-simple.png", "security": "lock-simple.png",
        "network": "share-network.png", "connect": "share-network.png",
        "building": "buildings.png", "industry": "factory.png",
        "scale": "scales.png", "weigh": "scales.png", "balance": "scales.png",
        "circle": "shield-check.png", "check-circle": "shield-check.png",
        "certified": "certificate.png", "certification": "certificate.png",
        "train": "graduation-cap.png", "education": "graduation-cap.png",
        "learn": "graduation-cap.png", "skill": "graduation-cap.png",
    }
    for keyword, icon in keyword_map.items():
        if keyword in text:
            return icon
    return None


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
        max_tokens=16000,
    )
    raw = response.choices[0].message.content or "{}"
    structure = json.loads(raw)
    # Post-process: validate and fix any hallucinated icon names
    _validate_and_fix_icons(structure, job_id)
    # Post-process: resolve any [n] citation markers in notes/sources to actual URLs
    citation_map = _extract_citation_map(research_md)
    _resolve_citations(structure, citation_map)
    # Embed citation map so build_slides can do a final resolution pass on references
    structure["_citation_map"] = citation_map
    return structure


# ─── MAIN HANDLER ─────────────────────────────────────────────────────────────

def handler(event: dict, context) -> None:  # noqa: ANN001
    """Entry point – invoked asynchronously; no HTTP response required."""
    job_id: str = event["job_id"]
    prompt: str = event["prompt"]
    settings: dict = event.get("settings", {})
    api_key: str = settings.get("api_key", "")
    file_ids: list[str] = event.get("file_ids") or []

    print(f"[{job_id}] Handler started. Prompt: {prompt[:100]}...")

    client = OpenAI(api_key=api_key, base_url=QWEN_BASE_URL, timeout=600.0)

    try:
        # Build prompt with fileid:// references (file_ids are Dashscope IDs)
        doc_refs = "".join(f"fileid://{fid}\n" for fid in file_ids)
        augmented_prompt = f"{doc_refs}{prompt}" if file_ids else prompt

        # ── Stage 1: Research ───────────────────────────────────────────────
        print(f"[{job_id}] Stage 1: Starting research...")
        research_md = _research(augmented_prompt, client, job_id)
        print(f"[{job_id}] Stage 1: Research complete ({len(research_md)} chars)")
        _update_job(job_id, research_md=research_md)

        # ── Stage 2: Structure ──────────────────────────────────────────────
        print(f"[{job_id}] Stage 2: Starting structure...")
        structure = _structure(prompt, research_md, client, job_id)
        print(f"[{job_id}] Stage 2: Structure complete ({len(structure.get('slides', []))} slides)")
        _update_job(job_id, structure_md=json.dumps(structure, indent=2))

        # ── Stage 3: Delegate to build_slides Lambda ────────────────────────
        print(f"[{job_id}] Stage 3: Invoking build_slides Lambda...")
        _update_job(job_id, status="building", stage_message="Building your presentation\u2026")
        boto3.client("lambda").invoke(
            FunctionName=BUILD_SLIDES_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps({
                "job_id": job_id,
                "structure": structure,
                "settings": settings,
            }).encode("utf-8"),
        )
        print(f"[{job_id}] Stage 3: build_slides invoked successfully")

    except Exception as exc:  # noqa: BLE001
        print(f"[{job_id}] ERROR: {exc}")
        _update_job(
            job_id,
            status="error",
            stage_message="Something went wrong.",
            error_message=str(exc),
        )
        raise
