variable "aws_region" {
  description = "AWS region to deploy all resources into."
  type        = string
  default     = "ap-southeast-1"
}

variable "project_name" {
  description = "Short name used as a prefix for all resource names."
  type        = string
  default     = "pptx-agent"
}

variable "environment" {
  description = "Deployment environment (e.g. dev, staging, prod)."
  type        = string
  default     = "prod"
}

variable "frontend_bucket_name" {
  description = "Globally unique S3 bucket name for the React frontend."
  type        = string
  # Override this with a unique name, e.g. "pptx-agent-frontend-<your-account-id>"
}

variable "logos_bucket_name" {
  description = "Globally unique S3 bucket name for user-uploaded logos."
  type        = string
  # Override this with a unique name, e.g. "pptx-agent-logos-<your-account-id>"
}

variable "presentations_bucket_name" {
  description = "Globally unique S3 bucket name for generated PPTX files."
  type        = string
  # Override this with a unique name, e.g. "pptx-agent-presentations-<your-account-id>"
}

variable "qwen_model" {
  description = "Qwen model identifier to use for generation."
  type        = string
  default     = "qwen3.6-plus"
}

variable "supabase_url" {
  description = "Supabase project URL, e.g. https://xyzcompany.supabase.co"
  type        = string
}

variable "supabase_anon_key" {
  description = "Supabase anon public key used for access token verification."
  type        = string
  sensitive   = true
}

variable "supabase_service_role_key" {
  description = "Supabase service role key used by Lambda to read user settings."
  type        = string
  sensitive   = true
}

variable "supabase_settings_table" {
  description = "Supabase table containing per-user settings keyed by user_id."
  type        = string
  default     = "user_settings"
}
