# PowerPoint Agent

AI-powered PowerPoint presentation generator — React frontend hosted on S3 + CloudFront, Python Lambda backend using Qwen AI and python-pptx.

## Quick Start

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full developer guide, including first-time deployment instructions, local development setup, and an explanation of all infrastructure components.

## Stack

- **Frontend:** React 18 + TypeScript, hosted on S3 behind CloudFront
- **Backend:** Python 3.12 AWS Lambda functions (`generate_pptx`, `upload_logo`)
- **AI:** Qwen AI via the OpenAI-compatible DashScope SDK
- **Infrastructure:** Terraform (AWS — S3, CloudFront, API Gateway, Lambda, IAM)
- **CI/CD:** GitHub Actions

## Pages

| Page | Path | Description |
|---|---|---|
| Home | `/` | Enter a prompt, click Generate, download the `.pptx` |
| Settings | `/settings` | Upload logo, set brand colors, enter Qwen API key |
