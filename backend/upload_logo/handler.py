"""
Lambda handler for generating a presigned S3 PUT URL so the
frontend can upload a company logo directly to S3.
"""
from __future__ import annotations

import json
import logging
import os
from urllib import error as urlerror
from urllib import request as urlrequest

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

S3_LOGO_BUCKET = os.environ.get("LOGO_BUCKET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

# Map MIME types to safe file extensions.
_MIME_TO_EXT = {
    # Images
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/svg+xml": "svg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/bmp": "bmp",
    # Documents
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/csv": "csv",
}


def _extract_bearer_token(event: dict) -> str:
    headers = event.get("headers") or {}
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


class AuthError(ValueError):
    """Raised when the bearer token is missing or invalid."""


def _get_authenticated_user(token: str) -> dict:
    if not token:
        raise AuthError("Missing bearer token")
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
    """Return a safe file extension for the given MIME type."""
    if not file_type:
        return "png"
    # Use an explicit allowlist so unsafe or unknown types fall back to png.
    return _MIME_TO_EXT.get(file_type.lower().strip(), "png")


def handler(event: dict, context) -> dict:  # noqa: ANN001
    """AWS Lambda entry point — handles POST (presigned upload URL) and DELETE."""

    http_method = event.get("requestContext", {}).get("http", {}).get("method", "POST")

    if http_method == "OPTIONS":
        return _response(200, {})

    try:
        token = _extract_bearer_token(event)
        user = _get_authenticated_user(token)
        user_id = (user or {}).get("id", "")
        if not user_id:
            return _response(401, {"error": "Unauthorized"})

        region = os.environ.get("AWS_REGION", "ap-southeast-1")
        s3 = boto3.client("s3", region_name=region)

        if http_method == "DELETE":
            return _handle_delete(s3, user_id, region)
        else:
            return _handle_post(s3, user_id, region, event)

    except AuthError:
        return _response(401, {"error": "Unauthorized"})
    except Exception:
        logger.exception("Unhandled error in upload_logo handler")
        return _response(500, {"error": "Internal server error"})


def _handle_post(s3, user_id: str, region: str, event: dict) -> dict:
    """Generate a presigned PUT URL for uploading a logo or document."""
    body = json.loads(event.get("body") or "{}")
    file_type: str = body.get("fileType", "image/png")
    upload_type: str = body.get("type", "logo")  # "logo" or "doc"
    ext = _file_extension(file_type)

    if upload_type == "doc":
        import uuid as _uuid

        doc_id = str(_uuid.uuid4())
        key = f"docs/{user_id}/{doc_id}.{ext}"
    else:
        key = f"logo/{user_id}/company_logo.{ext}"

    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": S3_LOGO_BUCKET,
            "Key": key,
            "ContentType": file_type,
        },
        ExpiresIn=300,
        HttpMethod="PUT",
    )

    public_url = f"https://{S3_LOGO_BUCKET}.s3.{region}.amazonaws.com/{key}"

    logger.info("Generated presigned URL for user=%s type=%s key=%s", user_id, upload_type, key)
    return _response(200, {"uploadUrl": upload_url, "publicUrl": public_url, "key": key})


def _handle_delete(s3, user_id: str, region: str) -> dict:
    """Delete all logo objects for the given user from S3."""
    if not S3_LOGO_BUCKET:
        raise RuntimeError("LOGO_BUCKET environment variable is not set")

    prefix = f"logo/{user_id}/"

    try:
        # List all objects under the user's logo prefix.
        objects_to_delete: list[dict] = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_LOGO_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                objects_to_delete.append({"Key": obj["Key"]})

        if objects_to_delete:
            s3.delete_objects(
                Bucket=S3_LOGO_BUCKET,
                Delete={"Objects": objects_to_delete},
            )
            logger.info(
                "Deleted %d logo object(s) for user=%s: %s",
                len(objects_to_delete),
                user_id,
                [o["Key"] for o in objects_to_delete],
            )
        else:
            logger.info("No logo objects to delete for user=%s", user_id)

        return _response(200, {"deleted": True})

    except Exception:
        logger.exception("Failed to delete logo objects for user=%s", user_id)
        return _response(500, {"error": "Failed to delete logo from S3"})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,DELETE,OPTIONS",
        },
        "body": json.dumps(body),
    }
