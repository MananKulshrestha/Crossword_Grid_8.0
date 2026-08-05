"""FastAPI and Swagger UI delivery layer for the shopper workflow."""

from __future__ import annotations

import os
import unicodedata
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from fkgrid.agentic.contracts import (
    Action,
    ActiveResultBinding,
    CartSnapshot,
    CompatibilityTuple,
    Fact,
    ProductBinding,
    QueryState,
    RecentTurnContext,
    StrictModel,
    TraceHistoryResponse,
    TurnRequest,
    TurnResult,
    UiAction,
)
from fkgrid.speech import SpeechStatus

from .runtime import ApiRuntime


class ModelRuntimeStatus(StrictModel):
    mode: str
    provider: str
    protocol: str
    model_alias: str
    intent_budget_ms: int
    catalog_profile: str
    catalog_size: int
    catalog_seed: int
    catalog_version: str
    cart_mode: str
    cart_ready: bool
    cart_error: str | None = None
    configured: bool
    configuration_error: str | None = None
    speech_configured: bool
    speech_provider: str
    speech_model_alias: str
    speech_error: str | None = None


class HealthResponse(StrictModel):
    status: str
    service: str
    default_model: str


class ReadinessResponse(StrictModel):
    ready: bool
    service: str
    model: ModelRuntimeStatus


class SpeechTranscriptionResponse(StrictModel):
    text: str
    language: str
    model_alias: str
    provider: str
    latency_ms: int
    prompt_id: str
    prompt_version: str


class SessionCreateRequest(StrictModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


class SessionView(StrictModel):
    session_id: str
    state_version: int
    cart_version: int
    query_state: QueryState
    cart: CartSnapshot
    acknowledged_result_set_id: str | None
    acknowledged_entries: list[ActiveResultBinding]
    recent_turns: list[RecentTurnContext]
    compatibility_tuple: CompatibilityTuple
    model: ModelRuntimeStatus


class CatalogItem(StrictModel):
    catalog_position: int
    result_entry_id: str
    binding: ProductBinding
    title: str
    facts: list[Fact]


class CatalogPage(StrictModel):
    profile: str
    seed: int
    catalog_version: str
    total: int
    offset: int
    limit: int
    items: list[CatalogItem]


class CatalogFacets(StrictModel):
    profile: str
    seed: int
    catalog_version: str
    total: int
    facets: dict[str, list[str]]


class ApiUiAction(BaseModel):
    """JSON-friendly API form converted into the strict domain UiAction."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    action: Action
    payload: dict[str, object] = Field(default_factory=dict)
    signed_action_token: str | None = None


class ApiTurnRequest(StrictModel):
    """Swagger-friendly request that fills omitted versions from current state."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        json_schema_extra={
            "examples": [
                {
                    "message": "Find a black cotton t-shirt in size m under 1500 rupees"
                },
                {"message": "hey"},
                {"message": "show me some red T-shirts in size L"},
                {"message": "add 2 to the cart"},
                {"message": "add 2 units of the first result to my cart"},
                {"message": "research the latest cotton-care guidance"},
                {"ui_action": {"action": "SHOW_CART", "payload": {}}},
                {
                    "ui_action": {
                        "action": "PRODUCT_DETAILS",
                        "payload": {
                            "references": [{"kind": "ORDINAL", "value": "1"}]
                        },
                    }
                },
            ]
        },
    )

    client_turn_id: str = Field(
        default_factory=lambda: f"client-{uuid.uuid4().hex}", min_length=1, max_length=128
    )
    idempotency_key: str = Field(
        default_factory=lambda: f"idem-{uuid.uuid4().hex}", min_length=1, max_length=128
    )
    expected_state_version: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Omit for the current session version; set explicitly for optimistic concurrency."
        ),
    )
    expected_cart_version: int | None = Field(
        default=None,
        ge=0,
        description="Omit for the current cart version; set explicitly for optimistic concurrency.",
    )
    locale: Literal["en-IN"] = "en-IN"
    message: str | None = Field(
        default=None,
        max_length=2000,
        description="Free-text shopper request. This uses the configured Gemma 4 26B model.",
        examples=[
            "hey",
            "Find a black cotton t-shirt in size m under 1500 rupees",
            "show me some red T-shirts in size L",
            "Compare the first and second result",
            "Check availability for the first one",
            "add 2 to the cart",
            "add 2 units of the first result to my cart",
            "research the latest cotton-care guidance",
        ],
    )
    ui_action: ApiUiAction | None = Field(
        default=None,
        description="Typed action for model-free controls such as SHOW_CART.",
    )
    ignore_history: bool = False

    @model_validator(mode="after")
    def exactly_one_input(self) -> ApiTurnRequest:
        if (self.message is None) == (self.ui_action is None):
            raise ValueError("exactly one of message or ui_action is required")
        if self.message is not None:
            if not self.message.strip():
                raise ValueError("message must not be empty")
            normalized = unicodedata.normalize("NFKC", self.message).casefold()
            if len(normalized.split()) > 100:
                raise ValueError("message exceeds the 100-token limit")
        return self


