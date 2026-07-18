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

variable "storage_bucket_name" {
  description = "Globally unique S3 bucket name shared by user-uploaded logos and generated PPTX files."
  type        = string
  # Override this with a unique name, e.g. "pptx-agent-storage-<your-account-id>"
}

variable "qwen_model" {
  description = "Qwen model identifier to use for generation."
  type        = string
  default     = "qwen3.7-plus"
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

variable "encryption_key" {
  description = "32+ character key used to encrypt/decrypt user API keys via pgcrypto."
  type        = string
  sensitive   = true
}

variable "resend_api_key" {
  description = "Resend API key for sending completion emails."
  type        = string
  sensitive   = true
}

variable "resend_from_email" {
  description = "Verified sender email address for Resend (e.g. noreply@yourdomain.com)."
  type        = string
  default     = "noreply@lemaiyanlabs.org"
}
