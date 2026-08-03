"""Testing runtime wiring for the FastAPI adapter.

The repository currently contains the shopper orchestrator and deterministic
ports, but not the database/catalog owners. This module wires those existing
ports into an in-memory runtime so the complete typed workflow can be exercised
from Swagger UI without creating a second business-policy implementation.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fkgrid.agentic.contracts import (
    CompatibilityTuple,
    ModelRequest,
    ModelResponse,
    ModelStatus,
    QueryState,
    SearchEntry,
    TurnSnapshot,
    ValidationIssue,
)
from fkgrid.agentic.fakes import (
    DeterministicEnhancer,
    FakeClock,
    FakeMarkdownPipeline,
    FakeRecoveryPort,
    FakeReferenceResolver,
    FakeResearchPort,
    FakeSuggestionPort,
    FakeTraceSink,
    InMemorySessionState,
    SequentialIds,
    empty_cart,
)
from fkgrid.agentic.gateway import (
    DEFAULT_DEEPINFRA_ENDPOINT,
    FakeModelGateway,
    Gemma4ModelAdapter,
    StructuredModelGateway,
)
from fkgrid.agentic.orchestrator import OrchestratorConfig, TurnOrchestrator
from fkgrid.agentic.validation import canonical_hash
from fkgrid.api.catalog import (
    DEFAULT_CATALOG_SEED,
    DEFAULT_CATALOG_SIZE,
    FIXTURE_PROFILE,
    MAX_CATALOG_SIZE,
    MIN_CATALOG_SIZE,
    FixtureCartPort,
    FixtureCatalogPort,
    build_catalog_entries,
    fixture_facets,
)
from fkgrid.speech import (
    DEFAULT_SPEECH_MODEL_ALIAS,
    DeepInfraWhisperAdapter,
    SpeechToTextPort,
    UnavailableSpeechToText,
)

DEFAULT_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
DEFAULT_DEEPINFRA_MODEL_ALIAS = "google/gemma-4-26b-a4b-it"
DEFAULT_SPEECH_DEADLINE_MS = 8_000
DEFAULT_SPEECH_LANGUAGE = "en"


def demo_compatibility(model_alias: str) -> CompatibilityTuple:
    """Return the compatibility tuple for the current synthetic fixture."""

    return CompatibilityTuple(
        contract_schema_version="agentic-contracts-v1",
        catalog_version="catalog-fixture-v1",
        index_version="index-fixture-v1",
        taxonomy_version="taxonomy-fixture-v1",
        category_schema_version="schema-fixture-v1",
        lexicon_version="lexicon-fixture-v1",
        rank_policy_version="rank-fixture-v1",
        gate_policy_version="gate-fixture-v1",
        intent_prompt_version="3",
        intent_model_alias=model_alias,
        response_template_version="response-fixture-v1",
        commerce_policy_version="commerce-fixture-v1",
        research_policy_version="research-fixture-v1",
        suggestion_policy_version="suggestions-fixture-v1",
        memory_schema_version="memory-fixture-v1",
        query_enhancement_policy_version="enhancement-fixture-v1",
        clarification_prompt_version="2",
        recovery_prompt_version="2",
        research_prompt_version="2",
        research_model_alias=model_alias,
        suggestion_prompt_version="2",
    )


def demo_entries(compatibility: CompatibilityTuple) -> list[SearchEntry]:
    """Backward-compatible name for the richer fixture builder."""

    return build_catalog_entries(compatibility)


class UnavailableModelGateway:
    """Safe non-provider state used only when live configuration is absent.

    This is intentionally not a fake model: it never produces an interpretation.
    The orchestrator therefore returns its existing safe interpretation fallback,
    while typed/model-free actions such as ``SHOW_CART`` remain usable.
    """

    provider_name = "unconfigured"

    def __init__(self, model_alias: str, reason_code: str) -> None:
        self.model_alias = model_alias
        self.reason_code = reason_code

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            call_id=request.call_id,
            status=ModelStatus.UNAVAILABLE,
            input_hash=canonical_hash(request.input_payload),
            provider_name=self.provider_name,
            model_alias=request.model_alias,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
            latency_ms=0,
            validation_issues=[
                ValidationIssue(
                    code=self.reason_code,
                    path="model",
                    safe_message="The live Gemma model is not configured for this process.",
                )
            ],
            retryable=False,
        )


@dataclass
class ManagedSession:
    """One isolated in-memory orchestrator and state port for a test session."""

    orchestrator: TurnOrchestrator
    state: InMemorySessionState
    lock: threading.RLock


class ApiRuntime:
    """Own the API's session registry and live model configuration."""

    def __init__(
        self,
        *,
        gateway: StructuredModelGateway,
        model_mode: str,
        model_alias: str,
        protocol: str,
        intent_budget_ms: int = 1800,
        catalog_seed: int = DEFAULT_CATALOG_SEED,
        catalog_size: int = DEFAULT_CATALOG_SIZE,
        model_error: str | None = None,
        catalog_entries: Sequence[SearchEntry] | None = None,
        speech_to_text: SpeechToTextPort | None = None,
        speech_deadline_ms: int = DEFAULT_SPEECH_DEADLINE_MS,
        speech_language: str = DEFAULT_SPEECH_LANGUAGE,
        speech_error: str | None = None,
    ) -> None:
        self.gateway = gateway
        self.model_mode = model_mode
        self.model_alias = model_alias
        self.protocol = protocol
        self.intent_budget_ms = intent_budget_ms
        self.catalog_seed = catalog_seed
        self.catalog_size = len(catalog_entries) if catalog_entries is not None else catalog_size
        self.catalog_profile = FIXTURE_PROFILE
        self.model_error = model_error
        self.speech_to_text = speech_to_text or UnavailableSpeechToText(
            speech_error or "FKGRID_SPEECH_API_KEY_REQUIRED"
        )
        self.speech_deadline_ms = speech_deadline_ms
        self.speech_language = speech_language
        self.speech_error = speech_error or getattr(self.speech_to_text, "reason_code", None)
        self.speech_configured = (
            speech_to_text is not None
            and not isinstance(self.speech_to_text, UnavailableSpeechToText)
            and self.speech_error is None
        )
        self.speech_provider = getattr(self.speech_to_text, "provider_name", "unconfigured")
        self.speech_model_alias = getattr(
            self.speech_to_text, "model_alias", DEFAULT_SPEECH_MODEL_ALIAS
        )
        self.catalog_entries = list(
            catalog_entries
            if catalog_entries is not None
            else build_catalog_entries(
                demo_compatibility(model_alias), catalog_size, catalog_seed
            )
        )
        self.catalog_version = (
            self.catalog_entries[0].binding.catalog_version
            if self.catalog_entries
            else "catalog-fixture-v1"
        )
        self._sessions: dict[str, ManagedSession] = {}
        self._sessions_lock = threading.RLock()

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> ApiRuntime:
        """Build the default runtime, preferring the real Gemma 4 26B path.

        ``FKGRID_MODEL_MODE=fake`` is an explicit test/development override. It
        is never selected implicitly when a key or endpoint is missing.
        """

        values = dict(os.environ if environment is None else environment)
        mode = values.get("FKGRID_MODEL_MODE", "live").casefold()
        if mode not in {"live", "fake"}:
            raise ValueError("FKGRID_MODEL_MODE_INVALID")

        try:
            intent_budget_ms = int(values.get("FKGRID_INTENT_BUDGET_MS", "1800"))
        except ValueError as exc:
            raise ValueError("FKGRID_INTENT_BUDGET_MS_INVALID") from exc
        if intent_budget_ms <= 0:
            raise ValueError("FKGRID_INTENT_BUDGET_MS_INVALID")
        try:
            catalog_size = int(values.get("FKGRID_TEST_CATALOG_SIZE", str(DEFAULT_CATALOG_SIZE)))
            catalog_seed = int(values.get("FKGRID_TEST_CATALOG_SEED", str(DEFAULT_CATALOG_SEED)))
        except ValueError as exc:
            raise ValueError("FKGRID_TEST_CATALOG_CONFIG_INVALID") from exc
        if not MIN_CATALOG_SIZE <= catalog_size <= MAX_CATALOG_SIZE:
            raise ValueError("FKGRID_TEST_CATALOG_SIZE_OUT_OF_RANGE")
        speech_mode = values.get("FKGRID_SPEECH_MODE", "live").casefold()
        if speech_mode not in {"live", "disabled"}:
            raise ValueError("FKGRID_SPEECH_MODE_INVALID")
        try:
            speech_deadline_ms = int(
                values.get("FKGRID_SPEECH_DEADLINE_MS", str(DEFAULT_SPEECH_DEADLINE_MS))
            )
        except ValueError as exc:
            raise ValueError("FKGRID_SPEECH_DEADLINE_MS_INVALID") from exc
        if speech_deadline_ms <= 0:
            raise ValueError("FKGRID_SPEECH_DEADLINE_MS_INVALID")
        speech_language = values.get("FKGRID_SPEECH_LANGUAGE", DEFAULT_SPEECH_LANGUAGE)
        speech_model_alias = values.get("FKGRID_SPEECH_MODEL_ALIAS", DEFAULT_SPEECH_MODEL_ALIAS)
        if speech_mode == "disabled":
            speech_to_text: SpeechToTextPort = UnavailableSpeechToText(
                "SPEECH_DISABLED", speech_model_alias
            )
            speech_error = "SPEECH_DISABLED"
        else:
            try:
                speech_to_text = DeepInfraWhisperAdapter.from_environment(environment=values)
                speech_error = None
            except ValueError as exc:
                # Keep application startup and the text-chat path available when
                # optional speech credentials are absent or malformed.
                speech_to_text = UnavailableSpeechToText(str(exc), speech_model_alias)
                speech_error = str(exc)
        protocol = values.get("FKGRID_MODEL_PROTOCOL")
        if not protocol:
            if values.get("DEEPINFRA_API_KEY") or values.get("FKGRID_MODEL_API_KEY"):
                protocol = "deepinfra"
            elif values.get("GEMINI_API_KEY"):
                protocol = "gemini"
            else:
                protocol = "deepinfra"
        protocol = protocol.casefold()
        default_alias = (
            DEFAULT_DEEPINFRA_MODEL_ALIAS
            if protocol == "deepinfra"
            else Gemma4ModelAdapter.default_model_alias
        )
        alias = values.get("FKGRID_MODEL_ALIAS", default_alias)

        if mode == "fake":
            return cls(
                gateway=FakeModelGateway(),
                model_mode="fake",
                model_alias=alias,
                protocol=protocol,
                intent_budget_ms=intent_budget_ms,
                catalog_seed=catalog_seed,
                catalog_size=catalog_size,
                speech_to_text=speech_to_text,
                speech_deadline_ms=speech_deadline_ms,
                speech_language=speech_language,
                speech_error=speech_error,
            )

        provider_values = dict(values)
        provider_values["FKGRID_MODEL_PROTOCOL"] = protocol
        provider_values["FKGRID_MODEL_ALIAS"] = alias
        if protocol == "gemini":
            provider_values.setdefault("FKGRID_MODEL_ENDPOINT", DEFAULT_GEMINI_ENDPOINT)
        elif protocol == "deepinfra":
            provider_values.setdefault("FKGRID_MODEL_ENDPOINT", DEFAULT_DEEPINFRA_ENDPOINT)

        try:
            gateway = Gemma4ModelAdapter.from_environment(environment=provider_values)
        except ValueError as exc:
            reason_code = str(exc)
            return cls(
                gateway=UnavailableModelGateway(alias, reason_code),
                model_mode="unavailable",
                model_alias=alias,
                protocol=protocol,
                intent_budget_ms=intent_budget_ms,
                catalog_seed=catalog_seed,
                catalog_size=catalog_size,
                model_error=reason_code,
                speech_to_text=speech_to_text,
                speech_deadline_ms=speech_deadline_ms,
                speech_language=speech_language,
                speech_error=speech_error,
            )
        return cls(
            gateway=gateway,
            model_mode="live",
            model_alias=gateway.model_alias,
            protocol=protocol,
            intent_budget_ms=intent_budget_ms,
            catalog_seed=catalog_seed,
            catalog_size=catalog_size,
            speech_to_text=speech_to_text,
            speech_deadline_ms=speech_deadline_ms,
            speech_language=speech_language,
            speech_error=speech_error,
        )

    @property
    def model_configured(self) -> bool:
        return self.model_mode == "live"

    def create_session(self, session_id: str | None = None) -> ManagedSession:
        session_id = session_id or f"session_{uuid.uuid4().hex}"
        if session_id.strip() != session_id:
            raise ValueError("SESSION_ID_WHITESPACE")
        compatibility = demo_compatibility(self.model_alias)
        state = QueryState(
            state_version=0,
            catalog_version=compatibility.catalog_version,
            index_version=compatibility.index_version,
            lexicon_version=compatibility.lexicon_version,
            compact_goal_summary="prototype shirts",
        )
        cart = empty_cart(session_id, compatibility)
        snapshot = TurnSnapshot(
            session_id=session_id,
            state=state,
            state_version=0,
            cart_version=0,
            cart=cart,
            acknowledged_result_set_id=None,
            acknowledged_entries=[],
            compatibility_tuple=compatibility,
        )
        session_state = InMemorySessionState(snapshot)
        clock = FakeClock()
        ids = SequentialIds()
        markdown = FakeMarkdownPipeline()
        catalog = FixtureCatalogPort(self.catalog_entries)
        orchestrator = TurnOrchestrator(
            state=session_state,
            enhancer=DeterministicEnhancer(clock, ids),
            gateway=self.gateway,
            catalog=catalog,
            recovery=FakeRecoveryPort(),
            references=FakeReferenceResolver(),
            cart=FixtureCartPort(cart, catalog),
            research=FakeResearchPort(clock),
            suggestions=FakeSuggestionPort(),
            markdown=markdown,
            clock=clock,
            ids=ids,
            trace_sink=FakeTraceSink(),
            config=OrchestratorConfig(
                model_alias=self.model_alias,
                intent_budget_ms=self.intent_budget_ms,
            ),
        )
        managed = ManagedSession(
            orchestrator=orchestrator,
            state=session_state,
            lock=threading.RLock(),
        )
        with self._sessions_lock:
            if session_id in self._sessions:
                raise ValueError("SESSION_EXISTS")
            self._sessions[session_id] = managed
        return managed

    def get_session(self, session_id: str) -> ManagedSession:
        with self._sessions_lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise KeyError("SESSION_NOT_FOUND") from exc

    def snapshot(self, session_id: str) -> TurnSnapshot:
        managed = self.get_session(session_id)
        with managed.lock:
            return managed.state.snapshot.model_copy(deep=True)

    def catalog(self) -> list[SearchEntry]:
        return [entry.model_copy(deep=True) for entry in self.catalog_entries]

    def catalog_slice(
        self,
        *,
        offset: int,
        limit: int,
        category: str | None = None,
        brand: str | None = None,
    ) -> tuple[int, list[SearchEntry]]:
        entries = self.catalog_entries
        if category:
            entries = [
                entry
                for entry in entries
                if any(
                    fact.label == "category"
                    and str(fact.typed_value).casefold() == category.casefold()
                    for fact in entry.facts
                )
            ]
        if brand:
            entries = [
                entry
                for entry in entries
                if any(
                    fact.label == "brand"
                    and str(fact.typed_value).casefold() == brand.casefold()
                    for fact in entry.facts
                )
            ]
        return len(entries), [
            entry.model_copy(deep=True) for entry in entries[offset : offset + limit]
        ]

    def catalog_facets(self) -> dict[str, list[str]]:
        return fixture_facets(self.catalog_entries)
