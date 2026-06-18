/**
 * voiceClient — thin wrapper over the Pipecat JS client for the analytics
 * dashboard's Voice Mode (presenter). Connects to the voice AgentCore Runtime
 * over WebRTC (SmallWebRTC transport + KVS managed TURN) and surfaces the events
 * ChatPanel needs through plain callbacks.
 *
 * Voice is additive: this module is only loaded/used when the user turns Voice
 * Mode ON. Text chat (awsAgentCore.invokeAgent) is unaffected.
 *
 * Signaling: the browser POSTs its SDP offer (and PATCHes trickled ICE) to a tiny
 * JWT-gated signaling proxy ({VOICE_SIGNALING_URL}/api/offer). The proxy validates
 * the Cognito token at the API-Gateway edge, forwards it as a Bearer to the
 * JWT-only voice runtime /invocations, and unwraps the runtime's SSE answer into
 * the plain JSON SDP answer the SmallWebRTC transport expects. Media then flows
 * browser ↔ KVS TURN ↔ runtime. (The runtime can't be hit directly from the
 * browser because it speaks a wrapped/SSE signaling contract the stock transport
 * doesn't understand.)
 *
 * Verified against @pipecat-ai/client-js exports (PipecatClient, RTVIEvent) and
 * @pipecat-ai/small-webrtc-transport SmallWebRTCTransport (webrtcRequestParams
 * APIRequest with endpoint+headers+requestData).
 */
import { PipecatClient, RTVIEvent } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';
import { fetchAccessToken } from './authService';
import { getRuntimeSessionId } from './awsAgentCore';

// A single hidden <audio> element that plays the bot's audio track. Without
// this, the bot generates speech and sends it over WebRTC but nothing in the
// page renders it, so the user hears nothing (the bare Playground does this for
// you; our custom integration must do it explicitly).
let _botAudioEl = null;
function getBotAudioEl() {
  if (_botAudioEl) return _botAudioEl;
  const el = document.createElement('audio');
  el.id = 'pipecat-bot-audio';
  el.autoplay = true;
  el.style.display = 'none';
  document.body.appendChild(el);
  _botAudioEl = el;
  return el;
}
function attachBotAudio(track) {
  const el = getBotAudioEl();
  const stream = new MediaStream([track]);
  el.srcObject = stream;
  const p = el.play();
  if (p && p.catch) p.catch(() => { /* autoplay gated; user gesture already happened on toggle */ });
}
function detachBotAudio() {
  if (_botAudioEl) {
    _botAudioEl.srcObject = null;
  }
}

// Runtime config: window.__APP_CONFIG__ (Amplify-injected) or REACT_APP_* (local).
// VOICE_SIGNALING_URL is the base URL of the JWT-gated WebRTC signaling proxy; it
// is set ONLY when voice is deployed (CFN EnableVoice=true VoiceMode=agentcore
// injects it into config.js, or .env.local sets it for laptop dev). When absent,
// voiceConfigured() is false and the UI hides the Voice button entirely.
const RC = (typeof window !== 'undefined' && window.__APP_CONFIG__) || {};
export const VOICE_SIGNALING_URL =
  RC.VOICE_SIGNALING_URL ||
  process.env.REACT_APP_VOICE_SIGNALING_URL ||
  '';

export function voiceConfigured() {
  return !!VOICE_SIGNALING_URL;
}

/**
 * Create and connect a voice session.
 *
 * @param {object} opts
 * @param {(text:string)=>void} opts.onUserTranscript  final user speech → text
 * @param {(text:string)=>void} opts.onBotSpoken       bot's spoken narrative (TTS text)
 * @param {(markdown:string)=>void} opts.onDisplay      displayed-track markdown (RTVI display-text)
 * @param {(data:object)=>void} opts.onServerMessage    any other RTVI server message (chart, panel, sql-approval)
 * @param {()=>void} opts.onReady                       bot ready (connected end-to-end)
 * @param {(e:any)=>void} opts.onError
 * @param {()=>void} opts.onDisconnected
 * @returns {Promise<PipecatClient>}
 */
