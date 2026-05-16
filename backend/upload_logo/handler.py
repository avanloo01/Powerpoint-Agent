"""
Lambda handler for generating a presigned S3 PUT URL so the
frontend can upload a company logo directly to S3.
"""
from __future__ import annotations

import json
import os

import boto3

S3_LOGO_BUCKET = os.environ.get("LOGO_BUCKET", "")
LOGO_KEY = "logo/company_logo"


def handler(event: dict, context) -> dict:  # noqa: ANN001
    """AWS Lambda entry point."""
    try:
        body = json.loads(event.get("body") or "{}")
        file_type: str = body.get("fileType", "image/png")

        s3 = boto3.client("s3")

        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_LOGO_BUCKET,
                "Key": LOGO_KEY,
                "ContentType": file_type,
            },
            ExpiresIn=300,
        )

        # Public URL of the uploaded logo (bucket must have public-read on this key,
        # or a CloudFront distribution fronting it).
        region = os.environ.get("AWS_REGION", "us-east-1")
        public_url = (
            f"https://{S3_LOGO_BUCKET}.s3.{region}.amazonaws.com/{LOGO_KEY}"
        )

        return _response(200, {"uploadUrl": upload_url, "publicUrl": public_url})

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
