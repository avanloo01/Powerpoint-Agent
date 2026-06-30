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

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

# Versioning intentionally disabled — the GitHub repo is the source of truth
# and rebuilds are cheap. No need for S3 version history.

resource "aws_s3_bucket_website_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "index.html"
  }
}

resource "aws_s3_bucket_policy" "frontend_public_read" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.frontend.arn}/*"
      }
    ]
  })
}

# ─────────────────────────────────────────────
# S3 — Shared Storage (logos + presentations)
# ─────────────────────────────────────────────

resource "aws_s3_bucket" "storage" {
  bucket = var.storage_bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "storage" {
  bucket = aws_s3_bucket.storage.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_cors_configuration" "storage" {
  bucket = aws_s3_bucket.storage.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "GET"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_policy" "storage_public_read_logos" {
  bucket = aws_s3_bucket.storage.id
  depends_on = [
    aws_s3_bucket_public_access_block.storage,
  ]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.storage.arn}/logo/*"
      }
    ]
  })
}

# ─────────────────────────────────────────────
# S3 — Generated Presentations lifecycle
# ─────────────────────────────────────────────

resource "aws_s3_bucket_lifecycle_configuration" "presentations" {
  bucket = aws_s3_bucket.storage.id

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
          "s3:DeleteObject",
          "s3:GeneratePresignedUrl"
        ]
        Resource = [
          "${aws_s3_bucket.storage.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.storage.arn
        ]
      }
    ]
  })
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
      LOGO_BUCKET       = aws_s3_bucket.storage.id
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
# Lambda — agent_loop
# ─────────────────────────────────────────────

data "archive_file" "agent_loop" {
  type        = "zip"
  source_dir  = "${path.module}/../backend/agent_loop"
  output_path = "${path.module}/.terraform/lambda_zips/agent_loop.zip"
}

resource "aws_lambda_function" "agent_loop" {
  function_name    = "${var.project_name}-agent-loop"
  filename         = data.archive_file.agent_loop.output_path
  source_code_hash = data.archive_file.agent_loop.output_base64sha256
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512
  tags             = local.common_tags

  environment {
    variables = {
      OUTPUT_BUCKET             = aws_s3_bucket.storage.id
      QWEN_MODEL                = var.qwen_model
      SUPABASE_URL              = var.supabase_url
      SUPABASE_SERVICE_ROLE_KEY = var.supabase_service_role_key
    }
  }
}

resource "aws_cloudwatch_log_group" "agent_loop" {
  name              = "/aws/lambda/${aws_lambda_function.agent_loop.function_name}"
  retention_in_days = 14
  tags              = local.common_tags
}

resource "aws_iam_role_policy" "lambda_invoke_agent_loop" {
  name = "${var.project_name}-lambda-invoke-agent-loop"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [aws_lambda_function.agent_loop.arn]
      }
    ]
  })
}

# ─────────────────────────────────────────────
# Lambda — start_job
# ─────────────────────────────────────────────

data "archive_file" "start_job" {
  type        = "zip"
  source_dir  = "${path.module}/../backend/start_job"
  output_path = "${path.module}/.terraform/lambda_zips/start_job.zip"
}

resource "aws_lambda_function" "start_job" {
  function_name    = "${var.project_name}-start-job"
  filename         = data.archive_file.start_job.output_path
  source_code_hash = data.archive_file.start_job.output_base64sha256
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  tags             = local.common_tags

  environment {
    variables = {
      SUPABASE_URL             = var.supabase_url
      SUPABASE_ANON_KEY        = var.supabase_anon_key
      SUPABASE_SERVICE_ROLE_KEY = var.supabase_service_role_key
      SUPABASE_SETTINGS_TABLE  = var.supabase_settings_table
      AGENT_LOOP_FUNCTION_NAME = aws_lambda_function.agent_loop.function_name
    }
  }
}

resource "aws_cloudwatch_log_group" "start_job" {
  name              = "/aws/lambda/${aws_lambda_function.start_job.function_name}"
  retention_in_days = 14
  tags              = local.common_tags
}

# ─────────────────────────────────────────────
# Lambda Function URLs (no API Gateway)
# ─────────────────────────────────────────────

resource "aws_lambda_function_url" "upload_logo" {
  function_name      = aws_lambda_function.upload_logo.function_name
  authorization_type = "NONE"
}

# Explicit resource-based policy so the Function URL permission is tracked
# in terraform state and cannot be silently lost.
resource "aws_lambda_permission" "upload_logo_url" {
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.upload_logo.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# Starting October 2025, Function URLs require a SECOND permission
# (lambda:InvokeFunction). Without it the Function URL returns 403.
resource "aws_lambda_permission" "upload_logo_invoke" {
  statement_id  = "FunctionURLAllowInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.upload_logo.function_name
  principal     = "*"
}

resource "aws_lambda_function_url" "start_job" {
  function_name      = aws_lambda_function.start_job.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "start_job_url" {
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.start_job.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# Second permission required since October 2025.
resource "aws_lambda_permission" "start_job_invoke" {
  statement_id  = "FunctionURLAllowInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.start_job.function_name
  principal     = "*"
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
