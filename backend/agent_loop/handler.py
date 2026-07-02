"""
Agent-loop Lambda: orchestrates Research → Structure → Build for a PowerPoint presentation.
Invoked asynchronously by start_job. Updates a Supabase `jobs` row at each stage.
"""
from __future__ import annotations

import json
import os
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


# ─── RESEARCH AGENT ──────────────────────────────────────────────────────────

def _research(prompt: str, client: OpenAI, job_id: str) -> str:
    """Stage 1 – Use Qwen with web search to gather current facts and data."""
    _update_job(job_id, status="researching", stage_message="Researching your topic\u2026")

    system = (
        "You are a research analyst preparing materials for a business presentation. "
        "Use web search to gather current facts, statistics, trends and examples. "
        "Produce a thorough markdown document covering: key facts and useful tables with data that can be used in charts, "
        "notable examples or case studies, and key takeaways. "
        "Include specific numbers and dates where available."
    )
    response = client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Research this topic for a business presentation:\n\n{prompt}"},
        ],
        extra_body={"enable_search": True,
                    "search_options":{"forced_search": True, "enable_source": True, "enable_citation": True}},
    )
    research_md = response.choices[0].message.content or ""

    # ── Image search for title / section-divider backgrounds ───────────────
    image_prompt = (
        f"Find 4-5 high-quality, professional background images suitable for a "
        f"business presentation about: {prompt}. The images should be simple "
        f", modern, and work well as full-slide backgrounds for a title "
        f"slide and section dividers. Search for broad visual concepts related to "
        f"the topic rather than specific data or charts. For each image found, "
        f"include its URL. Examples could be 'data center' for a presentation covering a paper on data center spatial distribution or 'smartphone factory' for a presentation on the company Transsion"
    )
    try:
        image_response = client.responses.create(
            model=QWEN_MODEL,
            input=image_prompt,
            tools=[{"type": "web_search_image"}],
        )
        image_text = image_response.output_text
        if image_text:
            research_md += "\n\n## Image Search Results (for title & divider backgrounds)\n\n" + image_text
    except Exception:  # noqa: BLE001 – non-critical; proceed without images
        pass

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
            {"icon": "acorn.svg | address-book.svg | air-traffic-control.svg | airplane-landing.svg | airplane-takeoff.svg | airplane-tilt.svg | airplay.svg | alarm.svg | alien.svg | align-bottom.svg | align-center-horizontal.svg | align-center-vertical.svg | align-left.svg | align-right.svg | align-top.svg | amazon-logo.svg | ambulance.svg | anchor-simple.svg | android-logo.svg | angle.svg | aperture.svg | app-store-logo.svg | apple-logo.svg | approximate-equals.svg | archive.svg | armchair.svg | arrow-arc-left.svg | arrow-arc-right.svg | arrow-bend-down-left.svg | arrow-bend-down-right.svg | arrow-bend-left-down.svg | arrow-bend-left-up.svg | arrow-bend-right-down.svg | arrow-bend-right-up.svg | arrow-bend-up-left.svg | arrow-bend-up-right.svg | arrow-clockwise.svg | arrow-counter-clockwise.svg | arrow-down-left.svg | arrow-down-right.svg | arrow-down.svg | arrow-fat-down.svg | arrow-fat-left.svg | arrow-fat-right.svg | arrow-fat-up.svg | arrow-left.svg | arrow-up-left.svg | arrow-up-right.svg | arrow-up.svg | arrows-clockwise.svg | arrows-down-up.svg | arrows-horizontal.svg | arrows-in-line-horizontal.svg | arrows-in-line-vertical.svg | arrows-in.svg | arrows-left-right.svg | arrows-merge.svg | arrows-out-cardinal.svg | arrows-out-line-horizontal.svg | arrows-out-line-vertical.svg | arrows-out.svg | arrows-split.svg | arrows-vertical.svg | asterisk.svg | at.svg | atom.svg | axe.svg | baby-carriage.svg | baby.svg | backspace.svg | bag.svg | balloon.svg | bandaids.svg | bank.svg | barbell.svg | barcode.svg | barn.svg | barricade.svg | baseball-helmet.svg | basket.svg | basketball.svg | bathtub.svg | battery-charging.svg | battery-high.svg | battery-warning.svg | beanie.svg | bed.svg | beer-bottle.svg | beer-stein.svg | bell-ringing.svg | bell-slash.svg | bell.svg | belt.svg | bezier-curve.svg | bicycle.svg | binary.svg | binoculars.svg | biohazard.svg | bird.svg | blueprint.svg | bluetooth-slash.svg | bluetooth.svg | boat.svg | bomb.svg | bone.svg | book-bookmark.svg | book.svg | books.svg | bounding-box.svg | bowl-steam.svg | bowling-ball.svg | box-arrow-down.svg | box-arrow-up.svg | boxing-glove.svg | brackets-angle.svg | brackets-curly.svg | brackets-round.svg | brackets-square.svg | brain.svg | brandy.svg | bread.svg | bridge.svg | broadcast.svg | broom.svg | bug.svg | buildings.svg | bulldozer.svg | butterfly.svg | cable-car.svg | cactus.svg | cake.svg | calculator.svg | calendar-blank.svg | calendar-check.svg | calendar-minus.svg | calendar-plus.svg | calendar-slash.svg | call-bell.svg | camera-plus.svg | camera-rotate.svg | camera-slash.svg | camera.svg | car-profile.svg | carrot.svg | cash-register.svg | cassette-tape.svg | castle-turret.svg | cat.svg | cell-signal-full.svg | cell-signal-slash.svg | cell-tower.svg | certificate.svg | chalkboard-simple.svg | champagne.svg | charging-station.svg | chart-bar.svg | chart-line-down.svg | chart-line-up.svg | chart-line.svg | chart-pie.svg | chart-scatter.svg | chat-centered-dots.svg | chat-centered-slash.svg | chat-text.svg | chats.svg | cheers.svg | cheese.svg | chef-hat.svg | cherries.svg | church.svg | cigarette-slash.svg | cigarette.svg | circle.svg | circuitry.svg | city.svg | clipboard-text.svg | clock-clockwise.svg | clock-counter-clockwise.svg | clock.svg | cloud-arrow-down.svg | cloud-arrow-up.svg | cloud-check.svg | cloud-lightning.svg | cloud-moon.svg | cloud-rain.svg | cloud-slash.svg | cloud-snow.svg | cloud-sun.svg | cloud.svg | clover.svg | club.svg | coat-hanger.svg | code-simple.svg | code.svg | coffee.svg | coins.svg | columns-plus-left.svg | columns-plus-right.svg | columns.svg | compass-rose.svg | compass-tool.svg | compass.svg | confetti.svg | contactless-payment.svg | cookie.svg | cooking-pot.svg | copy.svg | copyright.svg | corners-in.svg | corners-out.svg | couch.svg | cow.svg | cowboy-hat.svg | cpu.svg | crane-tower.svg | cricket.svg | crop.svg | cross.svg | crosshair-simple.svg | crown-simple.svg | cube-focus.svg | cube.svg | currency-btc.svg | currency-cny.svg | currency-dollar-simple.svg | currency-dollar.svg | currency-eth.svg | currency-eur.svg | currency-gbp.svg | currency-inr.svg | currency-jpy.svg | currency-krw.svg | currency-kzt.svg | currency-ngn.svg | currency-rub.svg | cursor-text.svg | cursor.svg | database.svg | desktop-tower.svg | detective.svg | device-mobile-slash.svg | device-mobile.svg | device-rotate.svg | diamond.svg | diamonds-four.svg | dice-six.svg | disco-ball.svg | discord-logo.svg | divide.svg | dna.svg | dog.svg | door.svg | download-simple.svg | dress.svg | dresser.svg | drone.svg | drop-simple.svg | drop-slash.svg | dropbox-logo.svg | egg-crack.svg | eject.svg | envelope-simple-open.svg | envelope-simple.svg | equalizer.svg | equals.svg | eraser.svg | escalator-down.svg | escalator-up.svg | exclamation-mark.svg | export.svg | eye-slash.svg | eye.svg | eyedropper.svg | face-mask.svg | facebook-logo.svg | factory.svg | faders.svg | fan.svg | fast-forward.svg | feather.svg | figma-logo.svg | file-archive.svg | file-arrow-down.svg | file-arrow-up.svg | file-cloud.svg | file-code.svg | file-pdf.svg | file-text.svg | film-slate.svg | fingerprint.svg | fire-extinguisher.svg | fire-simple.svg | first-aid-kit.svg | fish-simple.svg | flag.svg | flashlight.svg | flask.svg | flower-tulip.svg | flying-saucer.svg | folder-simple.svg | football.svg | fork-knife.svg | function.svg | funnel.svg | game-controller.svg | gas-pump.svg | gavel.svg | gear-six.svg | gender-female.svg | gender-intersex.svg | gender-male.svg | gender-neuter.svg | gender-nonbinary.svg | gender-transgender.svg | ghost.svg | gift.svg | git-branch.svg | git-commit.svg | git-diff.svg | git-fork.svg | git-merge.svg | git-pull-request.svg | github-logo.svg | gitlab-logo-simple.svg | globe-hemisphere-east.svg | globe-hemisphere-west.svg | globe.svg | google-chrome-logo.svg | google-drive-logo.svg | google-logo.svg | google-photos-logo.svg | google-play-logo.svg | graduation-cap.svg | graph.svg | graphics-card.svg | greater-than-or-equal.svg | greater-than.svg | guitar.svg | hair-dryer.svg | hamburger.svg | hammer.svg | hand-eye.svg | hand-fist.svg | hand-heart.svg | hand-peace.svg | hand-pointing.svg | hand-soap.svg | hand-waving.svg | hand.svg | handbag-simple.svg | hands-clapping.svg | hands-praying.svg | handshake.svg | hard-hat.svg | hash.svg | head-circuit.svg | headlights.svg | headphones.svg | heart-break.svg | heart.svg | heartbeat.svg | hexagon.svg | high-heel.svg | highlighter.svg | hockey.svg | horse.svg | hospital.svg | hourglass-medium.svg | house.svg | ice-cream.svg | identification-card.svg | image.svg | infinity.svg | info.svg | instagram-logo.svg | intersect-three.svg | island.svg | jar-label.svg | joystick.svg | kanban.svg | key.svg | keyhole.svg | knife.svg | ladder-simple.svg | lamp.svg | layout.svg | leaf.svg | less-than-or-equal.svg | less-than.svg | lightbulb.svg | lightning-slash.svg | lightning.svg | line-segments.svg | link-break.svg | link.svg | linkedin-logo.svg | linux-logo.svg | list-bullets.svg | list-checks.svg | lock-simple-open.svg | lock-simple.svg | magnet-straight.svg | magnifying-glass.svg | mailbox.svg | map-pin.svg | martini.svg | mask-happy.svg | mask-sad.svg | math-operations.svg | medal-military.svg | megaphone-simple.svg | memory.svg | messenger-logo.svg | meta-logo.svg | metronome.svg | microphone-slash.svg | microphone.svg | microscope.svg | minus.svg | money.svg | moon.svg | moped.svg | mosque.svg | mountains.svg | mouse.svg | music-notes-simple.svg | navigation-arrow.svg | newspaper.svg | not-equals.svg | note-blank.svg | nuclear-plant.svg | number-eight.svg | number-five.svg | number-four.svg | number-nine.svg | number-one.svg | number-seven.svg | number-six.svg | number-three.svg | number-two.svg | number-zero.svg | numpad.svg | octagon.svg | office-chair.svg | open-ai-logo.svg | oven.svg | package.svg | palette.svg | paper-plane-right.svg | paperclip.svg | parallelogram.svg | park.svg | pause.svg | paw-print.svg | paypal-logo.svg | peace.svg | pen-nib.svg | pencil-simple-slash.svg | pencil-simple.svg | pentagon.svg | pentagram.svg | pepper.svg | percent.svg | person-simple-bike.svg | person-simple-hike.svg | person-simple-run.svg | person-simple-ski.svg | person-simple-snowboard.svg | person-simple-swim.svg | person-simple-tai-chi.svg | person-simple-throw.svg | person.svg | phone-call.svg | phone-disconnect.svg | phone-incoming.svg | phone-outgoing.svg | phone-slash.svg | phone.svg | pi.svg | piano-keys.svg | picnic-table.svg | picture-in-picture.svg | piggy-bank.svg | pill.svg | ping-pong.svg | pint-glass.svg | pipe-wrench.svg | pipe.svg | pizza.svg | placeholder.svg | planet.svg | plant.svg | play.svg | playlist.svg | plug.svg | plugs.svg | plus-minus.svg | plus.svg | poker-chip.svg | polygon.svg | popcorn.svg | popsicle.svg | potted-plant.svg | power.svg | prescription.svg | presentation-chart.svg | printer.svg | prohibit.svg | pulse.svg | push-pin-slash.svg | push-pin.svg | puzzle-piece.svg | qr-code.svg | question-mark.svg | quotes.svg | rabbit.svg | racquet.svg | radical.svg | radio.svg | radioactive.svg | rainbow-cloud.svg | ranking.svg | receipt.svg | record.svg | rectangle-dashed.svg | rectangle.svg | recycle.svg | reddit-logo.svg | repeat-once.svg | repeat.svg | resize.svg | rewind.svg | road-horizon.svg | robot.svg | rocket-launch.svg | rows-plus-bottom.svg | rows-plus-top.svg | rows.svg | rss.svg | ruler.svg | sailboat.svg | scales.svg | scan-smiley.svg | scissors.svg | scooter.svg | screencast.svg | screwdriver.svg | seal-check.svg | seat.svg | security-camera.svg | share-network.svg | shield-check.svg | shield-plus.svg | shield-slash.svg | shield-star.svg | shield-warning.svg | shield.svg | shirt-folded.svg | shopping-cart-simple.svg | shovel.svg | shower.svg | shrimp.svg | shuffle-simple.svg | sigma.svg | sign-in.svg | sign-out.svg | signature.svg | siren.svg | skip-back.svg | skip-forward.svg | skull.svg | skype-logo.svg | slack-logo.svg | sliders.svg | smiley-angry.svg | smiley-meh.svg | smiley-melting.svg | smiley-nervous.svg | smiley-sad.svg | smiley-wink.svg | smiley-x-eyes.svg | smiley.svg | snapchat-logo.svg | sneaker.svg | snowflake.svg | soccer-ball.svg | sock.svg | solar-panel.svg | soundcloud-logo.svg | spade.svg | sparkle.svg | speaker-hifi.svg | speaker-simple-high.svg | speaker-simple-none.svg | speaker-simple-slash.svg | speaker-simple-x.svg | speedometer.svg | spinner-gap.svg | spiral.svg | spotify-logo.svg | spray-bottle.svg | square-logo.svg | square.svg | stack-overflow-logo.svg | stack.svg | star-and-crescent.svg | star-of-david.svg | star.svg | steam-logo.svg | steps.svg | stethoscope.svg | stool.svg | storefront.svg | strategy.svg | stripe-logo.svg | student.svg | suitcase-rolling.svg | sun.svg | sunglasses.svg | swimming-pool.svg | sword.svg | synagogue.svg | syringe.svg | t-shirt.svg | table.svg | tag.svg | taxi.svg | telegram-logo.svg | television-simple.svg | tennis-ball.svg | tent.svg | terminal.svg | test-tube.svg | textbox.svg | thermometer-simple.svg | threads-logo.svg | thumbs-down.svg | thumbs-up.svg | ticket.svg | tiktok-logo.svg | tilde.svg | tip-jar.svg | tipi.svg | toggle-right.svg | toilet-paper.svg | toilet.svg | tooth.svg | tractor.svg | trademark-registered.svg | trademark.svg | traffic-cone.svg | traffic-signal.svg | train-simple.svg | translate.svg | trash.svg | tree-evergreen.svg | tree-palm.svg | tree-structure.svg | tree-view.svg | tree.svg | trend-down.svg | trend-up.svg | triangle-dashed.svg | triangle.svg | trophy.svg | truck.svg | tumblr-logo.svg | twitch-logo.svg | twitter-logo.svg | umbrella-simple.svg | upload-simple.svg | upload.svg | usb.svg | user-check.svg | user-focus.svg | user-list.svg | user-minus.svg | user-plus.svg | user.svg | users-three.svg | vault.svg | vector-three.svg | vector-two.svg | vibrate.svg | video-camera-slash.svg | video-camera.svg | video.svg | vignette.svg | vinyl-record.svg | virus.svg | voicemail.svg | volleyball.svg | wall.svg | wallet.svg | warning.svg | washing-machine.svg | watch.svg | wave-sawtooth.svg | wave-sine.svg | wave-square.svg | waves.svg | webcam-slash.svg | webcam.svg | wechat-logo.svg | whatsapp-logo.svg | wheelchair.svg | wifi-high.svg | wifi-slash.svg | wind.svg | windmill.svg | windows-logo.svg | wine.svg | wrench.svg | x-logo.svg | x.svg | yarn.svg | yin-yang.svg | youtube-logo.svg",
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
- image_url: the research document should contain images that can be used as the background for the title slide and section divider slides. Use the urls available for relevant title slides and section divider slides. If no relevant image is found, set image_url to null.
- For charts: always include ACTUAL data matching the research findings
- For sources, write the author in the sources field (this will appear on the slide), write the actual url in the notes field (this will not appear on the slide but is useful for the user)
- Use 12–15 slides total; group related slides under the same section_label
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
        max_tokens=16000,
    )
    raw = response.choices[0].message.content or "{}"
    return json.loads(raw)




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
