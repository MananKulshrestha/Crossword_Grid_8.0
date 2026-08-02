"""Immutable lexicon artifact writer and compare-and-swap pointer adapter."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fkgrid.catalog_language.serialization import canonical_json_bytes, sha256_bytes, sha256_hex
from fkgrid.domain.catalog_language import (
    ActivationReceipt,
    ActivationRequest,
    LexiconArtifactManifest,
    LexiconCandidateVersion,
    LexiconCompatibility,
    LexiconMapping,
    MappingStatus,
    RegressionReport,
    ShadowReport,
)
from fkgrid.ports.catalog_language import (
    ActivationPort,
    ActiveLexiconPort,
    LexiconArtifactPort,
)


class FilesystemLexiconArtifactStore(LexiconArtifactPort, ActivationPort, ActiveLexiconPort):
    """Write candidate artifacts outside the active pointer, then swap atomically."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.pointer_path = self.root / "active.json"

    @staticmethod
    def _write_fsync(path: Path, content: bytes) -> None:
        with path.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _mapping_bytes(mappings: list[LexiconMapping]) -> bytes:
        return b"".join(canonical_json_bytes(mapping) + b"\n" for mapping in mappings)

    def write_candidate(
        self,
        candidate: LexiconCandidateVersion,
        regression: RegressionReport,
        shadow: ShadowReport,
    ) -> LexiconArtifactManifest:
        if any(mapping.compatibility != candidate.compatibility for mapping in candidate.mappings):
            raise ValueError("candidate contains a mapping from a different compatibility tuple")
        if regression.candidate_version != candidate.candidate_version:
            raise ValueError("regression report belongs to a different candidate")
        if shadow.candidate_version != candidate.candidate_version:
            raise ValueError("shadow report belongs to a different candidate")
        if len({mapping.mapping_id for mapping in candidate.mappings}) != len(candidate.mappings):
            raise ValueError("candidate contains duplicate mapping IDs")
        expected_candidate_checksum = sha256_hex(
            {
                "compatibility": candidate.compatibility,
                "mappings": candidate.mappings,
                "proposal_ids": candidate.proposal_ids,
                "proposed_mapping_ids": candidate.proposed_mapping_ids,
            }
        )
        if expected_candidate_checksum != candidate.candidate_checksum:
            raise ValueError("candidate checksum mismatch")
        final_dir = self.root / candidate.candidate_version
        if final_dir.exists():
            raise FileExistsError(f"artifact already exists: {candidate.candidate_version}")
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{candidate.candidate_version}.", dir=self.root))
        mapping_bytes = self._mapping_bytes(candidate.mappings)
        lookup: dict[str, list[str]] = {}
        for mapping in candidate.mappings:
            lookup.setdefault(f"{mapping.locale}\u0000{mapping.normalized_form}", []).append(
                mapping.mapping_id
            )
        mappings_checksum = sha256_bytes(mapping_bytes)
        regression_bytes = canonical_json_bytes(regression)
        shadow_bytes = canonical_json_bytes(shadow)
        self._write_fsync(temp_dir / "mappings.jsonl", mapping_bytes)
        self._write_fsync(temp_dir / "lookup.json", canonical_json_bytes(lookup))
        self._write_fsync(temp_dir / "regression-report.json", regression_bytes)
        self._write_fsync(temp_dir / "shadow-report.json", shadow_bytes)
        manifest_without_checksum = {
            "candidate_version": candidate.candidate_version,
            "compatibility": candidate.compatibility.model_dump(mode="json"),
            "candidate_checksum": candidate.candidate_checksum,
            "mapping_count": len(candidate.mappings),
            "mappings_checksum": mappings_checksum,
            "regression_report_checksum": sha256_bytes(regression_bytes),
            "shadow_report_checksum": sha256_bytes(shadow_bytes),
            "created_at": candidate.created_at.isoformat().replace("+00:00", "Z"),
        }
        manifest = LexiconArtifactManifest(
            candidate_version=candidate.candidate_version,
            compatibility=candidate.compatibility,
            candidate_checksum=candidate.candidate_checksum,
            mapping_count=len(candidate.mappings),
            mappings_checksum=mappings_checksum,
            regression_report_checksum=sha256_bytes(regression_bytes),
            shadow_report_checksum=sha256_bytes(shadow_bytes),
            created_at=candidate.created_at,
            manifest_checksum=sha256_hex(manifest_without_checksum),
        )
        self._write_fsync(temp_dir / "manifest.json", canonical_json_bytes(manifest))
        os.replace(temp_dir, final_dir)
        return manifest

    def _read_manifest(self, version: str) -> LexiconArtifactManifest:
        manifest_path = self.root / version / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"missing lexicon manifest: {version}")
        manifest = LexiconArtifactManifest.model_validate_json(manifest_path.read_bytes())
        payload = manifest.model_dump(mode="json")
        checksum = payload.pop("manifest_checksum")
        if checksum != sha256_hex(payload):
            raise ValueError("lexicon manifest checksum mismatch")
        mapping_bytes = (self.root / version / "mappings.jsonl").read_bytes()
        if sha256_bytes(mapping_bytes) != manifest.mappings_checksum:
            raise ValueError("lexicon mappings checksum mismatch")
        return manifest

    def _read_mappings(
        self, version: str, compatibility: LexiconCompatibility
    ) -> list[LexiconMapping]:
        manifest = self._read_manifest(version)
        if manifest.compatibility != compatibility:
            raise ValueError("lexicon artifact compatibility mismatch")
        path = self.root / version / "mappings.jsonl"
        mappings = [
            LexiconMapping.model_validate_json(line)
            for line in path.read_bytes().splitlines()
            if line.strip()
        ]
        if len(mappings) != manifest.mapping_count:
            raise ValueError("lexicon artifact mapping count mismatch")
        return mappings

    def activate_lexicon_version(self, request: ActivationRequest) -> ActivationReceipt:
        if not self.pointer_path.exists():
            raise RuntimeError("ACTIVE_LEXICON_POINTER_MISSING")
        current = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        if current["candidate_version"] != request.expected_active_version:
            raise RuntimeError("STALE_ACTIVE_LEXICON_VERSION")
        manifest = self._read_manifest(request.candidate_version)
        mapping_ids = {
            mapping.mapping_id
            for mapping in self._read_mappings(request.candidate_version, manifest.compatibility)
        }
        if not set(request.approved_mapping_ids).issubset(mapping_ids):
            raise ValueError("activation contains an unknown mapping ID")
        if not request.approved_mapping_ids:
            raise ValueError("activation requires at least one mapping")
        activated_at = datetime.now(UTC)
        pointer = {
            "candidate_version": request.candidate_version,
            "manifest_checksum": manifest.manifest_checksum,
            "mapping_ids": sorted(request.approved_mapping_ids),
            "activated_at": activated_at.isoformat(),
            "actor_id": request.actor_id,
        }
        temporary_pointer = self.root / f".active.{request.candidate_version}.tmp"
        self._write_fsync(temporary_pointer, canonical_json_bytes(pointer))
        os.replace(temporary_pointer, self.pointer_path)
        return ActivationReceipt(
            activated=True,
            active_lexicon_version=request.candidate_version,
            activated_mapping_ids=sorted(request.approved_mapping_ids),
            event_id=f"activation-{request.candidate_version}",
            activated_at=activated_at,
        )

    def load_active_mappings(
        self, lexicon_version: str, compatibility: LexiconCompatibility
    ) -> list[LexiconMapping]:
        if not self.pointer_path.exists():
            raise RuntimeError("ACTIVE_LEXICON_POINTER_MISSING")
        pointer = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        if pointer["candidate_version"] != lexicon_version:
            raise ValueError("requested lexicon version is not active")
        mappings = self._read_mappings(lexicon_version, compatibility)
        allowed = set(pointer["mapping_ids"])
        return [
            mapping.model_copy(update={"status": MappingStatus.APPROVED})
            for mapping in mappings
            if mapping.mapping_id in allowed
            and mapping.status in {MappingStatus.APPROVED, MappingStatus.IN_REVIEW}
        ]
