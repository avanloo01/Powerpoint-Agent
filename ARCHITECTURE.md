# PowerPoint Agent — Architecture & Developer Guide

> **TL;DR:** A React SPA hosted on S3 + CloudFront lets users describe a presentation. A Python Lambda calls Qwen AI and returns a generated `.pptx` via a presigned S3 URL. User settings (API key, brand colors) live in browser cookies; logos are stored in a separate S3 bucket.

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Repository Layout](#2-repository-layout)
3. [Frontend](#3-frontend)
4. [Backend (Lambda)](#4-backend-lambda)
5. [Infrastructure (Terraform)](#5-infrastructure-terraform)
6. [CI/CD (GitHub Actions)](#6-cicd-github-actions)
7. [Environment Variables & Secrets](#7-environment-variables--secrets)
8. [Local Development](#8-local-development)
9. [First-Time Deployment](#9-first-time-deployment)
10. [Future Improvements](#10-future-improvements)

---

## 1. High-Level Architecture

```
Browser
  │
  ├─ HTTPS ──► CloudFront ──► S3 (frontend bucket)
  │               React SPA served as static files
  │
  └─ HTTPS ──► API Gateway (HTTP API)
                  │
                  ├─ POST /generate ──► Lambda: generate_pptx
                  │                        │
                  │                        ├─ Qwen AI (DashScope API, OpenAI-compatible)
                  │                        └─ S3 (presentations bucket) — presigned URL returned
                  │
                  └─ POST /upload-logo ──► Lambda: upload_logo
                                              │
                                              └─ S3 (logos bucket) — presigned PUT URL returned
                                                   (browser PUTs the file directly to S3)
```

### Key Design Decisions

| Decision | Rationale |
|---|---|
| React SPA on S3 + CloudFront | Zero server ops, global CDN, cheap |
| API Gateway HTTP API (v2) | Low latency, built-in CORS, cheaper than REST API |
| Lambda per function | Simple, scales to zero, easy to deploy independently |
| Presigned S3 URLs for uploads | Browser uploads directly to S3 — no Lambda bandwidth cost |
| Presigned S3 URLs for downloads | Presentations are private; link expires in 1 hour |
| API key in browser cookie | No backend secret storage required; user owns their key |
| Colors in browser cookie | Purely client-side preference, no persistence needed |

---

## 2. Repository Layout

```
.
├── .github/
│   └── workflows/
│       └── ci-cd.yml           # GitHub Actions pipeline
├── frontend/                   # React + TypeScript SPA
│   ├── public/
│   │   └── index.html
│   ├── src/
│   │   ├── App.tsx             # Router setup
│   │   ├── index.tsx           # React entry point
│   │   ├── pages/
│   │   │   ├── HomePage.tsx    # Prompt input + generate button
│   │   │   └── SettingsPage.tsx# Logo upload, colors, API key
│   │   └── services/
│   │       └── api.ts          # API Gateway client (axios)
│   ├── package.json
│   └── tsconfig.json
├── backend/
│   ├── generate_pptx/
│   │   ├── handler.py          # Lambda: generate presentation
│   │   └── requirements.txt
│   └── upload_logo/
│       ├── handler.py          # Lambda: return presigned upload URL
│       └── requirements.txt
├── terraform/
│   ├── main.tf                 # All AWS resources
│   ├── variables.tf            # Input variables
│   └── outputs.tf              # Useful outputs (URLs, bucket names)
├── ARCHITECTURE.md             # This file
└── README.md
```

---

## 3. Frontend

### Pages

#### `HomePage` (`/`)
- Large prompt `<textarea>`
- **Generate** button — POSTs `{ prompt, apiKey, primaryColor, accentColor }` to `/generate`
- On success, shows a **Download** link backed by the presigned S3 URL
- `⚙️ Settings` button in the top-right corner navigates to `/settings`
- If no API key cookie is set, a hint nudges the user to the Settings page

#### `SettingsPage` (`/settings`)
- **Back** button returns to `/`
- **Logo upload**: drag-and-drop or file picker → calls `/upload-logo` for a presigned PUT URL → browser uploads directly to S3
- **Brand colors**: native `<input type="color">` pickers for *Primary* and *Accent* colors
- **Qwen API key**: password input
- **Save Settings**: writes all values to cookies (1-year expiry, `SameSite=Strict`)

### Cookie Keys

| Cookie | Content | Default |
|---|---|---|
| `qwen_api_key` | Qwen DashScope API key | _(empty)_ |
| `primary_color` | CSS hex, e.g. `#4f46e5` | `#4f46e5` |
| `accent_color` | CSS hex, e.g. `#f59e0b` | `#f59e0b` |
| `logo_url` | Public S3 URL of uploaded logo | _(empty)_ |

### Dependencies

| Package | Purpose |
|---|---|
| `react` / `react-dom` | UI framework |
| `react-router-dom` v6 | Client-side routing |
| `axios` | HTTP client for API calls |
| `js-cookie` | Cookie read/write helper |

---

## 4. Backend (Lambda)

### `generate_pptx` Lambda

**Route:** `POST /generate`

**Request body:**
```json
{
  "prompt":       "A 5-slide overview of renewable energy",
  "apiKey":       "<Qwen DashScope API key>",
  "primaryColor": "#4f46e5",
  "accentColor":  "#f59e0b",
  "logoUrl":      "https://..."  // optional
}
```

**Response body:**
```json
{ "downloadUrl": "https://s3.amazonaws.com/..." }
```

**Flow:**
1. Validates `prompt` and `apiKey`
2. Calls Qwen via the OpenAI-compatible SDK (`qwen-turbo` model by default)
3. Parses the JSON array of `{ title, content }` slides
4. Builds a `.pptx` with `python-pptx`, applying brand colors
5. Uploads the file to the presentations S3 bucket
6. Returns a presigned GET URL (1-hour expiry)

**Environment variables:**
- `OUTPUT_BUCKET` — presentations S3 bucket name
- `QWEN_MODEL` — Qwen model to use (default: `qwen-turbo`)

---

### `upload_logo` Lambda

**Route:** `POST /upload-logo`

**Request body:**
```json
{ "fileType": "image/png" }
```

**Response body:**
```json
{
  "uploadUrl": "https://s3.amazonaws.com/...?...",
  "publicUrl": "https://<logos-bucket>.s3.<region>.amazonaws.com/logo/company_logo"
}
```

**Flow:**
1. Generates a presigned `PUT` URL for `logo/company_logo` in the logos bucket (5-minute expiry)
2. Returns the upload URL and the resulting public URL
3. The browser PUTs the file directly to S3 — no Lambda bandwidth needed

**Environment variables:**
- `LOGO_BUCKET` — logos S3 bucket name

---

## 5. Infrastructure (Terraform)

All resources live in `terraform/main.tf`. Run from the `terraform/` directory.

### Resources Created

| Resource | Type | Purpose |
|---|---|---|
| `aws_s3_bucket.frontend` | S3 | React build files |
| `aws_s3_bucket.logos` | S3 | User-uploaded logos (public read) |
| `aws_s3_bucket.presentations` | S3 | Generated PPTX files (private, 7-day TTL) |
| `aws_cloudfront_distribution.frontend` | CloudFront | HTTPS CDN for the React app |
| `aws_cloudfront_origin_access_control.frontend` | CF OAC | Restricts S3 access to CF only |
| `aws_apigatewayv2_api.main` | API GW HTTP API | Routes to Lambda functions |
| `aws_lambda_function.generate_pptx` | Lambda | Generates presentations |
| `aws_lambda_function.upload_logo` | Lambda | Returns presigned upload URLs |
| `aws_iam_role.lambda_exec` | IAM Role | Lambda execution role |
| `aws_iam_role_policy.lambda_s3` | IAM Policy | S3 read/write for Lambdas |
| `aws_cloudwatch_log_group.*` | CloudWatch | Lambda log retention (14 days) |

### Required Variables

Set these in a `terraform.tfvars` file or via `-var` flags:

```hcl
frontend_bucket_name      = "pptx-agent-frontend-<account-id>"
logos_bucket_name         = "pptx-agent-logos-<account-id>"
presentations_bucket_name = "pptx-agent-presentations-<account-id>"
```

> **Tip:** Bucket names must be globally unique. Appending your AWS account ID is a common pattern.

### Optional Variables

```hcl
aws_region   = "ap-southeast-1"   # default
project_name = "pptx-agent"  # used as resource name prefix
environment  = "prod"
qwen_model   = "qwen-turbo"
```

### Key Outputs

After `terraform apply`:

```
cloudfront_url        = "https://d1xxxx.cloudfront.net"   ← app URL
api_gateway_url       = "https://xxxx.execute-api.us-east-1.amazonaws.com"
frontend_bucket_name  = "pptx-agent-frontend-xxxx"
```

Set `REACT_APP_API_URL` (GitHub secret / `.env.local`) to the `api_gateway_url` value before the next build.

---

## 6. CI/CD (GitHub Actions)

File: `.github/workflows/ci-cd.yml`

### Jobs

```
push/PR to main
     │
     ├─ backend-test        (always)  pytest + ruff lint
     ├─ frontend-build      (always)  npm ci → npm test → npm build
     │
     ├─ terraform-plan      (PR only) terraform init → validate → plan
     │
     └─ deploy              (main push only, after backend-test + frontend-build)
            ├─ pip install Lambda deps into source dirs
            ├─ terraform apply
            ├─ aws s3 sync (build → frontend bucket)
            └─ CloudFront invalidation (/*  )
```

### Required GitHub Secrets

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM user / OIDC role access key |
| `AWS_SECRET_ACCESS_KEY` | Corresponding secret key |
| `REACT_APP_API_URL` | API Gateway base URL (from Terraform output) |
| `TF_FRONTEND_BUCKET` | Frontend S3 bucket name |
| `TF_LOGOS_BUCKET` | Logos S3 bucket name |
| `TF_PRESENTATIONS_BUCKET` | Presentations S3 bucket name |

> **Recommended:** Replace long-lived IAM keys with GitHub OIDC for AWS. See [AWS docs](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html).

---

## 7. Environment Variables & Secrets

| Scope | Key | Where set |
|---|---|---|
| Frontend build | `REACT_APP_API_URL` | GitHub secret / `.env.local` |
| Lambda | `OUTPUT_BUCKET` | Terraform (env var) |
| Lambda | `LOGO_BUCKET` | Terraform (env var) |
| Lambda | `QWEN_MODEL` | Terraform (env var) |
| CI/CD | `AWS_ACCESS_KEY_ID` | GitHub secret |
| CI/CD | `AWS_SECRET_ACCESS_KEY` | GitHub secret |
| CI/CD | `TF_*_BUCKET` | GitHub secrets |

---

## 8. Local Development

### Frontend

```bash
cd frontend
npm install
# Create a local env file
echo "REACT_APP_API_URL=https://<api-gateway-url>" > .env.local
npm start       # http://localhost:3000
npm test        # run Jest tests
```

### Backend

```bash
cd backend/generate_pptx
pip install -r requirements.txt
# Run handler locally (e.g. with python-lambda-local or AWS SAM)
```

Using **AWS SAM** (recommended for local Lambda testing):

```bash
# Install SAM CLI: https://docs.aws.amazon.com/serverless-application-model/
sam local invoke GeneratePptxFunction \
  --event events/generate_event.json \
  --env-vars env.json
```

### Terraform

```bash
cd terraform
terraform init
terraform plan \
  -var="frontend_bucket_name=my-frontend-dev" \
  -var="logos_bucket_name=my-logos-dev" \
  -var="presentations_bucket_name=my-presentations-dev"
```

---

## 9. First-Time Deployment

1. **Fork / clone** this repository.
2. **Create an IAM user** with sufficient permissions (S3, Lambda, API GW, CloudFront, IAM) and add the credentials as GitHub Secrets.
3. **Choose globally unique bucket names** and add them as GitHub Secrets (`TF_FRONTEND_BUCKET`, `TF_LOGOS_BUCKET`, `TF_PRESENTATIONS_BUCKET`).
4. **Push to `main`** — the CI/CD pipeline will:
   - Run tests
   - Package and deploy Lambda functions
   - Create all AWS infrastructure via Terraform
   - Sync the React build to S3
   - Invalidate the CloudFront cache
5. **Note the outputs** (`cloudfront_url`, `api_gateway_url`) from the Terraform apply step in the Actions log.
6. **Set `REACT_APP_API_URL`** GitHub Secret to the `api_gateway_url` value.
7. **Re-run the deploy job** (or push a trivial commit) so the frontend rebuild picks up the API URL.
8. Open `cloudfront_url` in your browser — the app should be live!

---

## 10. Future Improvements

- [ ] **Logo in presentations** — download the logo from S3 inside `generate_pptx/handler.py` and insert it on each slide using `python-pptx` image shapes.
- [ ] **Unit tests** — add `pytest` tests for the Lambda handlers using `moto` to mock AWS services.