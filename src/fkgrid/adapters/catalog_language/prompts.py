"""Versioned prompt registry with checksum verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class PromptRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).with_name("prompts")
        self.manifest: dict[str, dict[str, Any]] = json.loads(
            (self.root / "manifest.json").read_text(encoding="utf-8")
        )

    def read(self, prompt_id: str) -> str:
        entry = self.manifest[prompt_id]
        content = (self.root / entry["file"]).read_text(encoding="utf-8")
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        expected = entry["file_sha256"]
        if expected == "PENDING" or checksum != expected:
            raise ValueError(f"prompt checksum mismatch for {prompt_id}")
        return content

    def entry(self, prompt_id: str) -> dict[str, Any]:
        return dict(self.manifest[prompt_id])
