# =============================================================================
#  Workshop Makefile — thin wrapper around the AgentCore top-up stack
# =============================================================================
#  You EDIT agentcore-topup-stack.yaml (uncomment one fenced section per step),
#  then run `make deploy`. This Makefile just runs the long `aws cloudformation
#  deploy` command for you and reads the base coordinates from config.env.
#
#  Targets:
#    make deploy   - deploy / update the AgentCore top-up CloudFormation stack
#    make build    - rebuild the agent container image + roll the runtime
#                    (use after editing unicorn_rental_agent.py — code changes
#                     don't redeploy via `make deploy`)
#    make outputs  - print the stack outputs (Gateway URL, Runtime ARN, ...)
#    make status   - show the stack's current status
#    make help     - this message
#
#  Everything reads config.env (seeded for you on the Code Editor). Override on
#  the command line, e.g.  make deploy POLICY_MODE=ENFORCE
# =============================================================================

SHELL := /bin/bash

# ---- config.env -------------------------------------------------------------
# Read individual keys from config.env without `include` (ARN values contain ':'
# and would confuse make). Each ?= lets you override on the command line.
CONFIG_ENV   := config.env
cfg = $(shell grep -E '^$(1)=' $(CONFIG_ENV) 2>/dev/null | head -1 | cut -d= -f2-)

REGION           ?= $(call cfg,AWS_REGION)
ARTIFACTS_BUCKET ?= $(call cfg,ARTIFACTS_BUCKET)
ARTIFACTS_PREFIX ?= $(call cfg,ARTIFACTS_PREFIX)

# Stack + parameters
ENV_NAME    ?= agentic-analytics
STACK       ?= agentic-analytics-agentcore
TEMPLATE    ?= agentcore-topup-stack.yaml
POLICY_MODE ?= LOG_ONLY

# Agent code zip (CodeBuild source). Must match AgentCodeS3Key in the template.
AGENT_CODE_KEY ?= agent/agent_code.zip

# Optional Voice step (after Cube). The voice runtime is its own stack/template,
# shipped next to this Makefile. VPC/subnet/Cognito values come from config.env.
VOICE_STACK     ?= agentic-analytics-voice
VOICE_TEMPLATE  ?= voice-agentcore-stack.yaml
VOICE_CODE_KEY  ?= voice/voice_agent_code.zip
VOICE_DIR       ?= ../voice
DEEPGRAM_API_KEY ?=
DEEPGRAM_VOICE_ID ?= aura-2-apollo-en
VPC_ID           ?= $(call cfg,VPC_ID)
SUBNET_IDS       ?= $(call cfg,SUBNET_IDS)
SUBNET1          ?= $(firstword $(subst $(comma), ,$(SUBNET_IDS)))
SUBNET2          ?= $(lastword $(subst $(comma), ,$(SUBNET_IDS)))
COGNITO_POOL     ?= $(call cfg,COGNITO_USER_POOL_ID)
COGNITO_CLIENT   ?= $(call cfg,COGNITO_USER_LOGIN_CLIENT_ID)
comma := ,

# ---- guards -----------------------------------------------------------------
ifeq ($(strip $(REGION)),)
  $(error AWS_REGION not found in config.env — run from app/agentcore_strands/ where config.env lives)
endif

.PHONY: help deploy build outputs status voice-deploy

help:
	@sed -n '2,18p' $(firstword $(MAKEFILE_LIST)) | sed 's/^# \{0,1\}//'

# ---- deploy: structural CFN changes (uncomment a fence, flip a value) --------
deploy:
	@echo "==> Deploying $(STACK) (PolicyMode=$(POLICY_MODE), region=$(REGION))"
	aws cloudformation deploy \
	  --template-file $(TEMPLATE) \
	  --stack-name $(STACK) \
	  --parameter-overrides \
	      EnvironmentName=$(ENV_NAME) \
	      ArtifactsBucket=$(ARTIFACTS_BUCKET) \
	      ArtifactsPrefix=$(ARTIFACTS_PREFIX) \
	      PolicyMode=$(POLICY_MODE) \
	  --capabilities CAPABILITY_NAMED_IAM \
	  --region $(REGION)
	@echo "==> Done. Run 'make outputs' to see the Gateway URL and Runtime ARN."

