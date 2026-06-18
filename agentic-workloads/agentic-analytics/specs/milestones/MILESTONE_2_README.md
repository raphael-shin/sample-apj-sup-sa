# Milestone 2 — Pipeline Bridge: Run the Voice Bot

End-to-end test: speak into a mic → Deepgram STT → AgentCore analytics agent →
Deepgram TTS → hear the answer aloud.

## Prerequisites

- Milestone 1 complete (token spike verified)
- `uv` installed
- A Daily API key (get one from https://pipecat.daily.co/your-org/settings/keys)
- A Deepgram API key

## Setup

```bash
cd server
cp .env.example .env   # if you haven't already
# Fill in all blanks in .env — minimum required vars listed below
uv sync
```

Minimum `.env` for this milestone (all vars required):

```
AWS_REGION=us-west-2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
DAILY_API_KEY=...
DEEPGRAM_API_KEY=...
COGNITO_CLIENT_ID=5uqdjp5fvp3k6qgvn184m028ij
DEMO_USERNAME=orion.moonshadow@example-mythicalunicorns.com
DEMO_PASSWORD=...
AWS_AGENT_ARN=arn:aws:bedrock-agentcore:us-west-2:074065412773:runtime/agentic_analytics_agent-FUc4rIClvp
```

## Run the bot

```bash
cd server
uv run bot.py --transport daily
```

The bot creates a Daily room and prints the join URL:

```
INFO  - Daily room URL: https://yourorg.daily.co/XXXXXXXX
```

Open that URL in a browser to join. Or use the React client from the hackathon starter:

```bash
cd resources/aws-deepgram-sa-hackathon/client
cp env.example .env.local
# set VITE_API_URL to http://localhost:7860 (default bot port)
npm install && npm run dev
```

## Smoke-test

1. Join the Daily room (browser or React client)
2. Speak: **"What unicorns are available this weekend?"**
3. Expected: bot speaks a 2–3 sentence summary of available unicorns

Check the bot terminal for per-stage metrics — look for `stt`, `llm` (AgentCore), and `tts`
timings in the `enable_metrics` output.

### What success looks like

Terminal output:
```
INFO  - Client connected
INFO  - Metrics: stt=...ms  llm=...ms  tts=...ms
```

Bot speaks something like:
> "You have 43 unicorns available this weekend. Highlights include the Tejat Posterior
>  Sugilite at $299/hour with teleportation and weather control, and the Vega Sapphire
>  at $375/hour. Would you like to filter by capacity, price, or specific abilities?"

## Design notes

### No custom subclass or deployed-agent changes needed

`AWSAgentCoreProcessor` accepts two injectable transformer callables:

- **`context_to_payload_transformer`** — builds the JSON payload sent to AgentCore.
  Our custom version appends `gateway_token` (a fresh Cognito AccessToken) to the
  default `{"prompt": "..."}` on every turn.

- **`response_to_output_transformer`** — extracts spoken text from each SSE line.
  Default expects `{"response": "..."}`. Our agent emits `{"text": "..."}` (Strands
  native shape). Our custom transformer reads that key; all other Strands event types
  (tool calls, metadata, lifecycle markers) return `None` and are silently skipped.

### No initial greeting

Unlike the hackathon starter, the bot does not queue an `LLMRunFrame` on
`on_client_ready`. There is no local LLM prompt to send, and triggering an AgentCore
call with no user input adds unnecessary latency. The bot waits for the user to speak.

### Token freshness

`get_gateway_token()` is called synchronously on every agent turn (inside the async
transformer). Cognito AccessTokens are valid for 1 hour so the overhead is negligible
for the demo. Token caching with a TTL can be added in Milestone 4 if needed.

## Troubleshooting

**Bot speaks an error message ("unable to connect to the scheduling system")**
→ Check that `DEMO_PASSWORD` is correct and the AccessToken is being fetched (not IdToken).
  See `MILESTONE_1_README.md` for the AccessToken/IdToken note.

**Bot is silent after STT transcribes**
→ Check AgentCore ARN and AWS credentials. Run the M1 smoke-test to confirm the invoke works.

**TTS speaks raw JSON or markdown tables**
→ The current SOP is text-UI-oriented. Milestone 3 adds a voice-variant SOP that suppresses
  markdown and tables.
