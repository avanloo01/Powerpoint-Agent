# PowerPoint Agent

AI-powered PowerPoint presentation generator for slides in school, consulting, or finance.

## Get Started

To generate your first presentation, you have to create an account and enter your Qwen API key. You can get a Qwen API key by going to <https://modelstudio.console.alibabacloud.com/cn-hongkong>

Make sure your region is set to Hong Kong. Then, go to the "Dashboard" in the top navigation bar. Click "API Key" in the left sidebar and create your API key. Copy the API key and paste it into the Settings page of this app.

## Stack

- **Frontend:** React 19 + TypeScript, hosted on S3 behind CloudFront
- **Backend:** Python 3.12 AWS Lambda functions (`start_job`, `agent_loop`, `build_slides`, `upload_logo`)
- **AI:** Qwen 3.7 Plus via the OpenAI-compatible DashScope SDK
- **Infrastructure:** Terraform (AWS — S3, CloudFront, API Gateway, Lambda, IAM)
- **CI/CD:** GitHub Actions

## Pages

| Page | Path | Description |
|---|---|---|
| Home | `/` | Enter a prompt, click Generate, download the `.pptx` |
| Settings | `/settings` | Upload logo, set brand colors, enter Qwen API key |