def _model_status(runtime: ApiRuntime) -> ModelRuntimeStatus:
    provider = getattr(runtime.gateway, "provider_name", "unknown")
    return ModelRuntimeStatus(
        mode=runtime.model_mode,
        provider=provider,
        protocol=runtime.protocol,
        model_alias=runtime.model_alias,
        intent_budget_ms=runtime.intent_budget_ms,
        catalog_profile=runtime.catalog_profile,
        catalog_size=runtime.catalog_size,
        catalog_seed=runtime.catalog_seed,
        catalog_version=runtime.catalog_version,
        cart_mode=runtime.cart_mode,
        cart_ready=runtime.cart_ready,
        cart_error=runtime.cart_error,
        configured=runtime.model_configured,
        configuration_error=runtime.model_error,
        speech_configured=runtime.speech_configured,
        speech_provider=runtime.speech_provider,
        speech_model_alias=runtime.speech_model_alias,
        speech_error=runtime.speech_error,
    )


def _session_view(runtime: ApiRuntime, session_id: str) -> SessionView:
    snapshot = runtime.snapshot(session_id)
    return SessionView(
        session_id=snapshot.session_id,
        state_version=snapshot.state_version,
        cart_version=snapshot.cart_version,
        query_state=snapshot.state,
        cart=snapshot.cart,
        acknowledged_result_set_id=snapshot.acknowledged_result_set_id,
        acknowledged_entries=snapshot.acknowledged_entries,
        recent_turns=snapshot.recent_turns,
        compatibility_tuple=snapshot.compatibility_tuple,
        model=_model_status(runtime),
    )


def _get_session_or_404(runtime: ApiRuntime, session_id: str):
    try:
        return runtime.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from exc


def _turn_response(result: TurnResult) -> JSONResponse:
    return JSONResponse(status_code=result.http_status, content=jsonable_encoder(result))


