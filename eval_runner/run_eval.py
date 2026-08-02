import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from tqdm import tqdm

OLLAMA_URL = "http://127.0.0.1:11435/api/chat"
NUM_MODELS = 4
NUM_WORKERS_PER_MODEL = 6


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)


def load_file(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8") as handle:
        return handle.read()


def write_results_file(results: list, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, out_path)


def query_ollama(model: str, system_prompt: str, user_query: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.0,
        },
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=300)
        response.raise_for_status()
        raw_content = response.json()["message"]["content"]
        cleaned_content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()

        try:
            parsed_json = json.loads(cleaned_content)
        except json.JSONDecodeError:
            parsed_json = None

        return {
            "status": "success",
            "raw_response": cleaned_content,
            "parsed_response": parsed_json,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error_message": str(exc),
            "parsed_response": None,
        }


def process_sample(sample: dict, model: str, system_prompt: str) -> dict:
    user_query = sample["input_query"]
    result = query_ollama(model, system_prompt, user_query)

    return {
        "input_query": user_query,
        "expected_output": sample["expected_output"],
        "model_output": result,
    }


def run_model_benchmark(model: str, dataset: list, system_prompt: str, base_dir: Path) -> None:
    out_path = base_dir / "results" / f"{sanitize_filename(model)}_results.json"
    results: list = []
    completed = 0

    print(f"🟢 Starting model [{model}] with {NUM_WORKERS_PER_MODEL} workers")

    with ThreadPoolExecutor(max_workers=NUM_WORKERS_PER_MODEL) as executor:
        futures = [executor.submit(process_sample, sample, model, system_prompt) for sample in dataset]
        for future in tqdm(as_completed(futures), total=len(dataset), desc=f"Evaluating {model}", leave=False):
            result = future.result()
            results.append(result)
            completed += 1
            write_results_file(results, out_path)
            print(f"💾 {model}: wrote response {completed}/{len(dataset)} to {out_path}")

    print(f"✅ Finished {model}: saved {len(results)} responses to {out_path}\n")


def run_benchmark() -> None:
    base_dir = Path(__file__).resolve().parent
    os.makedirs(base_dir / "results", exist_ok=True)

    system_prompt = load_file(base_dir / "system_prompt.md")
    with open(base_dir / "models.json", "r", encoding="utf-8") as handle:
        models = json.load(handle)

    dataset_path = base_dir.parent / "dataset_generator" / "output" / "dataset_eval.json"
    with open(dataset_path, "r", encoding="utf-8") as handle:
        dataset = json.load(handle)

    print(f"🚀 Loaded {len(dataset)} dataset samples across {len(models)} models.\n")

    if NUM_MODELS < 1:
        raise ValueError("NUM_MODELS must be at least 1")

    for batch_start in range(0, len(models), NUM_MODELS):
        model_batch = models[batch_start : batch_start + NUM_MODELS]
        batch_number = batch_start // NUM_MODELS + 1
        print(f"📦 Starting batch {batch_number}: {', '.join(model_batch)}")

        with ThreadPoolExecutor(max_workers=len(model_batch)) as model_pool:
            futures = [
                model_pool.submit(run_model_benchmark, model, dataset, system_prompt, base_dir)
                for model in model_batch
            ]
            for future in as_completed(futures):
                future.result()


if __name__ == "__main__":
    run_benchmark()
