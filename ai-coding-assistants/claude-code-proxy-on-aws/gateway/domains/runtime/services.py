"""Gateway orchestration service."""

from __future__ import annotations

import json
import logging

from gateway.domains.policy.context import PolicyContext
from gateway.domains.policy.engine import HandlerType
from gateway.domains.runtime.circuit_breaker import CircuitBreaker
from gateway.domains.runtime.types import (
    MessageRequest,
    MessageResponse,
    ModelData,
    ModelListResponse,
    UsageInfo,
)
from gateway.domains.usage.metrics import MetricsService
from shared.exceptions import (
    AnthropicError,
    AppError,
    BedrockClientBugError,
    BedrockError,
    BedrockThrottlingError,
    BudgetExceededError,
    ModelNotAllowedError,
    TeamInactiveError,
    UserInactiveError,
)

logger = logging.getLogger(__name__)


class _AnthropicStreamUsage:
    """Accumulate usage from a passthrough Anthropic SSE byte stream."""

    def __init__(self) -> None:
        self._buffer = ""
        self._input_tokens = 0
        self._usage = UsageInfo()

    @staticmethod
    def _coerce(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return 0

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk.decode(errors="ignore")
        while "\n\n" in self._buffer:
            event, self._buffer = self._buffer.split("\n\n", 1)
            for line in event.splitlines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                self._handle_event(data)

    def _handle_event(self, data: dict[str, object]) -> None:
        event_type = data.get("type")
        if event_type == "message_start":
            message = data.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if isinstance(usage, dict):
                self._input_tokens = self._coerce(usage.get("input_tokens"))
        elif event_type == "message_delta":
            usage = data.get("usage")
            if not isinstance(usage, dict):
                return
            delta = data.get("delta")
            stop_reason = delta.get("stop_reason") if isinstance(delta, dict) else None
            self._usage = UsageInfo(
                input_tokens=self._coerce(usage.get("input_tokens")) or self._input_tokens,
                output_tokens=self._coerce(usage.get("output_tokens")),
                cached_read_tokens=self._coerce(usage.get("cache_read_input_tokens")),
                cached_write_tokens=self._coerce(usage.get("cache_creation_input_tokens")),
                stop_reason=stop_reason if isinstance(stop_reason, str) else None,
            )

    def usage(self) -> UsageInfo:
        return self._usage


class GatewayService:
    """Orchestrate runtime requests."""

    def __init__(
        self,
        policy_chain,
        request_converter,
        response_converter,
        bedrock_client,
        stream_processor,
        usage_service,
        session,
        model_catalog_repo,
        metrics: MetricsService | None = None,
        log_full_payloads: bool = False,
        anthropic_client=None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:  # type: ignore[no-untyped-def]
        self._policy_chain = policy_chain
        self._request_converter = request_converter
        self._response_converter = response_converter
        self._bedrock_client = bedrock_client
        self._stream_processor = stream_processor
        self._usage_service = usage_service
        self._session = session
        self._model_catalog_repo = model_catalog_repo
        self._metrics = metrics
        self._log_full_payloads = log_full_payloads
        self._anthropic_client = anthropic_client
        self._circuit_breaker = circuit_breaker

    def _log_bedrock_error(self, context: PolicyContext, error: AppError) -> None:
        level = logging.WARNING if isinstance(error, BedrockThrottlingError) else logging.ERROR
        logger.log(
            level,
            "bedrock runtime request failed request_id=%s "
            "selected_model=%s resolved_model=%s user_id=%s error=%s",
            context.request_id,
            context.selected_model,
            context.resolved_model.bedrock_model_id if context.resolved_model else None,
            str(context.user.id) if context.user else None,
            str(error),
        )

    @staticmethod
    def _response_model_name(context: PolicyContext, fallback_model: str) -> str:
        if context.resolved_model and context.resolved_model.bedrock_model_id:
            return context.resolved_model.bedrock_model_id
        return fallback_model

    @staticmethod
    def _serialize_payload(payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    def _log_request_payload(self, request_id: str, request: MessageRequest) -> None:
        if not self._log_full_payloads:
            return
        logger.info(
            "runtime anthropic request payload request_id=%s payload=%s",
            request_id,
            self._serialize_payload(request.model_dump(exclude_none=True)),
        )

    def _log_bedrock_request_payload(self, request_id: str, payload: dict[str, object]) -> None:
        if not self._log_full_payloads:
            return
        logger.info(
            "runtime bedrock request payload request_id=%s payload=%s",
            request_id,
            self._serialize_payload(payload),
        )

    def _log_bedrock_response_payload(self, request_id: str, payload: dict[str, object]) -> None:
        if not self._log_full_payloads:
            return
        logger.info(
            "runtime bedrock response payload request_id=%s payload=%s",
            request_id,
            self._serialize_payload(payload),
        )

    @staticmethod
    def _extract_usage_from_anthropic(response: dict[str, object]) -> UsageInfo:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return UsageInfo()

        def _coerce(value: object) -> int:
            if isinstance(value, bool):
                return 0
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
            return 0

        stop_reason = response.get("stop_reason")
        return UsageInfo(
            input_tokens=_coerce(usage.get("input_tokens")),
            output_tokens=_coerce(usage.get("output_tokens")),
            cached_read_tokens=_coerce(usage.get("cache_read_input_tokens")),
            cached_write_tokens=_coerce(usage.get("cache_creation_input_tokens")),
            stop_reason=stop_reason if isinstance(stop_reason, str) else None,
        )

    def _can_fallback_to_anthropic(self, context: PolicyContext) -> bool:
        if self._anthropic_client is None:
            return False
        resolved = context.resolved_model
        anthropic_id = getattr(resolved, "anthropic_model_id", None) if resolved else None
        return bool(anthropic_id)

    async def _call_anthropic_fallback(
        self,
        request: MessageRequest,
        context: PolicyContext,
        request_id: str,
        reason: str,
    ) -> MessageResponse:
        logger.warning(
            "falling back to anthropic 1p request_id=%s reason=%s",
            request_id,
            reason,
        )
        anthropic_response = await self._anthropic_client.messages(
            request.model_dump(exclude_none=True),
            context.resolved_model.anthropic_model_id,
        )
        self._log_bedrock_response_payload(request_id, anthropic_response)
        message = MessageResponse.model_validate(anthropic_response)
        usage = self._extract_usage_from_anthropic(anthropic_response)
        await self._usage_service.record_success(context, usage)
        return message

    async def _stream_anthropic_fallback(
        self,
        request: MessageRequest,
        context: PolicyContext,
        request_id: str,
        reason: str,
    ):
        """Yield 1P SSE bytes unchanged while collecting usage off the stream.

        1P emits Anthropic-native SSE, so the bytes pass through to the client
        untouched. A side buffer parses message_start/message_delta events to
        record usage once the stream ends.
        """
        logger.warning(
            "falling back to anthropic 1p stream request_id=%s reason=%s",
            request_id,
            reason,
        )
        chunks = await self._anthropic_client.messages_stream(
            request.model_dump(exclude_none=True),
            context.resolved_model.anthropic_model_id,
        )

        async def _generator():
            collector = _AnthropicStreamUsage()
            try:
                async for chunk in chunks:
                    collector.feed(chunk)
                    yield chunk
            finally:
                try:
                    await self._usage_service.record_success(
                        context, collector.usage()
                    )
                except Exception:
                    logger.exception("Usage persistence failed after 1p stream fallback")
                if self._metrics:
                    self._metrics.emit_active_request_end(context)

        return _generator()

    @staticmethod
    def _inject_bedrock_request_metadata(
        request: dict[str, object], context: PolicyContext
    ) -> dict[str, object]:
        existing = request.get("requestMetadata")
        request_metadata = dict(existing) if isinstance(existing, dict) else {}
        request_metadata["request_id"] = context.request_id
        if context.user is not None and getattr(context.user, "id", None) is not None:
            request_metadata["user_id"] = str(context.user.id)
        if context.team is not None and getattr(context.team, "id", None) is not None:
            request_metadata["team_id"] = str(context.team.id)
        request["requestMetadata"] = request_metadata
        return request

    async def process_message(
        self,
        request: MessageRequest,
        api_key: str,
        request_id: str,
    ) -> MessageResponse:
        context = PolicyContext(api_key=api_key, request_id=request_id, request=request)
        self._log_request_payload(request_id, request)
        if self._metrics:
            self._metrics.emit_active_request_start(context)
        try:
            await self._policy_chain.evaluate(context)
            await self._session.commit()
            resolved = context.resolved_model
            region = getattr(resolved, "bedrock_region", None) if resolved else None
            breaker_open = (
                self._circuit_breaker is not None
                and region is not None
                and not self._circuit_breaker.allow_bedrock(region)
            )
            if breaker_open and self._can_fallback_to_anthropic(context):
                logger.info(
                    "bedrock circuit breaker open, skipping bedrock "
                    "request_id=%s region=%s",
                    request_id,
                    region,
                )
                return await self._call_anthropic_fallback(
                    request, context, request_id, "circuit_open"
                )

            bedrock_request = self._request_converter.convert_request(
                request,
                context.resolved_model,
                context.cache_policy,
                context.max_tokens_override,
            )
            bedrock_request = self._inject_bedrock_request_metadata(bedrock_request, context)
            self._log_bedrock_request_payload(request_id, bedrock_request)
            try:
                response = await self._bedrock_client.converse(
                    bedrock_request, context.resolved_model
                )
            except (BedrockError, BedrockThrottlingError) as bedrock_error:
                if self._circuit_breaker is not None and region is not None:
                    self._circuit_breaker.record_failure(region)
                if not self._can_fallback_to_anthropic(context):
                    raise
                self._log_bedrock_error(context, bedrock_error)
                if isinstance(bedrock_error, BedrockThrottlingError) and self._metrics:
                    self._metrics.emit_throttle(context)
                return await self._call_anthropic_fallback(
                    request, context, request_id, type(bedrock_error).__name__
                )
            if self._circuit_breaker is not None and region is not None:
                self._circuit_breaker.record_success(region)
            self._log_bedrock_response_payload(request_id, response)
            message = self._response_converter.convert_response(
                response,
                self._response_model_name(context, request.model),
            )
            usage = self._response_converter.extract_usage(response)
            await self._usage_service.record_success(context, usage)
            return message
        except AppError as error:
            if isinstance(error, BedrockThrottlingError):
                self._log_bedrock_error(context, error)
                if self._metrics:
                    self._metrics.emit_throttle(context)
            elif isinstance(error, (BedrockError, BedrockClientBugError)):
                self._log_bedrock_error(context, error)
            elif isinstance(error, AnthropicError):
                logger.error(
                    "anthropic 1p fallback failed request_id=%s error=%s",
                    request_id,
                    str(error),
                )
            if self._metrics and isinstance(
                error,
                (BudgetExceededError, ModelNotAllowedError, UserInactiveError, TeamInactiveError),
            ):
                self._metrics.emit_policy_block(context, type(error).__name__)
            if context.resolved_model and context.user and context.virtual_key:
                await self._usage_service.record_blocked_request(context, error)
            raise
        except Exception as error:
            if context.resolved_model and context.user and context.virtual_key:
                await self._usage_service.record_error(context, BedrockError(str(error)))
            raise
        finally:
            if self._metrics:
                self._metrics.emit_active_request_end(context)

    async def process_message_stream(
        self,
        request: MessageRequest,
        api_key: str,
        request_id: str,
    ):
        context = PolicyContext(api_key=api_key, request_id=request_id, request=request)
        self._log_request_payload(request_id, request)
        if self._metrics:
            self._metrics.emit_active_request_start(context)
        try:
            await self._policy_chain.evaluate(context)
            await self._session.commit()
            resolved = context.resolved_model
            region = getattr(resolved, "bedrock_region", None) if resolved else None
            breaker_open = (
                self._circuit_breaker is not None
                and region is not None
                and not self._circuit_breaker.allow_bedrock(region)
            )
            if breaker_open and self._can_fallback_to_anthropic(context):
                logger.info(
                    "bedrock circuit breaker open, skipping bedrock stream "
                    "request_id=%s region=%s",
                    request_id,
                    region,
                )
                return await self._stream_anthropic_fallback(
                    request, context, request_id, "circuit_open"
                )

            bedrock_request = self._request_converter.convert_request(
                request,
                context.resolved_model,
                context.cache_policy,
                context.max_tokens_override,
            )
            bedrock_request = self._inject_bedrock_request_metadata(bedrock_request, context)
            self._log_bedrock_request_payload(request_id, bedrock_request)
            try:
                bedrock_stream = await self._bedrock_client.converse_stream(
                    bedrock_request,
                    context.resolved_model,
                )
            except (BedrockError, BedrockThrottlingError) as bedrock_error:
                if self._circuit_breaker is not None and region is not None:
                    self._circuit_breaker.record_failure(region)
                if not self._can_fallback_to_anthropic(context):
                    raise
                self._log_bedrock_error(context, bedrock_error)
                if isinstance(bedrock_error, BedrockThrottlingError) and self._metrics:
                    self._metrics.emit_throttle(context)
                return await self._stream_anthropic_fallback(
                    request, context, request_id, type(bedrock_error).__name__
                )
            if self._circuit_breaker is not None and region is not None:
                self._circuit_breaker.record_success(region)
            return self._stream_processor.stream_response(
                bedrock_stream,
                context,
                on_done=(
                    lambda: self._metrics.emit_active_request_end(context)
                    if self._metrics
                    else None
                ),
            )
        except AppError as error:
            if isinstance(error, BedrockThrottlingError):
                self._log_bedrock_error(context, error)
                if self._metrics:
                    self._metrics.emit_throttle(context)
            elif isinstance(error, (BedrockError, BedrockClientBugError)):
                self._log_bedrock_error(context, error)
            elif isinstance(error, AnthropicError):
                logger.error(
                    "anthropic 1p stream fallback failed request_id=%s error=%s",
                    request_id,
                    str(error),
                )
            if self._metrics and isinstance(
                error,
                (BudgetExceededError, ModelNotAllowedError, UserInactiveError, TeamInactiveError),
            ):
                self._metrics.emit_policy_block(context, type(error).__name__)
            if self._metrics:
                self._metrics.emit_active_request_end(context)
            if context.resolved_model and context.user and context.virtual_key:
                await self._usage_service.record_blocked_request(context, error)
            raise

    async def list_models(self, api_key: str, request_id: str) -> ModelListResponse:
        request = MessageRequest(
            model="models", max_tokens=1, messages=[{"role": "user", "content": ""}]
        )
        context = PolicyContext(api_key=api_key, request_id=request_id, request=request)
        auth_handlers = self._policy_chain.get_handlers_by_type(
            [
                HandlerType.VIRTUAL_KEY,
                HandlerType.USER_STATUS,
                HandlerType.TEAM_STATUS,
            ]
        )
        for handler in auth_handlers:
            await handler.handle(context)  # type: ignore[attr-defined]
        models = await self._model_catalog_repo.get_active_list()
        return ModelListResponse(
            data=[
                ModelData(
                    id=model.canonical_name,
                    family=model.family,
                    supports_streaming=model.supports_streaming,
                    supports_tools=model.supports_tools,
                    supports_prompt_cache=model.supports_prompt_cache,
                )
                for model in models
            ]
        )
