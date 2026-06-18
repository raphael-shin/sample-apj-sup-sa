# Milestone 1 — Token Spike: Repeat the Test

Confirms that `auth.py` can obtain a Cognito AccessToken headlessly and that
`invoke_agent_runtime` accepts it, MCP tools initialize, and the agent returns
real analytics data.

## Prerequisites

- Python 3.10+
- `uv` (or `pip`) with `boto3` installed
- AWS credentials in env or `~/.aws` with permission to call:
  - `cognito-idp:InitiateAuth`
  - `bedrock-agentcore:InvokeAgentRuntime`

## Setup

```bash
cd server
cp .env.example .env   # then fill in DEMO_USERNAME, DEMO_PASSWORD, AWS_* creds
uv sync                # installs boto3 + pipecat deps from pyproject.toml
```

Minimum vars required for this test (everything else can stay blank):

```
AWS_REGION=us-west-2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
COGNITO_CLIENT_ID=5uqdjp5fvp3k6qgvn184m028ij
DEMO_USERNAME=orion.moonshadow@example-mythicalunicorns.com
DEMO_PASSWORD=...
AWS_AGENT_ARN=arn:aws:bedrock-agentcore:us-west-2:074065412773:runtime/agentic_analytics_agent-FUc4rIClvp
```

## Step 1 — Get a token

```bash
python -c "from auth import get_gateway_token; print(get_gateway_token()[:60], '...')"
```

Expected output: a JWT starting with `eyJ...`

If you see `NotAuthorizedException` the username/password is wrong.
If you see `ResourceNotFoundException` the client ID or region is wrong.

## Step 2 — Invoke AgentCore

```bash
python - <<'EOF'
import json
import boto3
from dotenv import load_dotenv
load_dotenv()
from auth import get_gateway_token

token = get_gateway_token()
print("Token OK:", token[:40], "...")

client = boto3.client("bedrock-agentcore", region_name="us-west-2")
resp = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-west-2:074065412773:runtime/agentic_analytics_agent-FUc4rIClvp",
    payload=json.dumps({
        "prompt": "What unicorns are available this weekend?",
        "gateway_token": token,
    }).encode(),
)

print("HTTP status:", resp["ResponseMetadata"]["HTTPStatusCode"])
print("Session ID: ", resp["runtimeSessionId"])
print("\n--- Agent response (raw SSE) ---")
print(resp["response"].read().decode("utf-8")[:3000])
EOF
```

### What success looks like

```
Token OK: eyJraWQiOiJvb2FqVERx ...
HTTP status: 200
Session ID:  543f6287-c8d7-42d0-b28f-90a7646e6fc4

--- Agent response (raw SSE) ---
data: {"init_event_loop": true}
data: {"start": true}
...
data: {"event": {"contentBlockStart": {"start": {"toolUse": {"name": "PrebakedSQL___get_current_unicorn_availability_tool", ...}}}}}
...
data: {"text": "Here's a summary of your current unicorn fleet availability..."}
```

HTTP 200 + a `runtimeSessionId` + tool calls firing = **Milestone 1 complete.**

## Key finding — use AccessToken, not IdToken

`auth.py` returns the Cognito **AccessToken** (not the IdToken).

The MCP Gateway validates the OAuth AccessToken for scope. The IdToken carries
identity claims (`custom:role`, `custom:account_id`) but is rejected by the Gateway
with `insufficient_scope`. The AccessToken passes Gateway auth, and the agent extracts
RBAC/RLS claims from it internally.

If you see this error from the agent:

```
Failed to load tool <MCPClient>: Failed to start MCP client: the client initialization failed.
```

Check that you are passing the **AccessToken** (not IdToken) as `gateway_token`.

## Response shape note (for Milestone 2)

The deployed agent streams raw Strands SSE events — tool call chunks, metadata, and
text deltas interleaved. The final spoken text arrives as `{"text": "<chunk>"}` events
inside the stream.

`AWSAgentCoreProcessor` expects a simpler shape: `{"response": "<chunk>"}` incremental
text and `{"done": true}` as the terminal event. A voice entrypoint wrapper on the
deployed agent (or a custom processor subclass) will bridge this gap — tracked in the
Milestone 2 plan (spec §7.2).
