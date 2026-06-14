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
2. Calls Qwen via the OpenAI-compatible SDK (`qwen3.6-plus` model by default)
3. Parses the JSON array of `{ title, content }` slides
4. Builds a `.pptx` with `python-pptx`, applying brand colors
5. Uploads the file to the presentations S3 bucket
6. Returns a presigned GET URL (1-hour expiry)

**Environment variables:**
- `OUTPUT_BUCKET` — presentations S3 bucket name
- `QWEN_MODEL` — Qwen model to use (default: `qwen3.6-plus`)

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
| `aws_s3_bucket.frontend` | S3 | Frontend static files |
| `aws_s3_bucket.storage` | S3 | Shared storage for logos (`logo/`) and presentations (`presentations/`) |
| `aws_s3_bucket_lifecycle_configuration.presentations` | S3 Lifecycle | Expires `presentations/` objects after 7 days |
| `aws_lambda_function_url.generate_pptx` | Lambda URL | Direct HTTPS endpoint for sync generation |
| `aws_lambda_function_url.upload_logo` | Lambda URL | Direct HTTPS endpoint for logo upload URL creation |
| `aws_lambda_function_url.start_job` | Lambda URL | Direct HTTPS endpoint for async job creation |
| `aws_lambda_function.generate_pptx` | Lambda | Synchronous generation endpoint |
| `aws_lambda_function.start_job` | Lambda | Creates async job record and triggers agent loop |
| `aws_lambda_function.agent_loop` | Lambda | Async multi-stage generation loop |
| `aws_lambda_function.upload_logo` | Lambda | Returns presigned upload URL under `logo/` |
| `aws_iam_role.lambda_exec` | IAM Role | Lambda execution role |
| `aws_iam_role_policy.lambda_s3` | IAM Policy | Shared-bucket read/write for Lambdas |
| `aws_cloudwatch_log_group.*` | CloudWatch | Lambda log retention (14 days) |

### Required Variables

Set these in a `terraform.tfvars` file or via `-var` flags:

```hcl
frontend_bucket_name = "pptx-agent-frontend-<account-id>"
storage_bucket_name  = "pptx-agent-storage-<account-id>"
```

> **Tip:** Bucket names must be globally unique. Appending your AWS account ID is a common pattern.

### Optional Variables

```hcl
aws_region   = "ap-southeast-1"   # default
project_name = "pptx-agent"  # used as resource name prefix
environment  = "prod"
qwen_model   = "qwen3.6-plus"
```

### Key Outputs

After `terraform apply`:

```
generate_pptx_function_url = "https://xxxx.lambda-url.ap-southeast-1.on.aws/"
upload_logo_function_url   = "https://yyyy.lambda-url.ap-southeast-1.on.aws/"
start_job_function_url     = "https://zzzz.lambda-url.ap-southeast-1.on.aws/"
frontend_bucket_name    = "<frontend-bucket>"
storage_bucket_name     = "<shared-storage-bucket>"
agent_loop_function_name = "pptx-agent-agent-loop"
```

Use these Function URL outputs directly in frontend env vars (`VITE_GENERATE_URL`, `VITE_UPLOAD_LOGO_URL`, `VITE_START_JOB_URL`).

---

## 6. CI/CD (GitHub Actions)

File: `.github/workflows/ci-cd.yml`

### Jobs

```
push/PR to main
     │
     ├─ backend-test    (always)  pytest + ruff lint
     ├─ frontend-build  (always)  npm ci → npm test → npm build
     │
     └─ terraform-plan  (PR only) terraform init → validate → plan

Terraform apply is intentionally manual (not executed by CI).
```

### Required GitHub Secrets

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM access key |
| `AWS_SECRET_ACCESS_KEY` | IAM secret key |
| `GENERATE_URL` | Lambda generate URL (`VITE_GENERATE_URL`) |
| `UPLOAD_LOGO_URL` | Lambda upload-logo URL (`VITE_UPLOAD_LOGO_URL`) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (Terraform var) |
| `SUPABASE_SETTINGS_TABLE` | Supabase settings table name (usually `user_settings`) |
| `TF_FRONTEND_BUCKET` | Frontend S3 bucket name |
| `TF_STORAGE_BUCKET` | Shared storage bucket name |

> **Recommended:** Replace long-lived IAM keys with GitHub OIDC for AWS. See [AWS docs](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html).

---

## 7. Environment Variables & Secrets

| Scope | Key | Where set |
|---|---|---|
| Frontend build | `VITE_GENERATE_URL` | GitHub secret `GENERATE_URL` / `.env` |
| Frontend build | `VITE_UPLOAD_LOGO_URL` | GitHub secret `UPLOAD_LOGO_URL` / `.env` |
| Frontend build | `VITE_SUPABASE_URL` | GitHub secret `SUPABASE_URL` |
| Frontend build | `VITE_SUPABASE_ANON_KEY` | GitHub secret `SUPABASE_ANON_KEY` |
| Frontend build | `VITE_SUPABASE_SETTINGS_TABLE` | GitHub secret `SUPABASE_SETTINGS_TABLE` |
| Lambda | `OUTPUT_BUCKET` | Terraform env var (`aws_s3_bucket.storage.id`) |
| Lambda | `LOGO_BUCKET` | Terraform env var (`aws_s3_bucket.storage.id`) |
| Lambda | `QWEN_MODEL` | Terraform env var |
| Lambda | `SUPABASE_*` | Terraform env vars |
| CI/CD | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | GitHub secrets |
| CI/CD | `TF_FRONTEND_BUCKET`, `TF_STORAGE_BUCKET` | GitHub secrets |

---

## 8. Local Development

### Frontend

```bash
cd frontend
npm install
# Create a local env file
echo "VITE_GENERATE_URL=https://<generate-lambda-url>" > .env
echo "VITE_UPLOAD_LOGO_URL=https://<upload-logo-lambda-url>" >> .env
npm run dev     # local Vite dev server
npm test        # run tests
```

### Backend

```bash
cd backend/generate_pptx
pip install -r requirements.txt

cd ../agent_loop
pip install -r requirements.txt
# Run handlers locally (e.g. with python-lambda-local or AWS SAM)
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
  -var="storage_bucket_name=my-storage-dev"
```

---

## 9. First-Time Deployment

1. **Fork / clone** this repository.
2. **Create an IAM user** with sufficient permissions (S3, Lambda, API GW, CloudFront, IAM) and add the credentials as GitHub Secrets.
3. **Choose globally unique bucket names** and add GitHub Secrets (`TF_FRONTEND_BUCKET`, `TF_STORAGE_BUCKET`).
4. **Push a PR** — CI will run tests/build and Terraform plan only.
5. **Run Terraform apply manually** from your machine with the same variable values used in CI.
6. **Set frontend secrets** (`GENERATE_URL`, Supabase keys/table) so the frontend build can target live backend services.
7. Rebuild/redeploy frontend artifacts to your hosting bucket as needed.

---

## 10. Future Improvements

- [ ] **Logo in presentations** — download the logo from S3 inside `generate_pptx/handler.py` and insert it on each slide using `python-pptx` image shapes.
- [ ] **Unit tests** — add `pytest` tests for the Lambda handlers using `moto` to mock AWS services.