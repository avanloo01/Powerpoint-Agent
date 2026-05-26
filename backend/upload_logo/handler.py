"""
Lambda handler for the upload-logo endpoint.

Routes requests based on Content-Type:
- application/json  → generate a presigned S3 PUT URL for a company logo upload
- multipart/form-data → upload one or more documents to the Qwen DashScope
                        Files API and return the resulting file IDs
"""
from __future__ import annotations

import base64
import email
import json
import os
import re
import uuid
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import boto3

S3_LOGO_BUCKET = os.environ.get("LOGO_BUCKET", "")
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_SETTINGS_TABLE = os.environ.get("SUPABASE_SETTINGS_TABLE", "user_settings")


# ── helpers ──────────────────────────────────────────────────────────────────

def _extract_bearer_token(event: dict) -> str:
    headers = event.get("headers") or {}
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def _supabase_get(url: str, headers: dict) -> dict:
    req = urlrequest.Request(url=url, method="GET", headers=headers)
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

    return _supabase_get(
        f"{SUPABASE_URL}/auth/v1/user",
        {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
    )


def _get_user_api_key(user_id: str) -> str:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured")

    query = urlparse.urlencode(
        {"user_id": f"eq.{user_id}", "select": "api_key", "limit": "1"}
    )
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_SETTINGS_TABLE}?{query}"
    data = _supabase_get(
        url,
        {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        },
    )
    if isinstance(data, list) and data:
        return (data[0].get("api_key") or "").strip()
    return ""


def _file_extension(file_type: str) -> str:
    if not file_type or "/" not in file_type:
        return "png"
    ext = file_type.split("/", 1)[1].lower().strip()
    ext = re.sub(r"[^a-z0-9]", "", ext)
    return ext or "png"


# ── logo upload ───────────────────────────────────────────────────────────────

def _handle_logo_upload(event: dict, user_id: str) -> dict:
    body = json.loads(event.get("body") or "{}")
    file_type: str = body.get("fileType", "image/png")
    ext = _file_extension(file_type)
    logo_key = f"logo/{user_id}/company_logo.{ext}"

    s3 = boto3.client("s3")
    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": S3_LOGO_BUCKET, "Key": logo_key, "ContentType": file_type},
        ExpiresIn=300,
    )

    region = os.environ.get("AWS_REGION", "ap-southeast-1")
    public_url = f"https://{S3_LOGO_BUCKET}.s3.{region}.amazonaws.com/{logo_key}"
    return _response(200, {"uploadUrl": upload_url, "publicUrl": public_url})


# ── document upload ───────────────────────────────────────────────────────────

def _parse_multipart(body: bytes, content_type: str) -> list[tuple[str, str, bytes, str]]:
    """Return ``(field_name, filename, content, mime_type)`` for every file part."""
    mime_header = f"MIME-Version: 1.0\r\nContent-Type: {content_type}\r\n\r\n"
    msg = email.message_from_bytes(mime_header.encode() + body)

    files: list[tuple[str, str, bytes, str]] = []
    for part in msg.walk():
        if part.get_content_disposition() == "form-data":
            params = dict(part.get_params(header="content-disposition") or [])
            filename = params.get("filename", "")
            if filename:
                content = part.get_payload(decode=True) or b""
                mime_type = part.get_content_type() or "application/octet-stream"
                files.append((params.get("name", ""), filename, content, mime_type))
    return files


def _upload_file_to_qwen(api_key: str, filename: str, content: bytes, mime_type: str) -> str:
    """Upload one file to DashScope Files API; return the file ID."""
    boundary = uuid.uuid4().hex
    payload = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="purpose"\r\n\r\n',
        b"file-extract\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])

    req = urlrequest.Request(
        url=f"{QWEN_BASE_URL}/files",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urlrequest.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))["id"]
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"Qwen file upload failed ({exc.code}): {detail}") from exc


def _handle_doc_upload(event: dict, user_id: str) -> dict:
    api_key = _get_user_api_key(user_id)
    if not api_key:
        return _response(400, {"error": "No API key saved for this user"})

    # Lambda Function URLs may base64-encode binary bodies
    is_base64 = event.get("isBase64Encoded", False)
    raw_body = event.get("body") or ""
    body_bytes = base64.b64decode(raw_body) if is_base64 else raw_body.encode("latin-1")

    headers = event.get("headers") or {}
    content_type = headers.get("content-type") or headers.get("Content-Type") or ""

    files = _parse_multipart(body_bytes, content_type)
    if not files:
        return _response(400, {"error": "No files found in request"})

    file_ids = [
        _upload_file_to_qwen(api_key, filename, content, mime_type)
        for _field, filename, content, mime_type in files
    ]
    return _response(200, {"fileIDs": file_ids})


# ── entry point ───────────────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:  # noqa: ANN001
    """AWS Lambda entry point."""
    try:
        token = _extract_bearer_token(event)
        user = _get_authenticated_user(token)
        user_id = (user or {}).get("id", "")
        if not user_id:
            return _response(401, {"error": "Unauthorized"})

        headers = event.get("headers") or {}
        content_type = headers.get("content-type") or headers.get("Content-Type") or ""

        if "multipart/form-data" in content_type:
            return _handle_doc_upload(event, user_id)
        return _handle_logo_upload(event, user_id)

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
