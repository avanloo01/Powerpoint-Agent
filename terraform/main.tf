terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # Optional: uncomment to store state remotely in S3
  # backend "s3" {
  #   bucket = "my-terraform-state-bucket"
  #   key    = "powerpoint-agent/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.aws_region
}

# ─────────────────────────────────────────────
# S3 — Frontend Static Website
# ─────────────────────────────────────────────

resource "aws_s3_bucket" "frontend" {
  bucket = var.frontend_bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_website_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "index.html"
  }
}

# ─────────────────────────────────────────────
# S3 — Logo Storage
# ─────────────────────────────────────────────

resource "aws_s3_bucket" "logos" {
  bucket = var.logos_bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "logos" {
  bucket = aws_s3_bucket.logos.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_cors_configuration" "logos" {
  bucket = aws_s3_bucket.logos.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "GET"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_policy" "logos_public_read" {
  bucket = aws_s3_bucket.logos.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.logos.arn}/logo/*"
      }
    ]
  })
}

# ─────────────────────────────────────────────
# S3 — Generated Presentations
# ─────────────────────────────────────────────

resource "aws_s3_bucket" "presentations" {
  bucket = var.presentations_bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "presentations" {
  bucket = aws_s3_bucket.presentations.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "presentations" {
  bucket = aws_s3_bucket.presentations.id

  rule {
    id     = "expire-presentations"
    status = "Enabled"

    expiration {
      days = 7
    }

    filter {
      prefix = "presentations/"
    }
  }
}

# ─────────────────────────────────────────────
# IAM — Lambda Execution Role
# ─────────────────────────────────────────────

resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-lambda-exec"
  tags = local.common_tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_s3" {
  name = "${var.project_name}-lambda-s3-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:GeneratePresignedUrl"
        ]
        Resource = [
          "${aws_s3_bucket.presentations.arn}/*",
          "${aws_s3_bucket.logos.arn}/*"
        ]
      }
    ]
  })
}

# ─────────────────────────────────────────────
# Lambda — generate_pptx
# ─────────────────────────────────────────────

data "archive_file" "generate_pptx" {
  type        = "zip"
  source_dir  = "${path.module}/../backend/generate_pptx"
  output_path = "${path.module}/.terraform/lambda_zips/generate_pptx.zip"
}

resource "aws_lambda_function" "generate_pptx" {
  function_name    = "${var.project_name}-generate-pptx"
  filename         = data.archive_file.generate_pptx.output_path
  source_code_hash = data.archive_file.generate_pptx.output_base64sha256
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512
  tags             = local.common_tags

  environment {
    variables = {
      OUTPUT_BUCKET              = aws_s3_bucket.presentations.id
      QWEN_MODEL                 = var.qwen_model
      SUPABASE_URL               = var.supabase_url
      SUPABASE_ANON_KEY          = var.supabase_anon_key
      SUPABASE_SERVICE_ROLE_KEY  = var.supabase_service_role_key
      SUPABASE_SETTINGS_TABLE    = var.supabase_settings_table
    }
  }
}

resource "aws_cloudwatch_log_group" "generate_pptx" {
  name              = "/aws/lambda/${aws_lambda_function.generate_pptx.function_name}"
  retention_in_days = 14
  tags              = local.common_tags
}

# ─────────────────────────────────────────────
# Lambda — upload_logo
# ─────────────────────────────────────────────

data "archive_file" "upload_logo" {
  type        = "zip"
  source_dir  = "${path.module}/../backend/upload_logo"
  output_path = "${path.module}/.terraform/lambda_zips/upload_logo.zip"
}

resource "aws_lambda_function" "upload_logo" {
  function_name    = "${var.project_name}-upload-logo"
  filename         = data.archive_file.upload_logo.output_path
  source_code_hash = data.archive_file.upload_logo.output_base64sha256
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  tags             = local.common_tags

  environment {
    variables = {
      LOGO_BUCKET       = aws_s3_bucket.logos.id
      SUPABASE_URL      = var.supabase_url
      SUPABASE_ANON_KEY = var.supabase_anon_key
    }
  }
}

resource "aws_cloudwatch_log_group" "upload_logo" {
  name              = "/aws/lambda/${aws_lambda_function.upload_logo.function_name}"
  retention_in_days = 14
  tags              = local.common_tags
}

# ─────────────────────────────────────────────
# API Gateway (HTTP API v2)
# ─────────────────────────────────────────────

resource "aws_apigatewayv2_api" "main" {
  name          = "${var.project_name}-api"
  protocol_type = "HTTP"
  tags          = local.common_tags

  cors_configuration {
    allow_headers = ["Content-Type", "Authorization"]
    allow_methods = ["POST", "OPTIONS"]
    allow_origins = ["*"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true
  tags        = local.common_tags
}

# --- /generate integration ---

resource "aws_apigatewayv2_integration" "generate_pptx" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.generate_pptx.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "generate_pptx" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /generate"
  target    = "integrations/${aws_apigatewayv2_integration.generate_pptx.id}"
}

resource "aws_lambda_permission" "apigw_generate_pptx" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.generate_pptx.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*/generate"
}

# --- /upload-logo integration ---

resource "aws_apigatewayv2_integration" "upload_logo" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.upload_logo.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "upload_logo" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /upload-logo"
  target    = "integrations/${aws_apigatewayv2_integration.upload_logo.id}"
}

resource "aws_lambda_permission" "apigw_upload_logo" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.upload_logo.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*/upload-logo"
}

# ─────────────────────────────────────────────
# CloudFront — Frontend Distribution
# ─────────────────────────────────────────────

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.project_name}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  tags                = local.common_tags

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "S3-${aws_s3_bucket.frontend.id}"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "S3-${aws_s3_bucket.frontend.id}"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 3600
    max_ttl     = 86400
  }

  # SPA fallback — serve index.html for 404/403 so React Router works
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# Grant CloudFront OAC permission to read the frontend bucket
resource "aws_s3_bucket_policy" "frontend_oac" {
  bucket = aws_s3_bucket.frontend.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowCloudFrontOAC"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.frontend.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn
          }
        }
      }
    ]
  })
}

# ─────────────────────────────────────────────
# Locals
# ─────────────────────────────────────────────

locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
