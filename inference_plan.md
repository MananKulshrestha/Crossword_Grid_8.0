Here are the updated instructions for your coding agent. It includes the explicit Ollama configuration parameters to disable thinking/reasoning outputs (such as DeepSeek-R1's `<think>` tags) and force direct JSON output.

---

### 📋 Updated Instructions for Coding Agent

```markdown
### SYSTEM INSTRUCTION: Ollama Multi-Model Inference Benchmark Pipeline (No Reasoning / Direct JSON)

You are tasked with building a robust, parallelized inference execution script (`run_eval.py`) that reads test queries from a dataset, sends them to specified Ollama models using your system prompt, and saves the structured responses.

---

### 1. FILE STRUCTURE & ENVIRONMENT

Ensure the workspace has the following layout:

```

eval_runner/
├── system_prompt.md     # Contains the system prompt for slot extraction
├── models.json          # List of Ollama model tags to test
├── dataset_eval.json    # Evaluation dataset (user-provided)
├── run_eval.py          # Main inference runner script
├── requirements.txt     # Requirements file
└── results/             # Output folder for raw model predictions

```

#### `models.json` Format
```json
[
  "gemma2:latest",
  "qwen2.5:latest",
  "deepseek-r1:14b"
]

```

#### `requirements.txt`

```text
requests
tqdm

```

---

### 2. CORE REQUIREMENTS FOR `run_eval.py`

1. **Ollama Endpoint Configuration:**
* Server base URL: `http://127.0.0.1:11435`
* Use the `/api/chat` Ollama REST endpoint.
* Set `"format": "json"` in the API request payload to enforce valid JSON schema responses.


2. **Disable Thinking & Reasoning:**
* Explicitly disable chain-of-thought/thinking tags (critical for models like DeepSeek-R1, Qwen 2.5 Thinker, or hybrid models) by adding `"thinking": False` inside the API request options or top-level payload.
* Inject a system directive requesting the model to skip internal thinking/reasoning steps and directly produce the output.


3. **System Prompt Integration:**
* Read system instructions dynamically from `system_prompt.md`.
* Inject `system_prompt.md` into the `system` role of the Ollama chat completion API call.


4. **Parallel Execution (per model):**
* For **each model sequentially**:
* Load all dataset items from `dataset_eval.json`.
* Process samples in parallel with **4 concurrent worker threads** using `concurrent.futures.ThreadPoolExecutor(max_workers=4)`.
* Display an active, real-time progress bar using `tqdm`.




5. **Saving Results:**
* Save completed inferences inside the `results/` folder as `<model_name_sanitized>_results.json`.
* Example filename for `qwen2.5:latest` -> `results/qwen2_5_latest_results.json`.



---

### 3. SCRIPT BLUEPRINT (`run_eval.py`)

Implement `run_eval.py` according to this implementation logic:

```python
import os
import json
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

OLLAMA_URL = "[http://127.0.0.1:11435/api/chat](http://127.0.0.1:11435/api/chat)"
MAX_WORKERS = 4


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)


def load_file(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def query_ollama(model: str, system_prompt: str, user_query: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0,  # Zero temperature for deterministic evaluation
            "thinking": False,   # Disables thinking mode in supported Ollama builds/models
        },
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        response.raise_for_status()
        raw_content = response.json()["message"]["content"]

        # Strip out <think> tags if a model still injects them
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
    except Exception as e:
        return {"status": "error", "error_message": str(e), "parsed_response": None}


def process_sample(sample: dict, model: str, system_prompt: str) -> dict:
    user_query = sample["input_query"]
    result = query_ollama(model, system_prompt, user_query)

    return {
        "input_query": user_query,
        "expected_output": sample["expected_output"],
        "model_output": result,
    }


def run_benchmark():
    os.makedirs("results", exist_ok=True)

    system_prompt = load_file("system_prompt.md")
    with open("models.json", "r") as f:
        models = json.load(f)

    with open("dataset_eval.json", "r") as f:
        dataset = json.load(f)

    print(f"🚀 Loaded {len(dataset)} dataset samples across {len(models)} models.\n")

    for model in models:
        print(f"🟢 Running inference for model: [{model}] (Parallel Workers: {MAX_WORKERS})")
        results = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [
                executor.submit(process_sample, sample, model, system_prompt)
                for sample in dataset
            ]

            for future in tqdm(
                as_completed(futures),
                total=len(dataset),
                desc=f"Evaluating {model}",
            ):
                results.append(future.result())

        out_filename = f"results/{sanitize_filename(model)}_results.json"
        with open(out_filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        print(f"✅ Saved results for {model} to {out_filename}\n")


if __name__ == "__main__":
    run_benchmark()

```

---

### 4. ACCEPTANCE CRITERIA

* Disables thinking/reasoning steps via API settings and cleans out residual `<think>` blocks.
* Connects to Ollama on port `11435` with 4 parallel worker threads.
* Iterates over models defined in `models.json` sequentially while processing query samples in parallel.
* Generates structured JSON benchmarks in `results/<model_name_sanitized>_results.json`.

```

```