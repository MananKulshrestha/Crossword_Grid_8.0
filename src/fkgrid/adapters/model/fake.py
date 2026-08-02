"""Deterministic model fake for contract tests and local workflow development."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from time import perf_counter

from fkgrid.catalog_language.normalization import normalize_surface_form
from fkgrid.catalog_language.serialization import canonical_json_bytes, sha256_hex
from fkgrid.domain.catalog_language import (
    CriticDraft,
    EvidenceBand,
    ExpansionAction,
    MappingDirection,
    MappingDraft,
    MappingKind,
    ModelCallRequest,
    ModelCallResponse,
    ModelStatus,
    TargetType,
)
from fkgrid.ports.catalog_language import CatalogLanguageModelPort


@dataclass(frozen=True, slots=True)
class QueuedModelResponse:
    status: ModelStatus
    payload: MappingDraft | CriticDraft | None = None
    validation_codes: tuple[str, ...] = ()
    retryable: bool = False


class FakeCatalogLanguageModel(CatalogLanguageModelPort):
    """A tool-less fake; default behavior selects the first supplied target."""

    def __init__(self, model_alias: str = "fake-catalog-language") -> None:
        self.model_alias = model_alias
        self.calls: list[ModelCallRequest] = []
        self._responses: dict[str, deque[QueuedModelResponse]] = defaultdict(deque)

    def queue(self, logical_call: str, response: QueuedModelResponse) -> None:
        self._responses[logical_call].append(response)

    def _complete(
        self, request: ModelCallRequest, default: MappingDraft | CriticDraft
    ) -> ModelCallResponse:
        started = perf_counter()
        self.calls.append(request)
        queued = (
            self._responses[request.logical_call].popleft()
            if self._responses[request.logical_call]
            else None
        )
        response = queued or QueuedModelResponse(status=ModelStatus.OK, payload=default)
        payload = response.payload
        if isinstance(payload, dict):
            model_type = (
                MappingDraft if request.logical_call == "propose_canonical_mapping" else CriticDraft
            )
            payload = model_type.model_validate_json(canonical_json_bytes(payload))
        input_hash = sha256_hex(request.input_payload)
        output_hash = sha256_hex(payload) if payload is not None else None
        return ModelCallResponse(
            call_id=request.call_id,
            status=response.status,
            payload=payload,
            input_hash=input_hash,
            output_hash=output_hash,
            model_alias=self.model_alias,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            validation_codes=list(response.validation_codes),
            retryable=response.retryable,
        )

    def propose_canonical_mapping(self, request: ModelCallRequest) -> ModelCallResponse:
        payload = request.input_payload
        target_ids = request.allowed_target_ids
        if not target_ids:
            default = MappingDraft(decision="ABSTAIN")
        else:
            target = payload.get("allowed_targets", [{}])[0]
            source_form = str(payload["surface_forms"][0])
            default = MappingDraft(
                decision="SELECT",
                source_form=source_form,
                normalized_form=normalize_surface_form(source_form),
                mapping_kind=MappingKind.SYNONYM,
                target_type=TargetType(target["target_type"]),
                target_id=target_ids[0],
                scope=payload["scope"],
                direction=MappingDirection.QUERY_TO_CANONICAL,
                expansion_action=ExpansionAction.CANONICAL_SYNONYM,
                evidence_band=EvidenceBand.MEDIUM,
                interpretation_label=str(target["canonical_name"]),
                evidence_ids=list(payload["evidence_ids"]),
            )
        return self._complete(request, default)

    def critique_mapping(self, request: ModelCallRequest) -> ModelCallResponse:
        default = CriticDraft(
            decision="ACCEPT",
            rationale_code="FAKE_ACCEPTED_BOUNDED_TARGET",
            evidence_ids=list(request.input_payload.get("evidence_ids", [])),
        )
        return self._complete(request, default)
