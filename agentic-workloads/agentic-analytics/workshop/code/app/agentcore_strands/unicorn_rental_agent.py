#!/usr/bin/env python3
"""
AgenticAnalytics AgentCore Integration with Gateway
Uses AgentCore Gateway instead of local MCP server
"""

import os
import json
import time
import asyncio
import boto3
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
# AgentCore excludes .env from deployment packages, so we use config.env
# which IS bundled. Locally, .env takes precedence if it exists.
_script_dir = Path(__file__).resolve().parent
_project_dir = _script_dir.parent
for _candidate in [_project_dir / "config.env", _project_dir / ".env", _script_dir / "config.env"]:
    if _candidate.exists():
        load_dotenv(_candidate)
        break

# Set bypass tool consent for AgentCore
os.environ["BYPASS_TOOL_CONSENT"] = "true"

# Strands imports
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from strands.hooks import HookProvider, HookRegistry, AgentInitializedEvent, MessageAddedEvent
from mcp.client.streamable_http import streamablehttp_client
from datetime import datetime, timezone

# AgentCore imports
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient

# Load SOP from S3 or local fallback
def load_system_prompt():
    """Load SOP from S3 if configured, otherwise use local file"""
    s3_bucket = os.getenv("SOP_S3_BUCKET")
    s3_key = os.getenv("SOP_S3_KEY", "sops/unicorn_rental_analytics.sop.md")
    
    print(f"DEBUG: SOP_S3_BUCKET={s3_bucket}, SOP_S3_KEY={s3_key}")
    
    if s3_bucket:
        try:
            s3 = boto3.client('s3')
            response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
            print(f"[OK] Loaded SOP from s3://{s3_bucket}/{s3_key}")
            return response['Body'].read().decode('utf-8')
        except Exception as e:
            print(f"⚠️ Failed to load SOP from S3: {e}, using local fallback")
    
    local_path = Path(__file__).parent / "unicorn_rental_analytics.sop.md"
    with open(local_path, 'r') as f:
        print(f"[OK] Loaded SOP from {local_path}")
        return f.read()

# ============================================================================
# TODO 2.3.1 (Step 2): Load the SOP as the system prompt
# Replace the basic prompt below with: SYSTEM_PROMPT = load_system_prompt()
# ============================================================================
SYSTEM_PROMPT = "You are a helpful unicorn rental analytics assistant. Help users query their business data."

# Gateway configuration
GATEWAY_URL = os.getenv("GATEWAY_URL", "")

# Bedrock model configuration
model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-4-6-v1")
region = os.getenv("AWS_REGION", "us-east-1")

# Guardrail configuration (native Bedrock integration — activated in Step 8)
# When GUARDRAIL_ID is set in config.env, the model automatically applies guardrails.
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")

bedrock_model_kwargs = dict(
    model_id=model_id,
    temperature=0.3,
    streaming=True,
)
if GUARDRAIL_ID:
    bedrock_model_kwargs.update(
        guardrail_id=GUARDRAIL_ID,
        guardrail_version=GUARDRAIL_VERSION,
        guardrail_redact_input=True,
        guardrail_redact_input_message="I can only help with unicorn rental analytics. Please ask about bookings, revenue, customers, or unicorn management.",
        guardrail_latest_message=True,
    )
    print(f"[OK] Guardrails enabled: {GUARDRAIL_ID}")

# Bedrock Model (pre-configured)
bedrock_model = BedrockModel(**bedrock_model_kwargs)

# ============================================================================
# Memory Hook — loads conversation history on init, saves each turn
# ============================================================================
MEMORY_ID = os.getenv("MEMORY_ID")

class MemoryHookProvider(HookProvider):
    """Loads recent conversation history and saves new turns to AgentCore Memory (STM)."""

    def __init__(self, memory_client, memory_id):
        self.memory_client = memory_client
        self.memory_id = memory_id

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        registry.add_callback(MessageAddedEvent, self.on_message_added)

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts."""
        try:
            state = event.agent.state or {}
            actor_id = state.get("actor_id") or "default"
            session_id = state.get("session_id") or "default"
            events = self.memory_client.list_events(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                max_results=5
            )
            for ev in events:
                for payload_item in ev.get('payload', []):
                    conv = payload_item.get('conversational', {})
                    role = conv.get('role', '').lower()
                    content = conv.get('content', {}).get('text', '')
                    if content and role in ('user', 'assistant'):
                        event.agent.messages.append({"role": role, "content": [{"text": content}]})
        except Exception as e:
            print(f"[MEMORY] Failed to load history: {e}")

    def on_message_added(self, event: MessageAddedEvent):
        """Save each new message to memory."""
        try:
            msg = event.message
            role = msg.get("role", "")
            text_parts = [c.get("text", "") for c in msg.get("content", []) if "text" in c]
            text = " ".join(text_parts).strip()
            if not text or role not in ("user", "assistant"):
                return
            actor_id = "default"
            session_id = "default"
            if hasattr(event, 'agent') and hasattr(event.agent, 'state'):
                state = event.agent.state or {}
                actor_id = state.get("actor_id") or "default"
                session_id = state.get("session_id") or "default"
            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                messages=[(text, role.upper())]
            )
        except Exception as e:
            print(f"[MEMORY] Failed to save message: {e}")

memory_hooks = []
if MEMORY_ID:
    try:
        memory_client = MemoryClient(region_name=region)
        memory_hooks = [MemoryHookProvider(memory_client, MEMORY_ID)]
        print(f"[OK] Memory enabled: {MEMORY_ID}")
    except Exception as e:
        print(f"⚠️  Memory init failed: {e}")

# Current datetime tool for relative date handling
@tool
def current_datetime() -> str:
    """Get the current date and time in UTC. Use this when users request bookings with relative dates like 'tomorrow', 'next week', etc."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")

