"""Score extracted attributes and missing-attribute detection independently."""

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional, Set, Tuple

from tqdm import tqdm


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)


def normalize_value(value: Any) -> str:
    """Apply only presentation normalization before comparing slot values."""
    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return re.sub(r"[\s_-]+", " ", text)


def parse_model_response(model_output: dict[str, Any]) -> dict[str, Any]:
    """Return the parsed model response, or an empty mapping for invalid output."""
    parsed = model_output.get("parsed_response")
    if not isinstance(parsed, dict):
        raw_response = model_output.get("raw_response")
        if isinstance(raw_response, str):
            try:
                parsed = json.loads(raw_response)
            except json.JSONDecodeError:
                return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_model_attributes(model_output: dict[str, Any]) -> dict[str, Any]:
    """Return parsed extracted attributes, or an empty mapping for invalid output."""
    attributes = parse_model_response(model_output).get("extracted_attributes")
    return attributes if isinstance(attributes, dict) else {}


def normalized_attributes(attributes: dict[str, Any]) -> set[tuple[str, str]]:
    """Convert an extracted-attributes object into comparable key/value pairs."""
    return {
        (normalize_value(key), normalize_value(value))
        for key, value in attributes.items()
        if value is not None
    }


def normalized_attribute_map(attributes: dict[str, Any]) -> dict[str, str]:
    """Convert an extracted-attributes object into a normalized key -> value map."""
    return {
        normalize_value(key): normalize_value(value)
        for key, value in attributes.items()
        if value is not None
    }


def values_match_loose(expected_value: str, predicted_value: str) -> bool:
    """Treat a value as matching if one is a substring of the other.

    Covers cases like the model dropping a qualifier ("under $80" -> "$80")
    or a unit prefix ("size 10" -> "10") while still naming the right value.
    """
    if not expected_value or not predicted_value:
        return expected_value == predicted_value
    return expected_value in predicted_value or predicted_value in expected_value


def compare_attribute_maps(
    expected_map: dict[str, str], predicted_map: dict[str, str], loose: bool
) -> dict[str, Any]:
    """Score one model's extracted attributes against expected, per attribute key."""
    matches: list[tuple[str, str, str]] = []
    false_positives: list[tuple[str, str]] = []
    false_negatives: list[tuple[str, str]] = []

    for key in sorted(expected_map.keys() | predicted_map.keys()):
        expected_value = expected_map.get(key)
        predicted_value = predicted_map.get(key)
        if expected_value is not None and predicted_value is not None:
            is_match = (
                values_match_loose(expected_value, predicted_value)
                if loose
                else expected_value == predicted_value
            )
            if is_match:
                matches.append((key, expected_value, predicted_value))
            else:
                false_positives.append((key, predicted_value))
                false_negatives.append((key, expected_value))
        elif expected_value is not None:
            false_negatives.append((key, expected_value))
        elif predicted_value is not None:
            false_positives.append((key, predicted_value))

    tp, fp, fn = len(matches), len(false_positives), len(false_negatives)
    precision, recall, f1 = precision_recall_f1(tp, fp, fn)
    return {
        "matched_attributes": [dict(attribute=k, expected=e, predicted=p) for k, e, p in matches],
        "false_positive_attributes": [dict(attribute=k, value=v) for k, v in false_positives],
        "false_negative_attributes": [dict(attribute=k, value=v) for k, v in false_negatives],
        "metrics": {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        },
    }


def missing_attribute_records(
    output: dict[str, Any],
) -> Set[Tuple[str, Optional[int], str]]:
    """Return comparable missing-metadata records, including priority and status."""
    missing_metadata = output.get("missing_metadata", [])
    if not isinstance(missing_metadata, list):
        return set()
    records = set()
    for item in missing_metadata:
        if not isinstance(item, dict) or not isinstance(item.get("attribute"), str):
            continue
        priority = item.get("priority")
        records.add(
            (
                normalize_value(item["attribute"]),
                priority if isinstance(priority, int) else None,
                normalize_value(item.get("status", "")),
            )
        )
    return records


def missing_attribute_names(
    records: Set[Tuple[str, Optional[int], str]],
) -> set[str]:
    """Return field names for the human-readable evaluation report."""
    return {attribute for attribute, _, _ in records}