# ---- outputs / status -------------------------------------------------------
outputs:
	@aws cloudformation describe-stacks --stack-name $(STACK) --region $(REGION) \
	  --query 'Stacks[0].Outputs' --output table

status:
	@aws cloudformation describe-stacks --stack-name $(STACK) --region $(REGION) \
	  --query 'Stacks[0].StackStatus' --output text

# ---- build: rebuild the agent image + roll the runtime ----------------------
# Use after editing unicorn_rental_agent.py. `make deploy` does NOT rebuild the
# image (the container URI is a fixed :latest tag), so a code change needs this.
# It re-zips the agent code, triggers the CodeBuild project the stack created,
# then calls update-agent-runtime RE-SENDING the JWT authorizer + Authorization
# header allowlist + network config — these are NOT defaults, and update-agent-runtime
# REPLACES the whole config, so omitting them would silently revert the runtime
# to IAM auth and every Bearer request would 403.
build:
	@set -euo pipefail; \
	echo "==> [build] zipping agent code"; \
	ZIP=$$(mktemp).zip; \
	zip -j "$$ZIP" unicorn_rental_agent.py unicorn_rental_analytics.sop.md requirements.txt config.env.sample -x '*.pyc' '*__pycache__*' >/dev/null; \
	echo "==> [build] uploading -> s3://$(ARTIFACTS_BUCKET)/$(ARTIFACTS_PREFIX)$(AGENT_CODE_KEY)"; \
	aws s3 cp "$$ZIP" "s3://$(ARTIFACTS_BUCKET)/$(ARTIFACTS_PREFIX)$(AGENT_CODE_KEY)" --region $(REGION); \
	rm -f "$$ZIP"; \
	echo "==> [build] triggering CodeBuild $(ENV_NAME)-agent-build"; \
	BID=$$(aws codebuild start-build --project-name "$(ENV_NAME)-agent-build" --region $(REGION) --query 'build.id' --output text); \
	echo "    build $$BID — waiting (~3 min)…"; \
	while true; do sleep 15; \
	  S=$$(aws codebuild batch-get-builds --ids "$$BID" --region $(REGION) --query 'builds[0].buildStatus' --output text); \
	  echo "    build status: $$S"; \
	  case "$$S" in SUCCEEDED) break;; FAILED|FAULT|STOPPED|TIMED_OUT) echo "build $$S" >&2; exit 1;; esac; \
	done; \
	echo "==> [build] reading current runtime config (to re-send unchanged)"; \
	RID=$$(aws cloudformation describe-stacks --region $(REGION) --stack-name $(STACK) --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeId'].OutputValue" --output text); \
	[ -n "$$RID" ] || { echo "AgentRuntimeId not in stack $(STACK) — run 'make deploy' first" >&2; exit 1; }; \
	RT=$$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$$RID" --region $(REGION)); \
	ROLE=$$(echo "$$RT" | jq -r '.roleArn'); \
	URI=$$(echo "$$RT" | jq -r '.agentRuntimeArtifact.containerConfiguration.containerUri'); \
	NET=$$(echo "$$RT" | jq -c '.networkConfiguration'); \
	ENVV=$$(echo "$$RT" | jq -c '.environmentVariables // {}'); \
	AUTHZ=$$(echo "$$RT" | jq -c '.authorizerConfiguration // empty'); \
	HDR=$$(echo "$$RT" | jq -c '.requestHeaderConfiguration // empty'); \
	EXTRA=(); \
	[ -n "$$AUTHZ" ] && EXTRA+=(--authorizer-configuration "$$AUTHZ"); \
	[ -n "$$HDR" ] && EXTRA+=(--request-header-configuration "$$HDR"); \
	echo "==> [build] update-agent-runtime $$RID (re-pull :latest, preserve JWT auth)"; \
	UPD=$$(aws bedrock-agentcore-control update-agent-runtime \
	  --agent-runtime-id "$$RID" \
	  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$$URI\"}}" \
	  --role-arn "$$ROLE" \
	  --network-configuration "$$NET" \
	  --environment-variables "$$ENVV" \
	  "$${EXTRA[@]}" \
	  --region $(REGION)); \
	VER=$$(echo "$$UPD" | jq -r '.agentRuntimeVersion'); \
	echo "    new runtime version $$VER — waiting for READY…"; \
	while true; do sleep 15; \
	  S=$$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$$RID" --agent-runtime-version "$$VER" --region $(REGION) --query 'status' --output text); \
	  echo "    runtime status: $$S"; \
	  case "$$S" in READY) break;; FAILED) echo "runtime version $$VER FAILED" >&2; exit 1;; esac; \
	done; \
	echo "==> [build] done — runtime $$RID now serving version $$VER"

# ---- voice-deploy: OPTIONAL voice step (after Cube) -------------------------
# Deploys voice-agentcore-stack.yaml — a SEPARATE stack with its own AgentCore
# Runtime (Pipecat voice pipeline, WebRTC + KVS managed TURN) and a JWT signaling
# proxy. It reuses the analytics agent you built: the StrandsAgentRuntimeArn comes
# from the top-up stack's output. VPC/subnet/Cognito values come from config.env.
# Requires DEEPGRAM_API_KEY=... on the command line.
voice-deploy:
	@set -euo pipefail; \
	[ -n "$(DEEPGRAM_API_KEY)" ] || { echo "Set DEEPGRAM_API_KEY=... (get a free key at deepgram.com)" >&2; exit 1; }; \
	echo "==> [voice] reading the analytics Runtime ARN from $(STACK)"; \
	STRANDS_ARN=$$(aws cloudformation describe-stacks --stack-name $(STACK) --region $(REGION) \
	  --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text); \
	[ -n "$$STRANDS_ARN" ] || { echo "AgentRuntimeArn not found — deploy the analytics top-up first (make deploy)" >&2; exit 1; }; \
	echo "==> [voice] zipping voice bot from $(VOICE_DIR)"; \
	ZIP=$$(mktemp).zip; \
	(cd $(VOICE_DIR) && zip -r "$$ZIP" bot.py analytics_processor.py auth.py pyproject.toml uv.lock -x '*.pyc' '*__pycache__*' '*tests*' >/dev/null); \
	echo "==> [voice] uploading -> s3://$(ARTIFACTS_BUCKET)/$(ARTIFACTS_PREFIX)$(VOICE_CODE_KEY)"; \
	aws s3 cp "$$ZIP" "s3://$(ARTIFACTS_BUCKET)/$(ARTIFACTS_PREFIX)$(VOICE_CODE_KEY)" --region $(REGION); \
	rm -f "$$ZIP"; \
	echo "==> [voice] deploying $(VOICE_STACK)"; \
	aws cloudformation deploy \
	  --template-file $(VOICE_TEMPLATE) \
	  --stack-name $(VOICE_STACK) \
	  --parameter-overrides \
	      EnvironmentName=$(ENV_NAME) \
	      ArtifactsBucket=$(ARTIFACTS_BUCKET) \
	      ArtifactsPrefix=$(ARTIFACTS_PREFIX) \
	      VoiceAgentCodeS3Key=$(VOICE_CODE_KEY) \
	      CognitoUserPoolId=$(COGNITO_POOL) \
	      CognitoClientId=$(COGNITO_CLIENT) \
	      StrandsAgentRuntimeArn=$$STRANDS_ARN \
	      ChartBucket=$(ARTIFACTS_BUCKET) \
	      DeepgramApiKey=$(DEEPGRAM_API_KEY) \
	      DeepgramVoiceId=$(DEEPGRAM_VOICE_ID) \
	      VpcId=$(VPC_ID) \
	      PrivateSubnet1Id=$(SUBNET1) \
	      PrivateSubnet2Id=$(SUBNET2) \
	  --capabilities CAPABILITY_NAMED_IAM \
	  --region $(REGION); \
	echo "==> [voice] done. Get the signaling URL with:"; \
	echo "    aws cloudformation describe-stacks --stack-name $(VOICE_STACK) --region $(REGION) \\"; \
	echo "      --query \"Stacks[0].Outputs[?OutputKey=='VoiceSignalingUrl'].OutputValue\" --output text"
