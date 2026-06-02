# Bedrock Model Benchmarking

A self-hosted web app for comparing Amazon Bedrock foundation models side-by-side. Send a prompt, get responses from multiple models in parallel, and evaluate quality with a built-in LLM judge — all with real-time latency and cost metrics.

## What it does

- **Multi-model comparison** — select any combination of Bedrock foundation models or cross-region inference profiles and run them against the same prompt simultaneously
- **Live streaming** — responses stream in real time; the first run per model streams while additional runs collect timing data in the background
- **Performance metrics** — TTFT (time to first token), TPOT (time per output token), end-to-end latency at p50/p95 across N runs, input/output token counts, and estimated USD cost per call
- **LLM-as-judge** — pick any model as a judge; it scores candidates on correctness, instruction-following, completeness, and clarity, and optionally compares them against a reference response you paste in (e.g. from GPT or Gemini)
- **RAG** — paste text or upload `.txt`, `.md`, or `.pdf` files; the app chunks and embeds them using Titan embeddings and injects retrieved context into every invocation
- **Multimodal attachments** — attach images and documents directly to prompts; the app drops them silently for models that don't support the format
- **Cost calculator** — live pricing lookup via the AWS Pricing API with a hardcoded fallback table for known models

## Problem it solves

Choosing a Bedrock model for production involves tradeoffs that are hard to measure: latency vs. quality vs. cost. This tool lets you run the exact same prompt against N models at once and get a quantitative comparison in under a minute, including an AI-generated quality ranking. No notebooks, no scripts, no context-switching between the Bedrock playground tabs.

## Running locally

**Prerequisites:** Python 3.12+, AWS credentials with Bedrock access (`bedrock:InvokeModel`, `bedrock:ListFoundationModels`, `bedrock:Converse`, `pricing:GetProducts`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app starts at `http://localhost:8501`. In development mode there is no login, no quota enforcement, and all models are visible.

## Deploying to AWS

The CloudFormation stack at [infra/sample-bedrock-model-benchmarking.yaml](infra/sample-bedrock-model-benchmarking.yaml) provisions the full production stack in one command. The deploy script at [infra/deploy.sh](infra/deploy.sh) handles ECR image build/push and stack creation automatically.

### What gets deployed

| Component | Purpose |
|---|---|
| ECS Fargate (1 vCPU / 2 GB) | Runs the Streamlit container |
| Application Load Balancer | Routes HTTP traffic to the container |
| CloudFront | Public HTTPS entry point; locks the ALB to CloudFront-only traffic via the managed prefix list |
| AWS WAF | Rate-limits to 2000 requests/IP/5 min + AWS managed common rule set (OWASP) |
| Cognito User Pool | Email/password auth; admin-created users only |
| DynamoDB | Per-user daily invocation quota with TTL auto-purge |
| AWS Budgets + Lambda kill-switch | Alerts at 80% of monthly budget cap; automatically sets ECS desired count to 0 at 100% |
| ECR | Container image registry |
| CloudWatch Logs (14-day retention) | App stdout/stderr |

### Prerequisites

- AWS CLI configured with a profile that has permissions to create IAM roles, ECS, ECR, ALB, CloudFront, Cognito, DynamoDB, WAF, Budgets, Lambda, and CloudWatch
- Docker (for building the image)
- Bedrock model access enabled in the target region

### Deploy

```bash
# defaults: ap-south-1, app name "sample-bedrock-model-benchmarking", $200/month budget cap
cd infra
bash deploy.sh

# override any setting via env vars
APP_NAME=sample-bedrock-model-benchmarking \
REGION=us-west-2 \
AWS_PROFILE=my-deploy-profile \
bash deploy.sh
```

The script runs four steps:

1. Creates the ECR repository if it doesn't exist
2. Builds the Docker image (`linux/amd64`) and pushes it with a timestamped tag
3. Deploys the CloudFormation stack (`aws cloudformation deploy`) with `CAPABILITY_NAMED_IAM`
4. Prints the stack outputs including the public CloudFront URL

After deploy, check your inbox for a Cognito invitation email with a temporary password.

### CloudFormation parameters

All parameters have defaults. Override them with `--parameter-overrides` in the deploy script or directly in the console.

| Parameter | Default | Description |
|---|---|---|
| `AppName` | `sample-bedrock-model-benchmarking` | Prefix for all resource names |
| `Region` | `ap-south-1` | Deployment region |
| `ImageTag` | `latest` | ECR image tag to run |
| `AdminEmail` | — | Creates a Cognito admin user and receives budget alerts |
| `MonthlyBudgetUsd` | `200` | Hard budget cap; Lambda kills the ECS service at 100% |
| `AlertEmail` | — | SNS email subscription for budget notifications |
| `DailyInvocationLimit` | `50` | Per-user Bedrock call limit per day |
| `WafRateLimit` | `2000` | WAF requests per 5 min per IP before blocking |
| `VpcId` | — | VPC for the ALB and ECS tasks |
| `SubnetIds` | — | Comma-separated public subnet IDs (minimum 2 AZs) |

### IAM separation

The app uses two distinct IAM principals:

- **`Bedrock Model BenchmarkingRuntimeRole`** — assumed by the ECS task at runtime. Scoped to `bedrock:*`, `dynamodb:GetItem/UpdateItem` (quota table), `cognito-idp:InitiateAuth/SignUp`, `pricing:GetProducts`, and `sts:GetCallerIdentity`. This is the only identity that touches Bedrock.
- **Your deploy profile** — used only by the deploy script to build infra and push images. Never embedded in the container.

### Updating the app

Push a new image and force a new ECS deployment:

```bash
IMAGE_TAG=v2 bash infra/deploy.sh
# then trigger a new deployment to pick up the image
aws ecs update-service --cluster sample-bedrock-model-benchmarking --service sample-bedrock-model-benchmarking --force-new-deployment --region ap-south-1
```

### Tearing down

```bash
aws cloudformation delete-stack --stack-name sample-bedrock-model-benchmarking --region ap-south-1
# ECR images are not deleted automatically — remove the repo manually if needed
aws ecr delete-repository --repository-name sample-bedrock-model-benchmarking --force --region ap-south-1
```

## Environment variables

These are injected automatically by the CloudFormation stack. Set them manually for custom deployments or local overrides.

| Variable | Production default | Description |
|---|---|---|
| `DEPLOY_MODE` | `production` | `production` enables auth and quota; anything else = dev mode |
| `MAX_TOKENS_CAP` | `2048` | Upper bound on the max_tokens slider |
| `RUNS_PER_MODEL_CAP` | `3` | Max parallel runs per model |
| `DAILY_INVOCATION_LIMIT` | `50` | Per-user daily Bedrock call limit (0 = unlimited) |
| `QUOTA_TABLE_NAME` | `sample-bedrock-model-benchmarking-quota` | DynamoDB table for quota tracking |
| `COGNITO_USER_POOL_ID` | — | Cognito user pool for login |
| `COGNITO_CLIENT_ID` | — | Cognito app client ID |
| `COGNITO_REGION` | `ap-south-1` | Region of the Cognito user pool |
| `MODEL_ALLOWLIST` | _(empty)_ | Comma-separated model ID substrings to show (empty = all models) |

## Architecture

```
Browser
  └─► CloudFront (HTTPS)
        └─► WAF (rate limit + managed rules)
              └─► ALB (HTTP, CloudFront prefix list only)
                    └─► ECS Fargate (Streamlit :8501, sticky sessions)
                          ├─► Bedrock (InvokeModel / Converse)
                          ├─► DynamoDB (quota table)
                          └─► Cognito (auth)
```