def _swagger_chat_html(openapi_url: str) -> HTMLResponse:
    """Return the normal Swagger UI with a small stateful chat harness above it."""

    swagger = get_swagger_ui_html(
        openapi_url=openapi_url,
        title="FK GRiD Shopper Agentic API - Swagger UI",
        swagger_ui_parameters={"persistAuthorization": False},
    )
    html = swagger.body.decode("utf-8")
    chat_markup = """
<style>
  #fkgrid-chat {
    box-sizing: border-box;
    max-width: 1460px;
    margin: 0 auto 18px;
    padding: 18px 20px 16px;
    border: 1px solid #d8dee9;
    border-radius: 10px;
    background: #ffffff;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
    font-family: sans-serif;
  }
  #fkgrid-chat *, #fkgrid-chat *::before, #fkgrid-chat *::after { box-sizing: border-box; }
  #fkgrid-chat-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  #fkgrid-chat h2 { margin: 0; color: #1f2937; font-size: 20px; }
  #fkgrid-chat p { margin: 5px 0 0; color: #5b6472; font-size: 13px; }
  #fkgrid-chat-status { color: #51606f; font-size: 12px; text-align: right; }
  #fkgrid-chat-status[data-state="error"] { color: #b42318; }
  #fkgrid-chat-status[data-state="ready"] { color: #067647; }
  #fkgrid-chat-log {
    display: flex;
    flex-direction: column;
    gap: 9px;
    min-height: 72px;
    max-height: 420px;
    margin: 15px 0 11px;
    padding: 12px;
    overflow-y: auto;
    border: 1px solid #e4e7ec;
    border-radius: 8px;
    background: #f8fafc;
  }
  .fkgrid-chat-bubble {
    max-width: 88%;
    padding: 9px 12px;
    border-radius: 9px;
    white-space: pre-wrap;
    line-height: 1.42;
  }
  .fkgrid-chat-bubble.user { align-self: flex-end; color: #fff; background: #2563eb; }
  .fkgrid-chat-bubble.assistant {
    align-self: flex-start;
    color: #202939;
    background: #fff;
    border: 1px solid #d8dee9;
  }
  .fkgrid-chat-bubble.error { color: #912018; background: #fff4f2; border-color: #fecdca; }
  .fkgrid-chat-results { margin: 8px 0 0; padding-left: 21px; }
  .fkgrid-chat-result { margin: 3px 0; }
  .fkgrid-chat-result[data-cart-eligible="false"] { color: #667085; }
  .fkgrid-chat-result-status { margin-left: 6px; font-size: 11px; color: #667085; }
  .fkgrid-chat-result-status[data-cart-eligible="false"] { color: #b42318; }
  .fkgrid-chat-meta { margin-top: 8px; color: #667085; font-size: 11px; }
  #fkgrid-chat-form { display: flex; gap: 9px; align-items: flex-end; }
  #fkgrid-chat-input {
    flex: 1;
    min-height: 44px;
    max-height: 140px;
    resize: vertical;
    padding: 10px;
    border: 1px solid #98a2b3;
    border-radius: 7px;
    font: inherit;
  }
  #fkgrid-chat-send, #fkgrid-chat-new, #fkgrid-chat-speech {
    border: 0;
    border-radius: 7px;
    cursor: pointer;
    font-weight: 600;
  }
  #fkgrid-chat-send { min-height: 44px; padding: 0 18px; color: #fff; background: #2563eb; }
  #fkgrid-chat-new { padding: 7px 11px; color: #344054; background: #eef2f6; font-size: 12px; }
  #fkgrid-chat-speech { min-height: 44px; padding: 0 14px; color: #344054; background: #eef2f6; }
  #fkgrid-chat-speech[data-recording="true"] { color: #fff; background: #b42318; }
  #fkgrid-chat-send:disabled, #fkgrid-chat-new:disabled,
  #fkgrid-chat-speech:disabled { cursor: wait; opacity: .65; }
  #fkgrid-chat-hint { margin: 7px 0 0; color: #667085; font-size: 11px; }
  #fkgrid-chat-json, .fkgrid-chat-json { margin-top: 7px; }
  #fkgrid-chat-json summary, .fkgrid-chat-json summary { color: #475467; cursor: pointer; font-size: 11px; }
  #fkgrid-chat-json pre, .fkgrid-chat-json pre {
    max-height: 220px;
    overflow: auto;
    margin: 5px 0 0;
    padding: 8px;
    background: #f2f4f7;
    font-size: 11px;
  }
  @media (max-width: 680px) {
    #fkgrid-chat-header, #fkgrid-chat-form { align-items: stretch; flex-direction: column; }
    #fkgrid-chat-status { text-align: left; }
    #fkgrid-chat-send { min-height: 40px; }
  }
</style>
<section id="fkgrid-chat" aria-label="FK GRiD shopper chat">
  <div id="fkgrid-chat-header">
    <div>
      <h2>Chat with the shopper agent</h2>
      <p>Type naturally. This panel keeps one session and sends each turn to the live API.</p>
    </div>
    <div>
      <button id="fkgrid-chat-new" type="button">New chat</button>
      <div id="fkgrid-chat-status" data-state="starting">Starting session...</div>
    </div>
  </div>
  <div id="fkgrid-chat-log" aria-live="polite"></div>
  <form id="fkgrid-chat-form">
    <textarea id="fkgrid-chat-input" maxlength="2000"
      placeholder="Ask about products, comparisons, availability, or your cart..."></textarea>
    <button id="fkgrid-chat-speech" type="button" aria-pressed="false">🎙 Start voice</button>
    <button id="fkgrid-chat-send" type="submit">Send</button>
  </form>
  <div id="fkgrid-chat-hint">Enter sends a message. Use Shift+Enter for a new line.
    Voice recording starts only when you press the microphone button; the
    transcript is inserted for review before sending. Each reply includes its
    execution trace; the full session trace is available at
    <code>/v1/sessions/{session_id}/trace</code>.</div>
</section>
"""
    chat_script = """
<script>
(() => {
  const log = document.getElementById('fkgrid-chat-log');
  const input = document.getElementById('fkgrid-chat-input');
  const form = document.getElementById('fkgrid-chat-form');
  const send = document.getElementById('fkgrid-chat-send');
  const speechButton = document.getElementById('fkgrid-chat-speech');
  const newChat = document.getElementById('fkgrid-chat-new');
  const status = document.getElementById('fkgrid-chat-status');
  let sessionId = null;
  let turnNumber = 0;
  let mediaRecorder = null;
  let mediaStream = null;
  let speechChunks = [];
  let speechConfigured = true;

  const requestId = (prefix) => `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const setStatus = (text, state) => {
    status.textContent = text;
    status.dataset.state = state || 'starting';
  };
  const speechMessages = {
    AUDIO_EMPTY: 'No audio was captured. Try recording again.',
    AUDIO_TOO_LARGE: 'That recording is too large. Try a shorter clip.',
    AUDIO_CONTENT_TYPE_UNSUPPORTED:
      'This browser audio format is not supported. Try again or type your request.',
    TRANSCRIPTION_EMPTY: 'No speech was recognized. Try speaking closer to the microphone.',
    SPEECH_DISABLED: 'Voice input is disabled for this server. Please type your request.',
    FKGRID_SPEECH_API_KEY_REQUIRED:
      'Voice input is not configured for this server. Please type your request.',
    SPEECH_PROVIDER_UNAVAILABLE:
      'Voice transcription is temporarily unavailable. Please try again or type your request.',
    SPEECH_PROVIDER_TIMEOUT:
      'Voice transcription timed out. Please try again or type your request.',
    SPEECH_PROVIDER_HTTP_ERROR:
      'Voice transcription service rejected the audio. Please try again.',
  };
  const speechFailureMessage = (code, httpStatus) => speechMessages[code] || (
    httpStatus === 503
      ? 'Voice transcription is temporarily unavailable. Please try again or type your request.'
      : `Speech request failed (${httpStatus})`
  );
  const setSpeechAvailability = (configured) => {
    speechConfigured = Boolean(configured);
    speechButton.disabled = !speechConfigured;
    speechButton.dataset.configured = speechConfigured ? 'true' : 'false';
    speechButton.title = speechConfigured
      ? 'Record a voice request for review before sending'
      : 'Voice input is unavailable; type your request instead';
    speechButton.textContent = speechConfigured ? '🎙 Start voice' : 'Voice unavailable';
  };
  const addBubble = (kind, text) => {
    const bubble = document.createElement('div');
    bubble.className = `fkgrid-chat-bubble ${kind}`;
    bubble.textContent = text;
    log.appendChild(bubble);
    log.scrollTop = log.scrollHeight;
    return bubble;
  };
  const factValue = (entry, label) => {
    const fact = (entry.facts || []).find((candidate) => candidate.label === label);
    return fact ? fact.typed_value : null;
  };
  const addAssistant = (body, raw) => {
    const response = body.response || {};
    const bubble = addBubble('assistant', response.summary || 'The agent returned no summary.');
    if (Array.isArray(response.search_entries) && response.search_entries.length) {
      const list = document.createElement('ol');
      list.className = 'fkgrid-chat-results';
      response.search_entries.forEach((entry) => {
        const item = document.createElement('li');
        item.className = 'fkgrid-chat-result';
        const availability = factValue(entry, 'availability');
        const cartEligible = availability === 'AVAILABLE';
        item.dataset.cartEligible = cartEligible ? 'true' : 'false';
        item.setAttribute('aria-disabled', cartEligible ? 'false' : 'true');
        item.textContent = entry.title || entry.binding?.product_id || 'Catalog result';
        if (availability !== null) {
          const status = document.createElement('span');
          status.className = 'fkgrid-chat-result-status';
          status.dataset.cartEligible = cartEligible ? 'true' : 'false';
          status.textContent = cartEligible
            ? 'Available for prototype cart'
            : `Cart disabled · ${String(availability).toLowerCase()}`;
          item.appendChild(status);
        }
        list.appendChild(item);
      });
      bubble.appendChild(list);
    }
    if (Array.isArray(response.warnings) && response.warnings.includes('APPROXIMATE_COLOR_MATCH')) {
      const note = document.createElement('div');
      note.className = 'fkgrid-chat-meta';
      note.textContent =
        'The exact color is not in this prototype dataset; closest colors are ranked first.';
      bubble.appendChild(note);
    }
    const meta = document.createElement('div');
    meta.className = 'fkgrid-chat-meta';
    meta.textContent =
      `${response.terminal_state || 'UNKNOWN'} · HTTP ${body.http_status ?? 'n/a'}`;
    bubble.appendChild(meta);
    const trace = raw.trace || {};
    const traceDetails = document.createElement('details');
    traceDetails.className = 'fkgrid-chat-json';
    const traceSummary = document.createElement('summary');
    traceSummary.textContent =
      `View execution trace (${Array.isArray(trace.events) ? trace.events.length : 0} stages)`;
    const tracePre = document.createElement('pre');
    tracePre.textContent = JSON.stringify(trace, null, 2);
    traceDetails.append(traceSummary, tracePre);
    bubble.appendChild(traceDetails);
    const details = document.createElement('details');
    details.id = 'fkgrid-chat-json';
    const summary = document.createElement('summary');
    summary.textContent = 'View API response JSON';
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(raw, null, 2);
    details.append(summary, pre);
    bubble.appendChild(details);
  };
  const createSession = async () => {
    const response = await fetch('/v1/sessions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: '{}'
    });
    if (!response.ok) throw new Error(`Session creation failed (${response.status})`);
    const body = await response.json();
    sessionId = body.session_id;
    turnNumber = 0;
    setSpeechAvailability(body.model?.speech_configured !== false);
    setStatus(`Ready · ${body.model?.model_alias || 'configured model'}`, 'ready');
  };
  const clearMedia = () => {
    if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
    mediaRecorder = null;
    speechChunks = [];
    speechButton.disabled = !speechConfigured;
    speechButton.dataset.recording = 'false';
    speechButton.setAttribute('aria-pressed', 'false');
    speechButton.textContent = speechConfigured ? '🎙 Start voice' : 'Voice unavailable';
  };
  const transcribeRecordedAudio = async (mimeType) => {
    const blob = new Blob(speechChunks, {type: mimeType || 'audio/webm'});
    clearMedia();
    if (!blob.size) {
      setStatus('No audio was captured', 'error');
      return;
    }
    speechButton.disabled = true;
    send.disabled = true;
    setStatus('Transcribing voice…', 'starting');
    try {
      const response = await fetch('/v1/speech/transcriptions', {
        method: 'POST',
        headers: {'Content-Type': mimeType || 'audio/webm'},
        body: blob
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(speechFailureMessage(body.detail, response.status));
      }
      const transcript = (body.text || '').trim();
      if (!transcript) throw new Error('No speech was recognized');
      input.value = input.value.trim() ? `${input.value.trim()} ${transcript}` : transcript;
      setStatus('Voice transcript ready · review and press Send', 'ready');
      input.focus();
    } catch (error) {
      setStatus(error.message || 'Speech transcription failed', 'error');
    } finally {
      speechButton.disabled = !speechConfigured;
      send.disabled = false;
    }
  };
  const stopVoice = () => {
    if (!mediaRecorder || mediaRecorder.state === 'inactive') return;
    speechButton.disabled = true;
    speechButton.textContent = 'Transcribing…';
    mediaRecorder.stop();
  };
  const startVoice = async () => {
    if (!speechConfigured) {
      setStatus('Voice input is not configured for this server. Please type your request.', 'error');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setStatus('This browser does not support microphone recording', 'error');
      return;
    }
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1, echoCancellation: true, noiseSuppression: true,
          autoGainControl: true
        }
      });
      const preferredTypes = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
      const mimeType = preferredTypes.find((type) => MediaRecorder.isTypeSupported(type)) || '';
      mediaRecorder = mimeType
        ? new MediaRecorder(mediaStream, {mimeType})
        : new MediaRecorder(mediaStream);
      speechChunks = [];
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size) speechChunks.push(event.data);
      };
      mediaRecorder.onstop = () => transcribeRecordedAudio(
        mediaRecorder.mimeType || mimeType || 'audio/webm'
      );
      mediaRecorder.start(250);
      speechButton.dataset.recording = 'true';
      speechButton.setAttribute('aria-pressed', 'true');
      speechButton.textContent = '■ Stop & transcribe';
      send.disabled = true;
      newChat.disabled = true;
      setStatus('Listening… press the microphone button to stop', 'starting');
    } catch (error) {
      clearMedia();
      setStatus(error.message || 'Microphone permission was not granted', 'error');
    }
  };
  const resetChat = async () => {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      mediaRecorder.onstop = null;
      mediaRecorder.stop();
    }
    clearMedia();
    newChat.disabled = true;
    send.disabled = true;
    log.replaceChildren();
    sessionId = null;
    setStatus('Starting session...', 'starting');
    try {
      await createSession();
      addBubble('assistant', 'New chat started. What would you like to shop for?');
    } catch (error) {
      setStatus(error.message, 'error');
      addBubble('error', error.message);
    } finally {
      newChat.disabled = false;
      send.disabled = false;
      input.focus();
    }
  };
  const sendMessage = async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || send.disabled) return;
    addBubble('user', message);
    input.value = '';
    send.disabled = true;
    newChat.disabled = true;
    turnNumber += 1;
    const waiting = addBubble('assistant', 'Thinking...');
    try {
      if (!sessionId) await createSession();
      const response = await fetch(`/v1/sessions/${encodeURIComponent(sessionId)}/turns`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          client_turn_id: `chat-turn-${turnNumber}-${requestId('client')}`,
          idempotency_key: `chat-key-${turnNumber}-${requestId('idem')}`,
          message
        })
      });
      const body = await response.json();
      waiting.remove();
      if (!response.ok) {
        addBubble('error', body.detail || `Request failed (${response.status})`);
        setStatus(`Request failed (${response.status})`, 'error');
      } else {
        addAssistant(body, body);
        setStatus(`Ready · ${body.response?.terminal_state || 'completed'}`, 'ready');
      }
    } catch (error) {
      waiting.remove();
      addBubble('error', error.message);
      setStatus('Request failed', 'error');
    } finally {
      send.disabled = false;
      newChat.disabled = false;
      input.focus();
    }
  };
  form.addEventListener('submit', sendMessage);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  newChat.addEventListener('click', resetChat);
  speechButton.addEventListener('click', () => {
    if (mediaRecorder && mediaRecorder.state === 'recording') stopVoice();
    else startVoice();
  });
  resetChat();
})();
</script>
"""
    html = html.replace("<body>", f"<body>{chat_markup}", 1)
    html = html.replace("</body>", f"{chat_script}</body>", 1)
    return HTMLResponse(html)