def precision_recall_f1(
    true_positive: int, false_positive: int, false_negative: int
) -> tuple[float, float, float]:
    """Calculate micro precision, recall, and F1 from comparison counts."""
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def score_sample(sample: dict[str, Any]) -> dict[str, Any]:
    """Score attributes and missing names; follow-up fields are intentionally excluded."""
    expected_output = sample.get("expected_output", {})
    expected_attributes = expected_output.get("extracted_attributes", {}) if isinstance(expected_output, dict) else {}
    raw_model_output = sample.get("model_output", {})
    model_response = parse_model_response(raw_model_output)
    predicted_attributes = parse_model_attributes(raw_model_output)
    expected_map = normalized_attribute_map(expected_attributes if isinstance(expected_attributes, dict) else {})
    predicted_map = normalized_attribute_map(predicted_attributes)

    strict_comparison = compare_attribute_maps(expected_map, predicted_map, loose=False)
    loose_comparison = compare_attribute_maps(expected_map, predicted_map, loose=True)

    expected_missing_records = missing_attribute_records(
        expected_output if isinstance(expected_output, dict) else {}
    )
    predicted_missing_records = missing_attribute_records(model_response)
    detected_missing_records = expected_missing_records & predicted_missing_records
    missed_missing_records = expected_missing_records - predicted_missing_records
    extra_missing_records = predicted_missing_records - expected_missing_records

    expected_missing = missing_attribute_names(expected_missing_records)
    predicted_missing = missing_attribute_names(predicted_missing_records)
    detected_missing = missing_attribute_names(detected_missing_records)
    missed_missing = missing_attribute_names(missed_missing_records)
    extra_missing = missing_attribute_names(extra_missing_records)

    missing_tp = len(detected_missing_records)
    missing_fp = len(extra_missing_records)
    missing_fn = len(missed_missing_records)

    missing_precision, missing_recall, missing_f1 = precision_recall_f1(
        missing_tp, missing_fp, missing_fn
    )

    return {
        "input_query": sample.get("input_query"),
        "expected_extracted_attributes": expected_attributes,
        "model_extracted_attributes": predicted_attributes,
        "matched_attributes": strict_comparison["matched_attributes"],
        "false_positive_attributes": strict_comparison["false_positive_attributes"],
        "false_negative_attributes": strict_comparison["false_negative_attributes"],
        "matched_attributes_loose": loose_comparison["matched_attributes"],
        "false_positive_attributes_loose": loose_comparison["false_positive_attributes"],
        "false_negative_attributes_loose": loose_comparison["false_negative_attributes"],
        "missing_metadata_detection": {
            "expected_attributes": sorted(expected_missing),
            "predicted_attributes": sorted(predicted_missing),
            "detected_attributes": sorted(detected_missing),
            "missed_attributes": sorted(missed_missing),
            "extra_attributes": sorted(extra_missing),
            "detected_count": len(detected_missing),
            "expected_count": len(expected_missing),
            "exact_set_match": predicted_missing_records == expected_missing_records,
        },
        "attribute_metrics": strict_comparison["metrics"],
        "attribute_metrics_loose": loose_comparison["metrics"],
        "missing_attribute_metrics": {
            "true_positive": missing_tp,
            "false_positive": missing_fp,
            "false_negative": missing_fn,
            "precision": round(missing_precision, 4),
            "recall": round(missing_recall, 4),
            "f1": round(missing_f1, 4),
        },
    }


