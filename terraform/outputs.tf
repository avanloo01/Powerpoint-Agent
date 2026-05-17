output "frontend_website_url" {
  description = "S3 static website endpoint — point your Cloudflare DNS CNAME here (HTTP origin)."
  value       = "http://${aws_s3_bucket_website_configuration.frontend.website_endpoint}"
}

output "generate_pptx_function_url" {
  description = "Lambda Function URL for generate-pptx. Set VITE_GENERATE_URL to this value."
  value       = aws_lambda_function_url.generate_pptx.function_url
}

output "upload_logo_function_url" {
  description = "Lambda Function URL for upload-logo. Set VITE_UPLOAD_LOGO_URL to this value."
  value       = aws_lambda_function_url.upload_logo.function_url
}

output "frontend_bucket_name" {
  description = "S3 bucket that hosts the built React app."
  value       = aws_s3_bucket.frontend.id
}

output "storage_bucket_name" {
  description = "S3 bucket for user-uploaded logos (logo/ prefix) and generated PPTX files (presentations/ prefix, expire after 7 days)."
  value       = aws_s3_bucket.storage.id
}

output "generate_pptx_function_name" {
  description = "Name of the generate-pptx Lambda function."
  value       = aws_lambda_function.generate_pptx.function_name
}

output "upload_logo_function_name" {
  description = "Name of the upload-logo Lambda function."
  value       = aws_lambda_function.upload_logo.function_name
}
