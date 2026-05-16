output "cloudfront_url" {
  description = "HTTPS URL of the CloudFront distribution (use this as the app URL)."
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "api_gateway_url" {
  description = "Base URL of the HTTP API Gateway. Set REACT_APP_API_URL to this value."
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "frontend_bucket_name" {
  description = "S3 bucket that hosts the built React app."
  value       = aws_s3_bucket.frontend.id
}

output "logos_bucket_name" {
  description = "S3 bucket for user-uploaded company logos."
  value       = aws_s3_bucket.logos.id
}

output "presentations_bucket_name" {
  description = "S3 bucket for generated PPTX files (expire after 7 days)."
  value       = aws_s3_bucket.presentations.id
}

output "generate_pptx_function_name" {
  description = "Name of the generate-pptx Lambda function."
  value       = aws_lambda_function.generate_pptx.function_name
}

output "upload_logo_function_name" {
  description = "Name of the upload-logo Lambda function."
  value       = aws_lambda_function.upload_logo.function_name
}
