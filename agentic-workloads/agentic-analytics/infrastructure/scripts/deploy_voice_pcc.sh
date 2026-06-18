#!/usr/bin/env bash
# Post-CFN deploy for VoiceMode=pipecat-cloud.
#
# Run this AFTER the main CFN stack is deployed with EnableVoice=true and
# VoiceMode=pipecat-cloud (main CFN leaves the UI's VOICE_START_URL empty for PCC
# mode — this script finishes the job). Pipecat Cloud is external SaaS, so its
# pieces can't be pure CloudFormation; this script orchestrates them.
#
# It does five things:
#   1. Create/update the PCC secret set (Deepgram/Daily keys live on PCC's side — CLI-only).
#   2. Deploy the PCC agent (infrastructure/voice-pcc-cr custom-resource stack → REST API).
#   3. Deploy the JWT start proxy (infrastructure/voice-proxy) — created here, not by main CFN.
#   4. Fill the proxy's Secrets Manager placeholder with the PCC PUBLIC key.
#   5. Point the Amplify UI at the proxy (VOICE_START_URL) and redeploy the UI.
#
# Required env:
#   PCC_PAT             Pipecat Cloud Personal Access Token (pcc_pat_...) — to deploy the agent
#   PCC_PUBLIC_KEY      Pipecat Cloud PUBLIC key (pk_...) — the proxy uses this to call /start
#   DEEPGRAM_API_KEY, DAILY_API_KEY
# Optional: AWS_REGION (us-west-2), AMPLIFY_APP_ID, MAIN_STACK, MIN_AGENTS
#
# No demo creds: the PCC bot forwards each signed-in user's own token (per-user
# RBAC/RLS), and the agent selects its voice formatting from the per-request mode
# field — there is no demo identity and no PRESENTER_MODE flag.
set -euo pipefail

# This script lives in infrastructure/scripts/, so repo root is two levels up.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REGION="${AWS_REGION:-us-west-2}"
ENV_NAME="${ENV_NAME:-agentic-analytics}"
MAIN_STACK="${MAIN_STACK:-agentic-analytics-demo}"
ARTIFACTS_BUCKET="${ARTIFACTS_BUCKET:-agentic-analytics-artifacts}"
AGENT_NAME="${PCC_AGENT:-voice-analytics-agent}"
PROXY_STACK="${PROXY_STACK:-${ENV_NAME}-voice-proxy}"

for v in PCC_PAT PCC_PUBLIC_KEY DEEPGRAM_API_KEY DAILY_API_KEY; do
  [ -n "${!v:-}" ] || { echo "ERROR: $v is required"; exit 1; }
done

# Pull backend-derived values from the deployed main stack's nested stacks.
COG_STACK="$(aws cloudformation list-stack-resources --stack-name "$MAIN_STACK" --region "$REGION" \
  --query "StackResourceSummaries[?ResourceType=='AWS::CloudFormation::Stack' && contains(LogicalResourceId,'Cognito')].PhysicalResourceId" --output text)"
POOL_ID="$(aws cloudformation describe-stacks --stack-name "$COG_STACK" --region "$REGION" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)"
CLIENT_ID="$(aws cloudformation describe-stacks --stack-name "$COG_STACK" --region "$REGION" --query "Stacks[0].Outputs[?OutputKey=='UserLoginClientId'].OutputValue" --output text)"
AMPLIFY_APP_ID="${AMPLIFY_APP_ID:-$(aws amplify list-apps --region "$REGION" --query "apps[?name=='${ENV_NAME}-ui' || contains(name,'agentic-analytics')].appId | [0]" --output text)}"
AMPLIFY_ORIGIN="https://main.${AMPLIFY_APP_ID}.amplifyapp.com"

echo "==> 1/5 PCC secret set (Deepgram/Daily keys on Pipecat's side)"
PATH="$ROOT/app/voice/.venv/bin:$PATH" PIPECAT_TOKEN="$PCC_PAT" \
  pipecatcloud secrets set voice-analytics-secrets --skip \
    DEEPGRAM_API_KEY="$DEEPGRAM_API_KEY" DAILY_API_KEY="$DAILY_API_KEY" \
    DEEPGRAM_VOICE_ID="${DEEPGRAM_VOICE_ID:-aura-2-apollo-en}" \
    AWS_REGION="$REGION" COGNITO_CLIENT_ID="$CLIENT_ID" \
    CHART_BUCKET="$ARTIFACTS_BUCKET" >/dev/null
echo "    secret set ready"

echo "==> 2/5 PCC agent (CFN custom resource → PCC REST API)"
PCC_API_KEY="$PCC_PUBLIC_KEY" MIN_AGENTS="${MIN_AGENTS:-0}" AWS_REGION="$REGION" \
  ENV_NAME="$ENV_NAME" AGENT_NAME="$AGENT_NAME" \
  bash "$ROOT/infrastructure/voice-pcc-cr/deploy.sh"

echo "==> 3/5 JWT start proxy (API Gateway + Cognito authorizer)"
( cd "$ROOT/infrastructure/voice-proxy"
  rm -rf build && mkdir -p build && cp index.py build/
  pip3 install --quiet --target build "python-jose[cryptography]==3.3.0" 2>/dev/null
  aws cloudformation package --template-file voice-proxy-stack.yaml \
    --s3-bucket "$ARTIFACTS_BUCKET" --s3-prefix voice-proxy --region "$REGION" \
    --output-template-file build/packaged.yaml >/dev/null
  aws cloudformation deploy --template-file build/packaged.yaml --stack-name "$PROXY_STACK" \
    --region "$REGION" --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
    --parameter-overrides EnvironmentName="$ENV_NAME" CognitoUserPoolId="$POOL_ID" \
      CognitoAppClientId="$CLIENT_ID" PccAgentName="$AGENT_NAME" AllowedOrigin="$AMPLIFY_ORIGIN" )

PROXY_URL="$(aws cloudformation describe-stacks --stack-name "$PROXY_STACK" --region "$REGION" --query "Stacks[0].Outputs[?OutputKey=='VoiceStartUrl'].OutputValue" --output text)"
KEY_SECRET_ARN="$(aws cloudformation describe-stacks --stack-name "$PROXY_STACK" --region "$REGION" --query "Stacks[0].Outputs[?OutputKey=='PccApiKeySecretArn'].OutputValue" --output text)"

echo "==> 4/5 Fill the proxy's PCC-key placeholder in Secrets Manager"
aws secretsmanager put-secret-value --secret-id "$KEY_SECRET_ARN" --secret-string "$PCC_PUBLIC_KEY" --region "$REGION" >/dev/null
echo "    filled $KEY_SECRET_ARN"

echo "==> 5/5 Point the UI at the proxy + redeploy (so the Voice button appears)"
# Patch config.js in the live build and push a new Amplify deployment.
"$ROOT/infrastructure/scripts/_amplify_set_voice_url.sh" "$AMPLIFY_APP_ID" "$PROXY_URL" 2>/dev/null \
  || echo "    (helper not present — set REACT_APP_VOICE_START_URL=$PROXY_URL in Amplify env + redeploy the UI manually)"

echo ""
echo "==> pipecat-cloud voice deployed."
echo "    VOICE_START_URL = $PROXY_URL"
echo "    PCC agent: $AGENT_NAME  | proxy stack: $PROXY_STACK"
