# PowerPoint Agent

AI-powered PowerPoint presentation generator for slides in school, consulting, or finance.

## Stack

- **Frontend:** React 19 + TypeScript, hosted on S3 behind CloudFront
- **Backend:** Python 3.12 AWS Lambda functions (`start_job`, `agent_loop`, `build_slides`, `upload_logo`)
- **AI:** Qwen 3.6 Plus via the OpenAI-compatible DashScope SDK
- **Infrastructure:** Terraform (AWS — S3, CloudFront, API Gateway, Lambda, IAM)
- **CI/CD:** GitHub Actions

## Pages

| Page | Path | Description |
|---|---|---|
| Home | `/` | Enter a prompt, click Generate, download the `.pptx` |
| Settings | `/settings` | Upload logo, set brand colors, enter Qwen API key |
