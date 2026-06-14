# PowerPoint Agent — Architecture & Developer Guide

> **TL;DR:** A React SPA hosted on S3 + CloudFront lets users describe a presentation. A Python Lambda (`start_job`) creates an async job, then an agent-loop Lambda (`agent_loop`) orchestrates Research → Structure → Build stages using Qwen AI and returns a generated `.pptx` via a presigned S3 URL. User settings and brand colors are stored in Supabase; logos are stored in S3.

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
  └─ HTTPS ──► Lambda Function URLs
                  │
                  ├─ POST /start_job ──► Lambda: start_job
                  │       │                  │
                  │       │                  ├─ Validates auth via Supabase
                  │       │                  ├─ Creates jobs row (status: pending)
                  │       │                  └─ Invokes agent_loop asynchronously
                  │       │                  └─ Returns { jobId }
                  │       │
                  │       └──► Lambda: agent_loop (async)
                  │               │
                  │               ├─ Stage 1: Research — Qwen + web search
                  │               ├─ Stage 2: Structure — JSON blueprint (charts, columns, etc.)
                  │               ├─ Stage 3a: Build — AI generates python-pptx code
                  │               ├─ Stage 3b: Execute — runs code with self-correction
                  │               ├─ S3 (presentations bucket) — presigned URL stored in jobs row
                  │               └─ Updates jobs row (status: done/error)
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
| Lambda Function URLs | Direct HTTPS endpoints, simpler than API Gateway |
| Lambda per function | Simple, scales to zero, easy to deploy independently |
| Async job-based generation | Research → Structure → Build pipeline takes time; async prevents timeout |
| Supabase for settings & jobs | User auth, settings persistence, and job tracking in one place |
| AI-generated python-pptx code | Dynamic slide layouts (charts, columns, news cards) — not hardcoded templates |
| Presigned S3 URLs for uploads | Browser uploads directly to S3 — no Lambda bandwidth cost |
| Presigned S3 URLs for downloads | Presentations are private; link expires in 1 hour |

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
│   ├── start_job/
│   │   ├── handler.py          # Lambda: validate auth, create job, invoke agent_loop
│   │   └── requirements.txt
│   ├── agent_loop/
│   │   ├── handler.py          # Lambda: async 3-stage generation pipeline
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
- **Generate** button — POSTs `{ prompt }` to the `start_job` Lambda, gets back `{ jobId }`
- Polls the Supabase `jobs` table for status (`pending` → `researching` → `structuring` → `building` → `done`)
- Shows a live status message while the job progresses
- On completion, shows a **Download** link backed by the presigned S3 URL
- `⚙️ Settings` button in the top-right corner navigates to `/settings`
- If no API key is saved, a hint nudges the user to the Settings page

#### `SettingsPage` (`/settings`)
- **Back** button returns to `/`
- **Logo upload**: drag-and-drop or file picker → calls `/upload-logo` for a presigned PUT URL → browser uploads directly to S3
- **Brand colors**: native `<input type="color">` pickers for *Primary* and *Accent* colors
- **Qwen API key**: password input, saved to Supabase
- **Save Settings**: upserts values to the `user_settings` table in Supabase

### Dependencies

| Package | Purpose |
|---|---|
| `react` / `react-dom` | UI framework |
| `react-router-dom` v6 | Client-side routing |
| `axios` | HTTP client for API calls |
| `@supabase/supabase-js` | Supabase client (auth, settings, job polling) |

---

## 4. Backend (Lambda)

### `start_job` Lambda

**Route:** Lambda Function URL — `POST /`

**Request body:**
```json
{
  "prompt":   "A 5-slide overview of renewable energy trends in 2025",
  "fileIDs":  []  // optional, reserved for future use
}
```

**Response body:**
```json
{ "jobId": "uuid-here" }
```

