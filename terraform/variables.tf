variable "aws_region" {
  description = "AWS region to deploy all resources into."
  type        = string
  default     = "us-east-1"
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
  default     = "qwen-turbo"
}
