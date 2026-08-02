from __future__ import annotations

from fkgrid.adapters.catalog_language.prompts import PromptRegistry
from fkgrid.adapters.catalog_language.sql import CatalogLanguageQueries
from fkgrid.catalog_language.normalization import normalize_surface_form, protected_ranges


def test_prompts_are_versioned_and_checksum_verified() -> None:
    registry = PromptRegistry()

    assert "allowed_targets" in registry.read("catalog_language_proposer_v1")
    assert registry.entry("catalog_language_critic_v1")["version"] == "1"


def test_sql_query_catalog_requires_named_parameters_and_uses_no_interpolation() -> None:
    for query in CatalogLanguageQueries.ALL:
        assert ":" in query.sql
        assert "%" not in query.sql
        query.validate_parameters({parameter: "value" for parameter in query.required_parameters})


def test_normalizer_is_conservative_and_protected_spans_are_available() -> None:
    assert normalize_surface_form("Noise‑Cancelling  Headphones") == "noise cancelling headphones"
    assert normalize_surface_form("A14 5G") == "a14 5g"
    ranges = protected_ranges('find "red shoes" not blue shoes')
    assert ranges
