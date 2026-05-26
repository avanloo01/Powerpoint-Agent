"""
Lambda handler for generating a presigned S3 PUT URL so the
frontend can upload a company logo directly to S3.
"""
from __future__ import annotations

import json
import os
import re
from urllib import error as urlerror
from urllib import request as urlrequest

import boto3

S3_LOGO_BUCKET = os.environ.get("LOGO_BUCKET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


def _extract_bearer_token(event: dict) -> str:
    headers = event.get("headers") or {}
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def _get_authenticated_user(token: str) -> dict:
    if not token:
        raise ValueError("Missing bearer token")
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be configured")

    req = urlrequest.Request(
        url=f"{SUPABASE_URL}/auth/v1/user",
        method="GET",
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urlrequest.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise RuntimeError(f"Supabase request failed ({exc.code}): {detail}") from exc


def _file_extension(file_type: str) -> str:
    if not file_type or "/" not in file_type:
        return "png"

    ext = file_type.split("/", 1)[1].lower().strip()
    ext = re.sub(r"[^a-z0-9]", "", ext)
    if not ext:
        return "png"
    return ext


def handler(event: dict, context) -> dict:  # noqa: ANN001
    """AWS Lambda entry point."""
    try:
        token = _extract_bearer_token(event)
        user = _get_authenticated_user(token)
        user_id = (user or {}).get("id", "")
        if not user_id:
            return _response(401, {"error": "Unauthorized"})

        body = json.loads(event.get("body") or "{}")
        file_type: str = body.get("fileType", "image/png")
        ext = _file_extension(file_type)
        logo_key = f"logo/{user_id}/company_logo.{ext}"

        s3 = boto3.client("s3")

        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_LOGO_BUCKET,
                "Key": logo_key,
                "ContentType": file_type,
            },
            ExpiresIn=300,
        )

        # Public URL of the uploaded logo (bucket must have public-read on this key,
        # or a CloudFront distribution fronting it).
        region = os.environ.get("AWS_REGION", "ap-southeast-1")
        public_url = (
            f"https://{S3_LOGO_BUCKET}.s3.{region}.amazonaws.com/{logo_key}"
        )

        return _response(200, {"uploadUrl": upload_url, "publicUrl": public_url})

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
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body),
    }
