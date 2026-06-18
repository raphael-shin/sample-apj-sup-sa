# Deployment

Everything needed to deploy lives in the repo: the agent (`app/agentcore_strands/`),
the React UI (`app/ui/`), the voice bot (`app/voice/`), CFN + scripts
(`infrastructure/`), and data (`dataset/`, `common/`).

## Two layers

1. **Backend** (the analytics agent + dashboard) — always deployed.
2. **Voice** (the Pipecat pipeline) — **optional**, chosen by a CFN parameter.

## Deploy experience

### Step 1 — package artifacts to S3 (local; needs aws-cli, npm, zip, pip3 — NOT Docker)
```bash
cd infrastructure/scripts
aws s3 mb s3://<your-artifacts-bucket> --region us-west-2
./package_and_upload.sh <your-artifacts-bucket>
```
This builds the UI (from `app/ui/`), zips the agent code (`app/agentcore_strands/`)
and the voice bot context (`app/voice/`, hash-versioned), uploads templates +
Lambdas + data, and prints the exact `create-stack` command with all the resolved
S3 keys.

### Step 2 — create the stack (CloudFormation builds the rest IN-CLOUD)
Run the printed command. Docker images are built by **CodeBuild inside the deploy**
(agent image always; voice bot image when voice is on) — no local Docker.

**Voice is a parameter.** The printed command deploys backend-only by default
(`EnableVoice=false`). To deploy **with voice**, add:

```
ParameterKey=EnableVoice,ParameterValue=true
ParameterKey=VoiceMode,ParameterValue=agentcore        # or pipecat-cloud
ParameterKey=DeepgramApiKey,ParameterValue=<key>
ParameterKey=DeepgramVoiceId,ParameterValue=aura-2-apollo-en
```

No demo credentials: hosted voice forwards each signed-in user's own Cognito token,
so RBAC/RLS is per-user (identical to the text path). No Daily key for agentcore mode
(WebRTC media relays via Amazon KVS managed TURN — no 3rd-party SFU).

## The voice modes

| Mode | Where the Pipecat pipeline runs | How it's deployed |
|------|----------------------------------|-------------------|
| **laptop** | your laptop + UI on `localhost:3001` | `infrastructure/scripts/deploy_voice.sh laptop` (local dev; not a CFN value) |
| **agentcore** | its OWN AgentCore Runtime (WebRTC + KVS TURN) in your AWS account | **Fully CFN**: `EnableVoice=true VoiceMode=agentcore` → main stack deploys `voice-agentcore-stack.yaml` (CodeBuild builds the ARM64 image; the Pipecat pipeline runs as a second AgentCore Runtime in VPC mode reusing the Aurora private subnets + NAT; a tiny API-Gateway+Cognito-JWT+Lambda signaling proxy translates the browser's SDP offer/ICE to the runtime and unwraps its SSE answer). One deploy. Fast iteration: `deploy_backend.sh --voice-only`. |
| **pipecat-cloud** | Daily's Pipecat Cloud (SaaS) | **CFN + post-deploy script**: deploy the main stack with `EnableVoice=true VoiceMode=pipecat-cloud` (this leaves the UI's voice URL empty — main CFN does NOT deploy PCC infra), then run `infrastructure/scripts/deploy_voice_pcc.sh`. |

### Why agentcore is one-step but pipecat-cloud needs a script
Pipecat Cloud is **external SaaS** — its agent + secret set can't be modeled in
CloudFormation (no AWS resource types for them), so `infrastructure/scripts/deploy_voice_pcc.sh`
finishes the job after the main CFN deploy. It:
1. creates the **PCC secret set** (Deepgram/Daily keys on Pipecat's side — CLI-only);
2. deploys the **PCC agent** (`infrastructure/voice-pcc-cr/`, drives PCC's REST API);
3. deploys the **JWT start proxy** (`infrastructure/voice-proxy/`) — needed because the
   browser can't call PCC's `/start` directly (the PCC key must stay server-side);
4. fills the proxy's **Secrets Manager placeholder** with the PCC key (the key
   never touches a CFN template/param);
5. points the UI's `VOICE_START_URL` at the proxy and **redeploys the UI** so the
   Voice button appears.

Required env for the script: `PCC_PAT`, `PCC_PUBLIC_KEY`, `DEEPGRAM_API_KEY`,
`DAILY_API_KEY`.

(agentcore needs no SaaS finisher: the pipeline runs as a second AgentCore Runtime
in your account, and the in-stack signaling proxy is plain CFN — one deploy.)

When voice is off, the UI's `VOICE_SIGNALING_URL` is empty → the **Voice button is
hidden** (the dashboard is pure text chat).

## Voice on/off is genuinely off

`app/ui/src/services/voiceClient.js` has **no localhost fallback** — `VOICE_SIGNALING_URL`
is set only when voice is deployed (CFN injects it into `config.js`, or
`app/ui/.env.local` sets it for laptop dev). Absent → `voiceConfigured()` is false.

## Operational notes

- **CodeBuild builds all images in-cloud** (mirrors how Vincent's demo mode builds
  the agent image). Custom-resource Lambdas trigger the builds and block dependent
  resources until images are in ECR. ECR repos use `EmptyOnDelete: true` so
  `delete-stack` is clean.
- **agentcore voice runs as a second AgentCore Runtime in VPC mode**
  (`voice-agentcore-stack.yaml`), reusing the **Aurora VPC's private subnets + NAT**
  (VPC NetworkMode is required for UDP TURN; the runtime ENIs reach
  Deepgram/Bedrock/KVS/STS via the existing NAT — no new VPC or second NAT). WebRTC
  media relays through **Amazon Kinesis Video Streams (KVS) managed TURN** (a signaling
  channel, ~$0.03/mo; TURN creds are fetched agent-side so the browser needs no AWS
  creds). No Daily, no ALB, no VPC Link.
- **Signaling proxy** (in the same stack): API Gateway HTTP API + Cognito JWT
  authorizer + a stdlib-only Lambda. It is authenticated at the edge (NOT a public
  Function URL): the browser POSTs its SDP offer (PATCHes ICE) with its Cognito Bearer
  token; the Lambda forwards that same Bearer to the JWT-only voice runtime
  `/invocations` and unwraps the SSE answer into the JSON the SmallWebRTC transport
  expects. Media never flows through it — signaling only.
- **Cost:** agentcore voice is microVM-per-session (no always-on task/ALB); it
  cold-starts ~5-10s on the first connection. pipecat-cloud scales to zero
  (`min_agents=0`) but also cold-starts ~10s.