export async function startVoiceSession(opts) {
  const {
    onUserTranscript,
    onBotSpoken,
    onDisplay,
    onServerMessage,
    onReady,
    onError,
    onDisconnected,
    sessionId,          // app session id, shared with the text chat
  } = opts;

  const client = new PipecatClient({
    transport: new SmallWebRTCTransport(),
    enableMic: true,
    enableCam: false,
  });

  // Fire onReady exactly once, from whichever signal arrives first. We do NOT
  // rely solely on RTVIEvent.BotReady: on a hosted (Pipecat Cloud) cold start it
  // can be slow or, if the RTVI handshake hiccups, never arrive — which left the
  // UI stuck on "enabling voice…" forever. The bot's audio track starting is an
  // equally good "we're connected" signal. A watchdog (below) bounds the wait.
  let _readyFired = false;
  const fireReady = () => {
    if (_readyFired) return;
    _readyFired = true;
    if (readyWatchdog) { clearTimeout(readyWatchdog); readyWatchdog = null; }
    if (onReady) onReady();
  };
  let readyWatchdog = null;

  // Bot's spoken text (mirror of TTS) → chat as the spoken-echo bubble.
  client.on(RTVIEvent.BotTtsText, (data) => {
    const text = data?.text ?? data;
    if (text && onBotSpoken) onBotSpoken(text);
  });

  // User speech transcript. TranscriptData = { text, final }. userTranscript
  // fires for BOTH partials and finals — only surface FINALs (final === true),
  // so interim/self-corrected partials don't each become a separate bubble.
  client.on(RTVIEvent.UserTranscript, (data) => {
    if (!data || data.final !== true) return;
    const text = data.text;
    if (text && onUserTranscript) onUserTranscript(text);
  });

  // Server messages: our bot pushes {type:'display-text', markdown} and may push
  // {type:'chart'|'panel'|'sql-approval', ...}. Route display-text specially.
  client.on(RTVIEvent.ServerMessage, (msg) => {
    const data = msg?.data ?? msg;
    if (!data) return;
    if (data.type === 'display-text' && onDisplay) {
      onDisplay(data.markdown || '');
      return;
    }
    if (onServerMessage) onServerMessage(data);
  });

  // Play the bot's audio track. TrackStarted fires with (track, participant);
  // the bot is the non-local participant. Attach its audio track so it's heard.
  client.on(RTVIEvent.TrackStarted, (track, participant) => {
    if (track && track.kind === 'audio' && (!participant || !participant.local)) {
      attachBotAudio(track);
      // Bot audio is flowing → we're connected. Clears the "enabling…" state even
      // if BotReady is delayed/missed.
      fireReady();
    }
  });

  // "Thinking" window: user stopped talking → agent is working. Cleared when the
  // real answer arrives (onDisplay in ChatPanel) or on error/disconnect. (The
  // spoken instant filler is OFF by default — see VOICE_SPOKEN_FILLER in
  // analytics_processor.py — so first bot speech is normally the real answer; the
  // grace window in ChatPanel's onBotSpoken is a harmless backstop for when the
  // filler is explicitly enabled on an AEC-capable transport.)
  const { onThinking } = opts;
  client.on(RTVIEvent.UserStoppedSpeaking, () => onThinking && onThinking(true));

  client.on(RTVIEvent.BotReady, () => fireReady());
  client.on(RTVIEvent.Error, (e) => onError && onError(e));
  client.on(RTVIEvent.Disconnected, () => { detachBotAudio(); if (onDisconnected) onDisconnected(); });

  // Signaling: the SmallWebRTC transport POSTs its SDP offer (and PATCHes trickled
  // ICE candidates) to {VOICE_SIGNALING_URL}/api/offer. We attach the user's Cognito
  // access token as a Bearer header (the proxy's API-Gateway JWT authorizer validates
  // it, then forwards it to the runtime so RBAC/RLS apply to the REAL user) and the
  // shared runtimeSessionId header (so voice + text share one AgentCore Memory thread).
  // The token also rides in requestData as a backstop. Refresh the token right before
  // connect so a long-idle tab doesn't open with an expired one.
  let userToken = null;
  try {
    userToken = await fetchAccessToken();
  } catch (e) {
    // hosted proxy will reject with 401 if the token is absent/expired
  }
  const headers = new Headers();
  if (userToken) headers.set('Authorization', `Bearer ${userToken}`);
  const sharedSessionId = sessionId ? getRuntimeSessionId(sessionId) : '';
  if (sharedSessionId) {
    headers.set('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', sharedSessionId);
  }

  // Watchdog: if neither BotReady nor bot audio arrives within the bound, stop
  // hanging on "enabling voice…" — tear down and report an error the UI can show.
  // 45s comfortably covers an AgentCore microVM cold start (~5-10s) + WebRTC setup.
  readyWatchdog = setTimeout(() => {
    if (_readyFired) return;
    _readyFired = true;
    try { client.disconnect(); } catch (e) { /* best effort */ }
    if (onError) onError(new Error('Voice timed out while connecting. Please try again.'));
  }, 45000);

  // webrtcRequestParams (APIRequest): the transport sends {sdp,type,...} to this
  // endpoint with these headers; requestData is merged into the body so the proxy
  // also sees the shared session id. The proxy unwraps the runtime's SSE answer to
  // the plain JSON SDP answer the transport expects.
  try {
    await client.connect({
      webrtcRequestParams: {
        endpoint: `${VOICE_SIGNALING_URL}/api/offer`,
        headers,
        requestData: sharedSessionId ? { runtimeSessionId: sharedSessionId } : undefined,
      },
    });
  } catch (e) {
    if (readyWatchdog) { clearTimeout(readyWatchdog); readyWatchdog = null; }
    throw e;
  }

  return client;
}

export async function stopVoiceSession(client) {
  detachBotAudio();
  if (!client) return;
  try {
    await client.disconnect();
  } catch (e) {
    // best-effort teardown
    // eslint-disable-next-line no-console
    console.warn('voice disconnect error', e);
  }
}