**Flow:**
1. Extracts bearer token from the `Authorization` header
2. Validates the token via Supabase Auth (`GET /auth/v1/user`)
3. Loads user settings (API key, brand colors, logo URL) from the Supabase `user_settings` table
4. Validates the prompt and API key
5. Creates a `jobs` row in Supabase with `status: "pending"`
6. Invokes the `agent_loop` Lambda asynchronously (`InvocationType="Event"`)
7. Returns `{ jobId }` to the caller

**Environment variables:**
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` — Supabase connection
- `SUPABASE_SETTINGS_TABLE` — settings table name (default `user_settings`)
- `AGENT_LOOP_FUNCTION_NAME` — name of the agent-loop Lambda to invoke

---

### `agent_loop` Lambda

**Invoked by:** `start_job` (async, fire-and-forget)

**Input payload:**
```json
{
  "job_id":   "uuid-here",
  "prompt":   "...",
  "file_ids": [],
  "settings": {
    "api_key":       "...",
    "primary_color": "#C00000",
    "accent_color":  "#A6CAEC",
    "logo_url":      "https://..."
  }
}
```

**Pipeline (3 stages):**

| Stage | Lambda step | What it does |
|---|---|---|
| 1 | **Research** | Calls Qwen with `enable_search=true` to gather current facts, stats, and trends |
| 2 | **Structure** | Designs a rich JSON blueprint with slides, columns, charts, bullet lists, news cards, etc. |
| 3a | **Build (code gen)** | Asks Qwen to write a `build_presentation(prs)` function using python-pptx |
| 3b | **Build (execute)** | Runs the generated code in a restricted namespace with up to 3 self-correction attempts |

**After pipeline:**
1. Uploads the `.pptx` to S3 under `presentations/{job_id}.pptx`
2. Generates a presigned GET URL (1-hour expiry)
3. Updates the Supabase `jobs` row with `status: "done"` and the `download_url`
4. On failure, sets `status: "error"` with an error message

**Environment variables:**
- `OUTPUT_BUCKET` — presentations S3 bucket name
- `QWEN_MODEL` — Qwen model to use (default: `qwen3.6-plus`)
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` — for job status updates

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
| `aws_s3_bucket.storage` | S3 | Shared storage for logos (`logo/`), presentations (`presentations/`), and icons (`icons/`) |
| `aws_s3_bucket_lifecycle_configuration.presentations` | S3 Lifecycle | Expires `presentations/` objects after 7 days |
| `aws_lambda_function_url.start_job` | Lambda URL | Direct HTTPS endpoint for async job creation |
| `aws_lambda_function_url.upload_logo` | Lambda URL | Direct HTTPS endpoint for logo upload URL creation |
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
start_job_function_url     = "https://xxxx.lambda-url.ap-southeast-1.on.aws/"
upload_logo_function_url   = "https://yyyy.lambda-url.ap-southeast-1.on.aws/"
frontend_bucket_name    = "<frontend-bucket>"
storage_bucket_name     = "<shared-storage-bucket>"
agent_loop_function_name = "pptx-agent-agent-loop"
```

Use these Function URL outputs directly in frontend env vars (`VITE_GENERATE_URL`, `VITE_UPLOAD_LOGO_URL`). `VITE_GENERATE_URL` should point to the `start_job` Function URL.

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
| `GENERATE_URL` | `start_job` Lambda URL (`VITE_GENERATE_URL`) |
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
| Frontend build | `VITE_GENERATE_URL` | GitHub secret `GENERATE_URL` (the `start_job` Lambda URL) / `.env` |
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
echo "VITE_GENERATE_URL=https://<start-job-lambda-url>" > .env
echo "VITE_UPLOAD_LOGO_URL=https://<upload-logo-lambda-url>" >> .env
npm run dev     # local Vite dev server
npm test        # run tests
```

### Backend

```bash
cd backend/agent_loop
pip install -r requirements.txt

cd ../start_job
pip install -r requirements.txt
# Run handlers locally (e.g. with python-lambda-local or AWS SAM)
```

Using **AWS SAM** (recommended for local Lambda testing):

```bash
# Install SAM CLI: https://docs.aws.amazon.com/serverless-application-model/
sam local invoke StartJobFunction \
  --event events/start_job_event.json \
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