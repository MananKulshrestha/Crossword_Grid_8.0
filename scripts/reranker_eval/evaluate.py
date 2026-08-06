"""nDCG@{1,3,5} + hallucination-rate evaluation for the fkgrid reranker.

Ground truth for each synthetic query is derived live from MySQL
(`product_metadata`), per the tiering rules in queries.py. For every query we
POST to the reranker's /api/search, score the returned ranking against the
ground-truth relevant set with binary-relevance nDCG, and separately verify
every returned sku_id actually exists in MySQL (hallucination check).

Usage:
    python3 evaluate.py [--top-n 10] [--out results.json]

Requires: pymysql, requests, tqdm (see ../../pyproject.toml [dev] extras).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pymysql
import pymysql.cursors
import requests
from tqdm import tqdm

from queries import ALL_QUERIES, TIER1, TIER2, TIER3, QuerySpec

MYSQL_HOST = os.environ.get("FKGRID_MYSQL_HOST", "213.173.105.95")
MYSQL_PORT = int(os.environ.get("FKGRID_MYSQL_PORT", "24679"))
MYSQL_USER = os.environ.get("FKGRID_MYSQL_USER", "flipkart_user")
MYSQL_PASSWORD = os.environ.get("FKGRID_MYSQL_PASSWORD", "flipkart_pass")
MYSQL_DB = os.environ.get("FKGRID_MYSQL_DB", "flipkart")

RERANKER_ENDPOINT = os.environ.get(
    "FKGRID_RERANKER_ENDPOINT",
    "https://dk82n1hdtxfqmf-8002.proxy.runpod.net/api/search",
)
RERANKER_TIMEOUT_S = float(os.environ.get("FKGRID_RERANKER_TIMEOUT_S", "60"))


def connect_mysql() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_ground_truth(cursor, spec: QuerySpec) -> set[str]:
    conditions = ["category = %s"]
    params: list = [spec.category]
    if spec.max_price is not None:
        conditions.append("discounted_price <= %s")
        params.append(spec.max_price)
        conditions.append("stock_status = 'in_stock'")
    for keyword in spec.keywords:
        conditions.append("product_name LIKE %s")
        params.append(f"%{keyword}%")
    query = f"SELECT sku_id FROM product_metadata WHERE {' AND '.join(conditions)}"
    cursor.execute(query, params)
    return {row["sku_id"] for row in cursor.fetchall()}


def fetch_existing_sku_ids(cursor, sku_ids: list[str]) -> set[str]:
    if not sku_ids:
        return set()
    placeholders = ",".join(["%s"] * len(sku_ids))
    cursor.execute(f"SELECT sku_id FROM product_metadata WHERE sku_id IN ({placeholders})", sku_ids)
    return {row["sku_id"] for row in cursor.fetchall()}


def call_reranker(spec: QuerySpec, top_n: int) -> list[str]:
    payload = {
        "soft_query_text": spec.soft_query_text,
        "hard_constraints": spec.hard_constraints,
        "top_n": top_n,
    }
    response = requests.post(RERANKER_ENDPOINT, json=payload, timeout=RERANKER_TIMEOUT_S)
    response.raise_for_status()
    body = response.json()
    results = body.get("results", [])
    return [item["sku_id"] for item in results if item.get("sku_id")]


def dcg_at_k(relevances: list[int], k: int) -> float:
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))


def ndcg_at_k(relevances: list[int], k: int, num_relevant: int) -> float | None:
    if num_relevant == 0:
        return None
    ideal_count = min(k, num_relevant)
    idcg = sum(1 / math.log2(i + 2) for i in range(ideal_count))
    if idcg == 0:
        return None
    return dcg_at_k(relevances, k) / idcg


def _evaluate_one(spec: QuerySpec, top_n: int) -> dict:
    # Own MySQL connection - pymysql connections aren't thread-safe, and each
    # worker in the pool needs to fetch ground truth / verify sku_ids independently.
    conn = connect_mysql()
    try:
        with conn.cursor() as cursor:
            ground_truth = fetch_ground_truth(cursor, spec)

            error = None
            sku_ids: list[str] = []
            elapsed_s = 0.0
            try:
                start = time.monotonic()
                sku_ids = call_reranker(spec, top_n)
                elapsed_s = time.monotonic() - start
            except (requests.RequestException, ValueError) as exc:
                error = str(exc)

            existing = fetch_existing_sku_ids(cursor, sku_ids)
            hallucinated = [s for s in sku_ids if s not in existing]

            relevances = [1 if s in ground_truth else 0 for s in sku_ids]
            num_relevant = len(ground_truth)
            ndcg_scores = {
                k: ndcg_at_k(relevances, k, num_relevant) for k in (1, 3, 5)
            }

            return {
                "query_id": spec.query_id,
                "tier": spec.tier,
                "soft_query_text": spec.soft_query_text,
                "hard_constraints": spec.hard_constraints,
                "ground_truth_size": num_relevant,
                "returned_sku_ids": sku_ids,
                "relevances": relevances,
                "ndcg": ndcg_scores,
                "hallucinated_sku_ids": hallucinated,
                "hallucination_rate": (len(hallucinated) / len(sku_ids)) if sku_ids else None,
                "latency_s": round(elapsed_s, 2),
                "error": error,
            }
    finally:
        conn.close()


def evaluate_queries(
    top_n: int, queries: list[QuerySpec], desc: str = "Evaluating reranker", concurrency: int = 4
) -> list[dict]:
    results_by_id: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_evaluate_one, spec, top_n): spec.query_id for spec in queries}
        for future in tqdm(as_completed(futures), total=len(futures), desc=desc, unit="query"):
            query_id = futures[future]
            results_by_id[query_id] = future.result()

    # Preserve the original query order regardless of completion order.
    return [results_by_id[spec.query_id] for spec in queries]


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _ndcg_for_scoring(r: dict, k: int) -> float | None:
    # A query that errored out (timeout, HTTP failure, malformed response) is
    # a real production failure, not a data artifact - score it as 0 rather
    # than excluding it, so an unreliable endpoint can't hide behind "n/a".
    if r["error"] is not None:
        return 0.0
    return r["ndcg"][k]


def summarize(per_query_results: list[dict], top_n: int) -> dict:
    tiers: dict[int, list[dict]] = {}
    for r in per_query_results:
        tiers.setdefault(r["tier"], []).append(r)

    tier_summary = {}
    for tier, rows in tiers.items():
        ok_rows = [r for r in rows if r["error"] is None]
        num_errors = len(rows) - len(ok_rows)
        tier_summary[tier] = {
            "num_queries": len(rows),
            "num_errors": num_errors,
            "ndcg@1": _mean([_ndcg_for_scoring(r, 1) for r in rows]),
            "ndcg@3": _mean([_ndcg_for_scoring(r, 3) for r in rows]),
            "ndcg@5": _mean([_ndcg_for_scoring(r, 5) for r in rows]),
            "hallucination_rate": _mean([r["hallucination_rate"] for r in ok_rows]),
        }

    ok_rows = [r for r in per_query_results if r["error"] is None]
    total_returned = sum(len(r["returned_sku_ids"]) for r in ok_rows)
    total_hallucinated = sum(len(r["hallucinated_sku_ids"]) for r in ok_rows)

    overall = {
        "num_queries": len(per_query_results),
        "num_errors": len(per_query_results) - len(ok_rows),
        "ndcg@1": _mean([_ndcg_for_scoring(r, 1) for r in per_query_results]),
        "ndcg@3": _mean([_ndcg_for_scoring(r, 3) for r in per_query_results]),
        "ndcg@5": _mean([_ndcg_for_scoring(r, 5) for r in per_query_results]),
        "hallucination_rate_by_query": _mean([r["hallucination_rate"] for r in ok_rows]),
        "hallucination_rate_by_sku": (total_hallucinated / total_returned) if total_returned else None,
        "total_sku_ids_returned": total_returned,
        "total_sku_ids_hallucinated": total_hallucinated,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_n": top_n,
        "reranker_endpoint": RERANKER_ENDPOINT,
        "overall": overall,
        "by_tier": tier_summary,
        "per_query": per_query_results,
    }


def format_score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


TIER_NAMES = {1: "Tier 1 (low ambiguity)", 2: "Tier 2 (medium)", 3: "Tier 3 (high ambiguity)"}


def print_report(report: dict, title: str = "Reranker evaluation report") -> None:
    tier_names = TIER_NAMES
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)
    print(f"Endpoint: {report['reranker_endpoint']}")
    print(f"top_n:    {report['top_n']}")
    print(f"Run at:   {report['generated_at']}")
    print()
    header = f"{'Tier':<28}{'nDCG@1':>10}{'nDCG@3':>10}{'nDCG@5':>10}{'Halluc.':>12}"
    print(header)
    print("-" * len(header))
    for tier in sorted(report["by_tier"]):
        s = report["by_tier"][tier]
        print(
            f"{tier_names[tier]:<28}"
            f"{format_score(s['ndcg@1']):>10}"
            f"{format_score(s['ndcg@3']):>10}"
            f"{format_score(s['ndcg@5']):>10}"
            f"{format_score(s['hallucination_rate']):>12}"
        )
    overall = report["overall"]
    print("-" * len(header))
    print(
        f"{'Overall':<28}"
        f"{format_score(overall['ndcg@1']):>10}"
        f"{format_score(overall['ndcg@3']):>10}"
        f"{format_score(overall['ndcg@5']):>10}"
        f"{format_score(overall['hallucination_rate_by_query']):>12}"
    )
    print()
    print(
        f"Hallucinated {overall['total_sku_ids_hallucinated']} / "
        f"{overall['total_sku_ids_returned']} returned sku_ids "
        f"({format_score(overall['hallucination_rate_by_sku'])} by-sku rate)."
    )
    if overall["num_errors"]:
        print(f"WARNING: {overall['num_errors']} / {overall['num_queries']} queries errored out.")
    print("=" * 72)


TIER_MAP = {1: TIER1, 2: TIER2, 3: TIER3}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=10, help="top_n to request from the reranker")
    parser.add_argument("--out", default=None, help="path to write full JSON results")
    parser.add_argument("--tier1", action="store_true", help="run only tier 1 (low ambiguity) queries")
    parser.add_argument("--tier2", action="store_true", help="run only tier 2 (medium ambiguity) queries")
    parser.add_argument("--tier3", action="store_true", help="run only tier 3 (high ambiguity) queries")
    parser.add_argument(
        "--concurrency", type=int, default=4, help="number of reranker queries to run in parallel"
    )
    args = parser.parse_args()

    selected_tiers = [t for t in (1, 2, 3) if getattr(args, f"tier{t}")] or [1, 2, 3]

    all_per_query: list[dict] = []
    for tier in selected_tiers:
        tier_per_query = evaluate_queries(
            args.top_n, TIER_MAP[tier], desc=f"Tier {tier}", concurrency=args.concurrency
        )
        all_per_query.extend(tier_per_query)
        # Print this tier's score immediately, before moving on to the next.
        print_report(summarize(tier_per_query, args.top_n), title=f"{TIER_NAMES[tier]} - done")

    report = summarize(all_per_query, args.top_n)
    if len(selected_tiers) > 1:
        print_report(report, title="Reranker evaluation report - all tiers")

    out_path = args.out or os.path.join(
        os.path.dirname(__file__), "results", f"eval_{int(time.time())}.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
