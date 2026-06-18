# Milestone 3 — Voice SOP: Repeat the Test

Confirms that the agent responds in plain spoken sentences with no markdown, no tables,
and no special characters when the voice SOP is active.

## What was built

| Artifact | Location |
|----------|----------|
| Voice SOP | `server/unicorn_rental_voice.sop.md` (also at `s3://kiennt-agentic-analytics-artifacts/sops/unicorn_rental_voice.sop.md`) |
| Agent change | `resources/agentic-analytics-workshop/app/agentcore_strands/unicorn_rental_agent.py` — `load_system_prompt()` accepts `s3_key_override`; `agent_invocation` reads `sop_s3_key` from payload |
| CFN change | `resources/agentic-analytics-workshop/infrastructure/stacks/agentcore-stack.yaml` — `SOP_S3_BUCKET: !Ref ArtifactsBucket` added to `AgentRuntime` env vars |
| Pipeline change | `server/analytics_processor.py` — payload now includes `"sop_s3_key": "sops/unicorn_rental_voice.sop.md"` |

## How it works

`analytics_processor.py` adds `"sop_s3_key": "sops/unicorn_rental_voice.sop.md"` to every
payload sent to AgentCore. The deployed agent reads this key, fetches the voice SOP from S3,
and uses it as the system prompt for that request instead of the default text SOP.

The `SOP_S3_BUCKET` env var must be set in the runtime so `load_system_prompt()` knows which
bucket to read from. Without it the agent silently falls back to the local text SOP bundled
in the container.

## Smoke-test (boto3)

Run from `server/` with credentials and `.env` populated:

```bash
cd server
python3 - <<'EOF'
import json, boto3
from auth import get_gateway_token

token = get_gateway_token()
client = boto3.client("bedrock-agentcore", region_name="us-west-2")
resp = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-west-2:074065412773:runtime/agentic_analytics_agent-FUc4rIClvp",
    payload=json.dumps({
        "prompt": "What unicorns are available this weekend?",
        "gateway_token": token,
        "sop_s3_key": "sops/unicorn_rental_voice.sop.md",
    }).encode(),
)

raw = resp["response"].read().decode("utf-8")
texts = []
for line in raw.split("\n"):
    if not line.startswith("data: "):
        continue
    try:
        d = json.loads(line[6:])
        t = d.get("event", {}).get("contentBlockDelta", {}).get("delta", {}).get("text")
        if t:
            texts.append(t)
    except Exception:
        pass

response = "".join(texts)
print("--- Spoken response ---")
print(response)
print()
artifacts = [c for c in ["**", "##", "| ", "* ", "- ", "```"] if c in response]
if artifacts:
    print(f"WARNING: markdown artifacts found: {artifacts}")
else:
    print("✓ No markdown artifacts detected")
EOF
```

### What success looks like

```
--- Spoken response ---
You have forty-three unicorns available this weekend. The most affordable options start
at about three hundred dollars an hour, like Tejat Posterior Sugilite and Yildun Brookite,
both with teleportation and weather control abilities. Would you like me to filter by
capacity, price range, or specific magic abilities?

✓ No markdown artifacts detected
```

## Redeploying after changes to the voice SOP

The voice SOP is loaded from S3 at request time — no container rebuild needed. Just
upload the updated file:

```bash
aws s3 cp server/unicorn_rental_voice.sop.md \
  s3://kiennt-agentic-analytics-artifacts/sops/unicorn_rental_voice.sop.md \
  --region us-west-2
```

Changes take effect on the next invocation.

## Redeploying after changes to unicorn_rental_agent.py

A container rebuild and runtime update are required (the agent code is bundled in the image):

```bash
# 1. Authenticate to ECR
aws ecr get-login-password --region us-west-2 \
  | docker login --username AWS --password-stdin \
    074065412773.dkr.ecr.us-west-2.amazonaws.com

# 2. Build from the agentcore_strands directory
cd resources/agentic-analytics-workshop/app/agentcore_strands
docker build -f Dockerfile.deploy -t agentic-analytics-agent:latest .

# 3. Tag and push
docker tag agentic-analytics-agent:latest \
  074065412773.dkr.ecr.us-west-2.amazonaws.com/agentic-analytics-agent:latest
docker push \
  074065412773.dkr.ecr.us-west-2.amazonaws.com/agentic-analytics-agent:latest

# 4. Update the runtime (triggers a new container pull)
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id "agentic_analytics_agent-FUc4rIClvp" \
  --region us-west-2 \
  --agent-runtime-artifact '{"containerConfiguration": {"containerUri": "074065412773.dkr.ecr.us-west-2.amazonaws.com/agentic-analytics-agent:latest"}}' \
  --role-arn "arn:aws:iam::074065412773:role/agentic-analytics-agent-runtime-role" \
  --network-configuration '{"networkMode": "PUBLIC"}' \
  --environment-variables '{"AWS_REGION":"us-west-2","BEDROCK_MODEL_ID":"global.anthropic.claude-opus-4-6-v1","BYPASS_TOOL_CONSENT":"true","GATEWAY_URL":"https://agenticanalyticsmcpgateway-jeuddx5bfs.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp","GUARDRAIL_ID":"arn:aws:bedrock:us-west-2:074065412773:guardrail/qbtm99qgmfyj","GUARDRAIL_VERSION":"DRAFT","MEMORY_ID":"unicorn_rental_agent_memory-qkXOPfBF1M","SOP_S3_BUCKET":"kiennt-agentic-analytics-artifacts"}'

# 5. Wait for READY
aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id "agentic_analytics_agent-FUc4rIClvp" \
  --region us-west-2 \
  --query 'status' --output text
```

## SOP_S3_BUCKET — why it must be set

`load_system_prompt()` only reads from S3 when `SOP_S3_BUCKET` is set. Without it the
function falls back to the local `.sop.md` file bundled in the container (the text SOP),
regardless of the `sop_s3_key` in the payload. The variable is now in `agentcore-stack.yaml`
so future CFN deployments include it automatically. If you ever redeploy the full stack
from scratch, no manual step is needed.

## Investigative notes — what was tried and why it failed

**Prompt injection approach (blocked by Bedrock Guardrail):**
The first attempt prepended voice formatting instructions to the user query:
`"[VOICE MODE] Answer in 1-3 sentences... User query: <actual question>"`.
The Bedrock Guardrail has `PROMPT_ATTACK` at `HIGH` input strength, which correctly
classified this as a prompt injection attempt and replaced the entire response with the
topic rejection message. Any instruction-style text mixed into the user query will hit
this filter. The per-request SOP swap via `sop_s3_key` is the right approach.