# Initialize AgentCore app
app = BedrockAgentCoreApp()

print("AgenticAnalytics AgentCore Gateway Configuration:")
print(f"Gateway URL: {GATEWAY_URL}")
print(f"Using model: {model_id}")
print(f"AWS Region: {region}")
print("[OK] AgenticAnalytics AgentCore ready for requests")

# ============================================================================
# TODO 2.4 (Step 2): Add the @app.entrypoint decorator on the line below
# This tells AgentCore Runtime which function handles requests.
# See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html
# ============================================================================
# @app.entrypoint  # TODO 2.4: Uncomment this line
async def agent_invocation(payload, context):
    """Handler for agent invocation with streaming support"""
    user_message = payload.get("prompt", "No prompt found in input, please provide a prompt")
    # JWT-native inbound auth: the runtime's CustomJWTAuthorizer has already validated
    # the caller's Cognito access token before we run, and passes it through via the
    # runtime's RequestHeaderConfiguration allowlist. We read it from
    # context.request_headers['Authorization'] — this is THE user identity, forwarded to
    # the MCP Gateway for RBAC/RLS. (The UI sends it as a Bearer header, not in the payload.)
    def _bearer_from_headers(ctx):
        headers = getattr(ctx, "request_headers", None) or {} if ctx else {}
        # Header name casing can vary; match case-insensitively.
        auth = headers.get("Authorization") or headers.get("authorization")
        if auth and auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        return auth.strip() if auth else None

    gateway_token = _bearer_from_headers(context)

    print("AgentCore Context:\n-------\n", context)
    print(f"Inbound JWT present: {'Yes' if gateway_token else 'No'}")
    print("Processing Query:\n*******\n", user_message)

    # Pass-through for now; modify here to enrich the prompt (e.g., inject context, tenant info)
    enhanced_prompt = user_message

    try:
        if not gateway_token:
            # Should be unreachable: the runtime's JWT authorizer rejects unauthenticated
            # calls before we run. This guards against a misconfigured request-header
            # allowlist (token validated but not passed through).
            raise ValueError("No Authorization header on the request — check the runtime's "
                             "RequestHeaderConfiguration allowlist includes 'Authorization'")
        access_token = gateway_token
        print(f"[OK] Using validated inbound JWT for gateway auth")
        
        # Extract actor_id from JWT for memory isolation
        import base64 as _b64
        try:
            jwt_payload = access_token.split('.')[1]
            jwt_payload += '=' * (4 - len(jwt_payload) % 4)
            claims = json.loads(_b64.b64decode(jwt_payload))
            actor_id = claims.get("sub", "default")
        except Exception:
            actor_id = "default"
        
        # Use runtime session_id for memory session isolation
        runtime_session_id = context.session_id if context and hasattr(context, 'session_id') else "default"
        print(f"[OK] Memory context: actor={actor_id[:12]}..., session={runtime_session_id[:30]}")
        
        def create_transport():
            return streamablehttp_client(GATEWAY_URL, headers={"Authorization": f"Bearer {access_token}"})
        
        mcp_client = MCPClient(create_transport)
        
        # ====================================================================
        # TODO 2.3.2 (Step 2): Create the Strands Agent
        # Replace None with: Agent(model=bedrock_model, system_prompt=SYSTEM_PROMPT, tools=[mcp_client, current_datetime], hooks=[], callback_handler=None, state={"actor_id": actor_id, "session_id": runtime_session_id})
        #
        # TODO 2.8 (Step 2): Change hooks=[] to hooks=memory_hooks to enable memory.
        # ====================================================================
        request_agent = None  # TODO 2.2.2: Replace with Agent(...)
        
        async for event in request_agent.stream_async(enhanced_prompt):
            yield event
                
    except Exception as e:
        print(f"❌ Request failed: {str(e)}")
        yield {"type": "text", "content": f"I'm currently unable to connect to the scheduling system: {str(e)}. Please try again later."}

app.run()
