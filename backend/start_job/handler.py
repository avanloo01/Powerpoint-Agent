"""
Lambda handler: validates auth, creates a Supabase jobs row,
then invokes agent_loop asynchronously and returns the job ID.
"""
from __future__ import annotations

import json
import os
import uuid
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import boto3

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_SETTINGS_TABLE = os.environ.get("SUPABASE_SETTINGS_TABLE", "user_settings")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")
AGENT_LOOP_FUNCTION_NAME = os.environ.get("AGENT_LOOP_FUNCTION_NAME", "")


# ─── SUPABASE HELPERS ─────────────────────────────────────────────────────────

def _supabase_request(method: str, url: str, headers: dict, body: dict | None = None) -> object:
    payload = None
    request_headers = dict(headers)
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = urlrequest.Request(url=url, method=method, data=payload, headers=request_headers)
    try:
        with urlrequest.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"Supabase request failed ({exc.code}): {detail}") from exc


def _extract_bearer_token(event: dict) -> str:
    headers = event.get("headers") or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return ""
    return auth.split(" ", 1)[1].strip()


def _get_authenticated_user(token: str) -> dict:
    if not token:
        raise ValueError("Missing bearer token")
    url = f"{SUPABASE_URL}/auth/v1/user"
    return _supabase_request(  # type: ignore[return-value]
        "GET",
        url,
        {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
    )


def _get_user_settings(user_id: str) -> dict | None:
    # Query the decryption view which handles pgp_sym_decrypt server-side.
    query = urlparse.urlencode({
        "user_id": f"eq.{user_id}",
        "select": "api_key,primary_color,accent_color,logo_url",
        "limit": "1",
    })
    url = f"{SUPABASE_URL}/rest/v1/user_settings_decrypted?{query}"
    data = _supabase_request(
        "GET",
        url,
        {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        },
    )
    if isinstance(data, list) and data:
        return data[0]  # type: ignore[return-value]
    return None


def _create_job(job_id: str, user_id: str) -> None:
    url = f"{SUPABASE_URL}/rest/v1/jobs"
    _supabase_request(
        "POST",
        url,
        {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Prefer": "return=minimal",
        },
        {"id": job_id, "user_id": user_id, "status": "pending", "stage_message": "Queuing job\u2026"},
    )


# ─── MAIN HANDLER ─────────────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:  # noqa: ANN001
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return _response(200, {})

    try:
        token = _extract_bearer_token(event)
        user = _get_authenticated_user(token)
        user_id = (user or {}).get("id", "")
        if not user_id:
            return _response(401, {"error": "Unauthorized"})

        settings = _get_user_settings(user_id) or {}
        api_key = (settings.get("api_key") or "").strip()
        if not api_key:
            return _response(400, {"error": "No API key saved for this user"})

        body = json.loads(event.get("body") or "{}")
        prompt: str = body.get("prompt", "").strip()
        file_ids: list[str] = body.get("fileIDs") or []

        if not prompt:
            return _response(400, {"error": "prompt is required"})

        job_id = str(uuid.uuid4())
        print(f"[{job_id}] Creating job for user {user_id}, prompt: {prompt[:100]}...")
        _create_job(job_id, user_id)
        print(f"[{job_id}] Job created, invoking agent-loop...")

        boto3.client("lambda").invoke(
            FunctionName=AGENT_LOOP_FUNCTION_NAME,
            InvocationType="Event",  # async fire-and-forget
            Payload=json.dumps({
                "job_id": job_id,
                "prompt": prompt,
                "file_ids": file_ids,
                "settings": {
                    "api_key": api_key,
                    "primary_color": settings.get("primary_color") or "#C00000",
                    "accent_color": settings.get("accent_color") or "#A6CAEC",
                    "logo_url": settings.get("logo_url"),
                },
            }).encode("utf-8"),
        )

        print(f"[{job_id}] Agent-loop invoked successfully")
        return _response(200, {"jobId": job_id})

    except ValueError as exc:
        print(f"[start_job] Auth error: {exc}")
        return _response(401, {"error": "Unauthorized"})
    except Exception as exc:  # noqa: BLE001
        print(f"[start_job] Error: {exc}")
        return _response(500, {"error": str(exc)})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body),
    }