def create_app(runtime: ApiRuntime | None = None) -> FastAPI:
    """Create the API app; production defaults to the real Gemma adapter."""

    api_runtime = runtime or ApiRuntime.from_environment()
    application = FastAPI(
        title="FK GRiD Shopper Agentic API",
        version="0.1.0",
        docs_url=None,
        description=(
            "Swagger-accessible delivery layer for the existing typed shopper "
            "orchestrator. Free-text turns use the configured Gemma 4 26B "
            "adapter by default; typed actions retain their model-free paths."
        ),
    )
    cors_origins = [
        origin.strip()
        for origin in os.environ.get(
            "FKGRID_CORS_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173",
        ).split(",")
        if origin.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.state.runtime = api_runtime

    @application.get("/docs", include_in_schema=False)
    def swagger_docs() -> HTMLResponse:
        return _swagger_chat_html(application.openapi_url)

    @application.get("/healthz", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="fkgrid-agentic-api",
            default_model=api_runtime.model_alias,
        )

    @application.get("/readyz", response_model=ReadinessResponse, tags=["system"])
    def readiness() -> ReadinessResponse:
        return ReadinessResponse(
            ready=api_runtime.model_configured and api_runtime.cart_ready,
            service="fkgrid-agentic-api",
            model=_model_status(api_runtime),
        )

    @application.get("/v1/model", response_model=ModelRuntimeStatus, tags=["system"])
    def model_status() -> ModelRuntimeStatus:
        """Show safe model configuration metadata; never returns the API key."""

        return _model_status(api_runtime)

    @application.post(
        "/v1/speech/transcriptions",
        response_model=SpeechTranscriptionResponse,
        tags=["speech"],
        summary="Transcribe one explicitly recorded shopper voice clip",
        description=(
            "This opt-in endpoint accepts one raw browser audio blob and sends it "
            "to the configured DeepInfra Whisper large-v3-turbo adapter. It is "
            "not called by normal text turns. The returned text is inserted into "
            "the chat composer for review; it is not automatically submitted."
        ),
    )
    async def transcribe_speech(request: Request) -> SpeechTranscriptionResponse:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if not content_type:
            raise HTTPException(status_code=415, detail="AUDIO_CONTENT_TYPE_REQUIRED")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 20 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="AUDIO_TOO_LARGE")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="AUDIO_CONTENT_LENGTH_INVALID") from exc
        audio = await request.body()
        if not audio:
            raise HTTPException(status_code=422, detail="AUDIO_EMPTY")
        if len(audio) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="AUDIO_TOO_LARGE")
        result = await run_in_threadpool(
            api_runtime.speech_to_text.transcribe,
            audio,
            content_type,
            language=api_runtime.speech_language,
            deadline_ms=api_runtime.speech_deadline_ms,
        )
        if result.status is not SpeechStatus.OK:
            status_codes = {
                SpeechStatus.INVALID_AUDIO: 422,
                SpeechStatus.EMPTY_TRANSCRIPTION: 422,
                SpeechStatus.TIMEOUT: 504,
                SpeechStatus.UNAVAILABLE: 503,
                SpeechStatus.ERROR: 502,
            }
            raise HTTPException(
                status_code=status_codes.get(result.status, 502),
                detail=result.error_code or result.status.value,
            )
        return SpeechTranscriptionResponse(
            text=result.text,
            language=result.language,
            model_alias=result.model_alias,
            provider=result.provider_name,
            latency_ms=result.latency_ms,
            prompt_id=result.prompt_id,
            prompt_version=result.prompt_version,
        )

    @application.get(
        "/v1/catalog/facets",
        response_model=CatalogFacets,
        tags=["testing catalog"],
        summary="List fixture categories and filter values",
    )
    def catalog_facets() -> CatalogFacets:
        return CatalogFacets(
            profile=api_runtime.catalog_profile,
            seed=api_runtime.catalog_seed,
            catalog_version=api_runtime.catalog_version,
            total=api_runtime.catalog_size,
            facets=api_runtime.catalog_facets(),
        )

    @application.get(
        "/v1/catalog",
        response_model=CatalogPage,
        tags=["testing catalog"],
        summary="Browse the synthetic multi-category catalog fixture",
        description=(
            "This endpoint exposes synthetic catalog truth for local testing. "
            "It is deterministic by seed and separate from the live Gemma model."
        ),
    )
    def catalog(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
        category: str | None = Query(default=None, max_length=64),
        brand: str | None = Query(default=None, max_length=64),
    ) -> CatalogPage:
        total, entries = api_runtime.catalog_slice(
            offset=offset,
            limit=limit,
            category=category,
            brand=brand,
        )
        return CatalogPage(
            profile=api_runtime.catalog_profile,
            seed=api_runtime.catalog_seed,
            catalog_version=api_runtime.catalog_version,
            total=total,
            offset=offset,
            limit=limit,
            items=[
                CatalogItem(
                    catalog_position=offset + position,
                    result_entry_id=entry.result_entry_id,
                    binding=entry.binding,
                    title=entry.title,
                    facts=entry.facts,
                )
                for position, entry in enumerate(entries, start=1)
            ],
        )

    @application.post(
        "/v1/sessions",
        response_model=SessionView,
        status_code=201,
        tags=["sessions"],
        summary="Create an in-memory shopper session",
    )
    def create_session(payload: SessionCreateRequest | None = None) -> SessionView:
        try:
            managed = api_runtime.create_session(payload.session_id if payload else None)
        except ValueError as exc:
            code = str(exc)
            if code == "SESSION_EXISTS":
                raise HTTPException(status_code=409, detail=code) from exc
            raise HTTPException(status_code=422, detail=code) from exc
        return _session_view(api_runtime, managed.state.snapshot.session_id)

    @application.get(
        "/v1/sessions/{session_id}",
        response_model=SessionView,
        tags=["sessions"],
        summary="Read the current typed session snapshot",
    )
    def get_session(session_id: str) -> SessionView:
        _get_session_or_404(api_runtime, session_id)
        return _session_view(api_runtime, session_id)

    @application.get(
        "/v1/sessions/{session_id}/trace",
        response_model=TraceHistoryResponse,
        tags=["sessions"],
        summary="Read the structured execution trace for a session",
        description=(
            "Returns the bounded per-turn trace, including stage timings, "
            "validated structured model outputs, safe tool outputs, and the "
            "recent-turn memory projection. Provider errors and raw prompts "
            "are intentionally omitted."
        ),
    )
    def get_trace(session_id: str) -> TraceHistoryResponse:
        managed = _get_session_or_404(api_runtime, session_id)
        with managed.lock:
            traces = [trace.model_copy(deep=True) for trace in managed.trace_history]
        return TraceHistoryResponse(
            session_id=session_id,
            trace_count=len(traces),
            traces=traces,
        )

    @application.post(
        "/v1/sessions/{session_id}/turns",
        response_model=TurnResult,
        tags=["shopper turns"],
        summary="Run one turn through the existing shopper orchestrator",
        description=(
            "Send message for the default Gemma 4 26B intent path, or send a "
            "typed ui_action for controls that intentionally bypass model extraction. "
            "The response status is also returned in the typed http_status field."
        ),
        responses={
            202: {"model": TurnResult},
            409: {"model": TurnResult},
            500: {"model": TurnResult},
        },
    )
    def turn(session_id: str, payload: ApiTurnRequest) -> JSONResponse:
        managed = _get_session_or_404(api_runtime, session_id)
        with managed.lock:
            snapshot = managed.state.snapshot.model_copy(deep=True)
            try:
                request = TurnRequest(
                    session_id=session_id,
                    client_turn_id=payload.client_turn_id,
                    idempotency_key=payload.idempotency_key,
                    expected_state_version=(
                        snapshot.state_version
                        if payload.expected_state_version is None
                        else payload.expected_state_version
                    ),
                    expected_cart_version=(
                        snapshot.cart_version
                        if payload.expected_cart_version is None
                        else payload.expected_cart_version
                    ),
                    locale=payload.locale,
                    message=payload.message,
                    ui_action=(
                        UiAction(
                            action=payload.ui_action.action,
                            payload=payload.ui_action.payload,
                            signed_action_token=payload.ui_action.signed_action_token,
                        )
                        if payload.ui_action is not None
                        else None
                    ),
                    ignore_history=payload.ignore_history,
                )
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors()) from exc
            result = managed.orchestrator.handle(request)
            managed.trace_history.append(result.trace.model_copy(deep=True))
            del managed.trace_history[:-100]
            return _turn_response(result)

    return application


app = create_app()

__all__ = [
    "ApiTurnRequest",
    "CatalogFacets",
    "CatalogPage",
    "SessionCreateRequest",
    "SessionView",
    "app",
    "create_app",
]
