output "frontend_website_url" {
  description = "S3 static website endpoint — point your Cloudflare DNS CNAME here (HTTP origin)."
  value       = "http://${aws_s3_bucket_website_configuration.frontend.website_endpoint}"
}

output "frontend_bucket_name" {
  description = "S3 bucket that hosts the built frontend app."
  value       = aws_s3_bucket.frontend.id
}

output "storage_bucket_name" {
  description = "Shared S3 bucket for uploaded logos and generated presentations."
  value       = aws_s3_bucket.storage.id
}

output "upload_logo_function_name" {
  description = "Name of the upload-logo Lambda function."
  value       = aws_lambda_function.upload_logo.function_name
}

output "upload_logo_function_url" {
  description = "Lambda Function URL for upload-logo. Set VITE_UPLOAD_LOGO_URL to this value."
  value       = aws_lambda_function_url.upload_logo.function_url
}

output "start_job_function_url" {
  description = "Lambda Function URL for start-job. Set VITE_START_JOB_URL to this value."
  value       = aws_lambda_function_url.start_job.function_url
}

output "agent_loop_function_name" {
  description = "Name of the async agent-loop Lambda function."
  value       = aws_lambda_function.agent_loop.function_name
}