def append_json_line(handle: Any, content: dict[str, Any]) -> None:
    """Persist every completed sample immediately for live inspection."""
    handle.write(json.dumps(content, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def write_json(filepath: Path, content: Any) -> None:
    """Atomically write the final ordered per-model report."""
    temporary_path = filepath.with_suffix(filepath.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        json.dump(content, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary_path.replace(filepath)


def display_model_name(result_path: Path) -> str:
    return result_path.name.removesuffix("_results.json").replace("_", ":")


def run_evaluation() -> None:
    base_dir = Path(__file__).resolve().parent
    result_files = sorted((base_dir / "results").glob("*_results.json"))
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    if not result_files:
        print("No evaluation result files found in the results/ folder.")
        return

    summary: list[dict[str, Any]] = []
    print("Starting deterministic attribute and missing-attribute evaluation\n")

    for result_path in tqdm(result_files, desc="Models", unit="model"):
        model_name = display_model_name(result_path)
        with result_path.open(encoding="utf-8") as handle:
            samples = json.load(handle)
        if not isinstance(samples, list):
            raise ValueError(f"Expected a JSON list in {result_path}")

        print(f"Scoring {model_name} ({len(samples)} samples)")
        live_log_path = logs_dir / f"{sanitize_filename(model_name)}_attribute_verdicts.jsonl"
        completed_logs: list[dict[str, Any]] = []
        attribute_totals = {"true_positive": 0, "false_positive": 0, "false_negative": 0}
        attribute_totals_loose = {"true_positive": 0, "false_positive": 0, "false_negative": 0}
        missing_totals = {"true_positive": 0, "false_positive": 0, "false_negative": 0}
        missing_exact_set_matches = 0

        with live_log_path.open("w", encoding="utf-8") as live_log:
            for sample_index, sample in enumerate(
                tqdm(samples, desc=f"Scoring {model_name}", unit="sample", leave=False)
            ):
                result = score_sample(sample)
                completed_logs.append(result)
                append_json_line(live_log, {"sample_index": sample_index, **result})
                for metric_name in attribute_totals:
                    attribute_totals[metric_name] += result["attribute_metrics"][metric_name]
                    attribute_totals_loose[metric_name] += result["attribute_metrics_loose"][metric_name]
                    missing_totals[metric_name] += result["missing_attribute_metrics"][metric_name]
                missing_detection = result["missing_metadata_detection"]
                missing_exact_set_matches += int(missing_detection["exact_set_match"])

        attribute_precision, attribute_recall, attribute_f1 = precision_recall_f1(**attribute_totals)
        attribute_precision_loose, attribute_recall_loose, attribute_f1_loose = precision_recall_f1(
            **attribute_totals_loose
        )
        missing_precision, missing_recall, missing_f1 = precision_recall_f1(**missing_totals)
        summary.append(
            {
                "model_name": model_name,
                "attribute_precision": attribute_precision,
                "attribute_recall": attribute_recall,
                "attribute_f1": attribute_f1,
                "attribute_precision_loose": attribute_precision_loose,
                "attribute_recall_loose": attribute_recall_loose,
                "attribute_f1_loose": attribute_f1_loose,
                "missing_precision": missing_precision,
                "missing_recall": missing_recall,
                "missing_f1": missing_f1,
                "missing_exact_set_accuracy": missing_exact_set_matches / len(samples) if samples else 0.0,
            }
        )

        report_path = logs_dir / f"{sanitize_filename(model_name)}_evaluation_report.json"
        write_json(report_path, completed_logs)
        print(
            f"Live log: {live_log_path.relative_to(base_dir)} | "
            f"Attribute F1 (strict): {attribute_f1:.2%} | Attribute F1 (loose): {attribute_f1_loose:.2%} | "
            f"Missing F1: {missing_f1:.2%}\n"
        )

    print("=" * 175)
    print("ATTRIBUTE EXTRACTION (STRICT = exact value match, LOOSE = substring match) AND MISSING-ATTRIBUTE DETECTION")
    print("=" * 175)
    print(
        f"{'Model Name':<30} | {'Strict P/R/F1':>20} | {'Loose P/R/F1':>20} | {'Missing P/R/F1':>20} | "
        f"{'Missing exact-set':>17}"
    )
    print("-" * 175)
    for item in summary:
        print(
            f"{item['model_name']:<30} | "
            f"{item['attribute_precision']:>6.2%}/{item['attribute_recall']:>6.2%}/{item['attribute_f1']:>6.2%} | "
            f"{item['attribute_precision_loose']:>6.2%}/{item['attribute_recall_loose']:>6.2%}/{item['attribute_f1_loose']:>6.2%} | "
            f"{item['missing_precision']:>6.2%}/{item['missing_recall']:>6.2%}/{item['missing_f1']:>6.2%} | "
            f"{item['missing_exact_set_accuracy']:>16.2%}"
        )
    print("=" * 175)


if __name__ == "__main__":
    run_evaluation()
