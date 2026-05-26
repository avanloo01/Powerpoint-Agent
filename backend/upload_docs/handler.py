"""
Lambda handler for uploading documents to the Qwen DashScope Files API.

The caller sends a multipart/form-data POST with one or more file fields.
Each file is forwarded to DashScope's /files endpoint using the user's own
API key (fetched from Supabase).  The resulting DashScope file IDs are returned
so the frontend can pass them to the generate_pptx Lambda for context-aware
slide generation.
"""
from __future__ import annotations

import base64
import email
import json
import os
import uuid
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_SETTINGS_TABLE = os.environ.get("SUPABASE_SETTINGS_TABLE", "user_settings")


def _extract_bearer_token(event: dict) -> str:
    headers = event.get("headers") or {}
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def _supabase_request(method: str, url: str, headers: dict, body: dict | None = None) -> dict:
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


def _get_authenticated_user(token: str) -> dict:
    if not token:
        raise ValueError("Missing bearer token")
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be configured")

    url = f"{SUPABASE_URL}/auth/v1/user"
    return _supabase_request(
        "GET",
        url,
        {
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {token}",
        },
    )


def _get_user_settings(user_id: str) -> dict | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured")

    query = urlparse.urlencode(
        {
            "user_id": f"eq.{user_id}",
            "select": "api_key",
            "limit": "1",
        }
    )
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_SETTINGS_TABLE}?{query}"
    data = _supabase_request(
        "GET",
        url,
        {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        },
    )

    if isinstance(data, list) and data:
        return data[0]
    return None


def _parse_multipart(body: bytes, content_type: str) -> list[tuple[str, str, bytes, str]]:
    """Parse multipart/form-data body.

    Returns a list of ``(field_name, filename, content, mime_type)`` tuples
    for every part that carries a filename (i.e. file fields).
    """
    mime_header = f"MIME-Version: 1.0\r\nContent-Type: {content_type}\r\n\r\n"
    full_msg = mime_header.encode() + body
    msg = email.message_from_bytes(full_msg)

    files: list[tuple[str, str, bytes, str]] = []
    for part in msg.walk():
        if part.get_content_disposition() == "form-data":
            params = part.get_params(header="content-disposition")
            params_dict = dict(params) if params else {}
            field_name = params_dict.get("name", "")
            filename = params_dict.get("filename", "")
            if filename:
                content = part.get_payload(decode=True) or b""
                mime_type = part.get_content_type() or "application/octet-stream"
                files.append((field_name, filename, content, mime_type))
    return files


def _upload_file_to_qwen(api_key: str, filename: str, content: bytes, mime_type: str) -> str:
    """Upload a single file to the Qwen DashScope Files API.

    Returns the DashScope file ID (e.g. ``"file-abc123"``).
    """
    boundary = uuid.uuid4().hex

    body_parts: list[bytes] = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="purpose"\r\n\r\n',
        b"file-extract\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    payload = b"".join(body_parts)

    url = f"{QWEN_BASE_URL}/files"
    req = urlrequest.Request(
        url=url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    try:
        with urlrequest.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["id"]
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"Qwen file upload failed ({exc.code}): {detail}") from exc


def handler(event: dict, context) -> dict:  # noqa: ANN001
    """AWS Lambda entry point."""
    try:
        token = _extract_bearer_token(event)
        user = _get_authenticated_user(token)
        user_id = (user or {}).get("id", "")
        if not user_id:
            return _response(401, {"error": "Unauthorized"})

        settings = _get_user_settings(user_id) or {}
        api_key: str = (settings.get("api_key") or "").strip()
        if not api_key:
            return _response(400, {"error": "No API key saved for this user"})

        # Decode the request body (Lambda Function URLs may base64-encode binary)
        is_base64 = event.get("isBase64Encoded", False)
        raw_body = event.get("body") or ""
        body_bytes = base64.b64decode(raw_body) if is_base64 else raw_body.encode("latin-1")

        headers = event.get("headers") or {}
        content_type = headers.get("content-type") or headers.get("Content-Type") or ""

        if "multipart/form-data" not in content_type:
            return _response(400, {"error": "Expected multipart/form-data"})

        files = _parse_multipart(body_bytes, content_type)
        if not files:
            return _response(400, {"error": "No files found in request"})

        # Upload each file to Qwen DashScope and collect the returned IDs
        file_ids: list[str] = []
        for _field_name, filename, content, mime_type in files:
            file_id = _upload_file_to_qwen(api_key, filename, content, mime_type)
            file_ids.append(file_id)

        return _response(200, {"fileIDs": file_ids})

    except ValueError:
        return _response(401, {"error": "Unauthorized"})
    except Exception as exc:  # noqa: BLE001
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
