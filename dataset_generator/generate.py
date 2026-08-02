import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List


class DatasetGenerator:
    MAX_MISSING_ATTRIBUTES = 3

    def __init__(self, taxonomy_path: str, priorities_path: str, templates_path: str):
        with open(taxonomy_path, "r", encoding="utf-8") as handle:
            self.taxonomy = json.load(handle)
        with open(priorities_path, "r", encoding="utf-8") as handle:
            self.priorities = json.load(handle)
        with open(templates_path, "r", encoding="utf-8") as handle:
            self.templates = json.load(handle)
        self.rng = random.Random(42)
        self.scored_attributes = {
            attribute
            for attributes in self.priorities["priority_mapping"].values()
            for attribute in attributes
        }

    def _get_priority(self, attr_name: str) -> int:
        for priority, attrs in self.priorities["priority_mapping"].items():
            if attr_name in attrs:
                return int(priority)
        return 5

    def _is_scored_attribute(self, attr_name: str) -> bool:
        """Return whether an attribute belongs to the global benchmark schema."""
        return attr_name in self.scored_attributes

    def generate_sample(self, template_item: dict) -> dict:
        cat_key = template_item["category_key"]
        cat_info = self.taxonomy[cat_key]
        cat_attrs = cat_info["attributes"]

        template_str: str = template_item["template"]
        implicit_attrs = template_item.get("implicit_attributes", {})

        extracted_attributes: Dict[str, Any] = {}
        tokens_to_replace: Dict[str, str] = {}

        if "{category}" in template_str:
            tokens_to_replace["{category}"] = cat_info["canonical_category"]
            extracted_attributes["category"] = cat_info["canonical_category"]

        for attr, details in cat_attrs.items():
            slot_key = f"{{{attr}}}"
            if slot_key in template_str:
                selected_val = self.rng.choice(details["values"])
                tokens_to_replace[slot_key] = selected_val
                if self._is_scored_attribute(attr):
                    extracted_attributes[attr] = selected_val

        for key, value in implicit_attrs.items():
            slot_key = f"{{{key}}}"
            if slot_key in template_str:
                tokens_to_replace[slot_key] = str(value)
            if self._is_scored_attribute(key):
                extracted_attributes[key] = value

        formatted_query = template_str
        for slot, value in tokens_to_replace.items():
            formatted_query = formatted_query.replace(slot, value)

        missing_metadata = []
        for attr, details in cat_attrs.items():
            if self._is_scored_attribute(attr) and attr not in extracted_attributes:
                missing_metadata.append(
                    {
                        "attribute": attr,
                        "priority": self._get_priority(attr),
                        "status": "missing",
                    }
                )

        missing_metadata.sort(key=lambda item: item["priority"])
        missing_metadata = missing_metadata[: self.MAX_MISSING_ATTRIBUTES]

        if missing_metadata:
            target_priority = missing_metadata[0]["priority"]
            followup_question = (
                f"Ask for the missing attribute with priority {target_priority}."
            )
        else:
            target_priority = None
            followup_question = None

        return {
            "input_query": formatted_query,
            "expected_output": {
                "extracted_attributes": extracted_attributes,
                "missing_metadata": missing_metadata,
                "primary_followup": {
                    "target_priority": target_priority,
                    "question": followup_question,
                },
            },
        }

    def build_dataset(self, num_samples: int) -> List[dict]:
        dataset = []
        for _ in range(num_samples):
            template_item = self.rng.choice(self.templates)
            dataset.append(self.generate_sample(template_item))
        return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic benchmark samples")
    parser.add_argument("--num-samples", type=int, default=100, help="Number of samples to generate")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    generator = DatasetGenerator(
        taxonomy_path=str(base_dir / "data" / "taxonomy.json"),
        priorities_path=str(base_dir / "data" / "priorities.json"),
        templates_path=str(base_dir / "data" / "templates.json"),
    )

    dataset = generator.build_dataset(num_samples=args.num_samples)
    output_path = base_dir / "output" / "dataset_eval.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2)

    print(f"Successfully generated {len(dataset)} evaluation samples in {output_path}")


if __name__ == "__main__":
    main()
