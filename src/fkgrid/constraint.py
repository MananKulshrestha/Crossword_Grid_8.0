"""Constraint lane: N objects, one shared budget, best possible basket.

Separate pipeline from the search/cart/compare lanes - nothing here touches
the query extractor, the enhancer, or the reranker, so the existing (tested)
turn path is unchanged. Entered only when the shopper picks constrain mode.

Three steps:

1. Decompose  - one LLM call splits the request into N object sub-queries
                plus the shared total budget (llm.decompose_basket_request).
2. Fan out    - one plain SQL query per object against MySQL. Keyword LIKE
                matching scored in the query itself; no reranker, no BM25,
                no vectors. Every candidate is a real catalog row by
                construction, so nothing can be hallucinated here.
3. Optimise   - search the combination space for the basket that maximises
                total relevance subject to sum(price) <= budget.

Any MySQL failure raises CatalogError and any LLM failure raises LLMError,
matching the rest of the pipeline: a failure is a hard pause, never a
silently degraded answer.
"""

from __future__ import annotations

import functools

from . import db, llm
from .catalog import CatalogError, _entry_from_row
from .contracts import Basket, BasketSlot, BasketSlotSpec, SearchEntry

# Ceiling on how many (slot, candidate) combinations the optimiser will
# enumerate. Candidate pools are trimmed per slot to keep the product under
# this, so solve time stays flat regardless of how many objects were asked
# for.
_MAX_COMBINATIONS = 200_000

# Candidates pulled from MySQL per object before trimming.
_CANDIDATES_PER_SLOT = 60

# Weight of the catalog rating (normalised to 0-1) against keyword match
# quality (also 0-1) in a candidate's relevance score. Match dominates -
# rating only breaks ties between comparably relevant products.
_RATING_WEIGHT = 0.3

# Dropped from an object label before it becomes a required match - these
# never appear in a catalog title ("a pair of shoes" -> "shoes").
_FILLER = {"pair", "set", "the", "and", "for", "one", "some", "any", "new"}


class ConstraintError(RuntimeError):
    """Raised when the request can't be turned into a solvable basket."""


@functools.lru_cache(maxsize=1)
def allowed_categories() -> list[str]:
    """Literal category names in the catalog, most populated first. Passed to
    the decomposer so it can only ever emit a category that exists."""

    query = """
    SELECT category, COUNT(*) AS n
    FROM product_metadata
    WHERE category IS NOT NULL AND category <> ''
    GROUP BY category
    HAVING n >= 20
    ORDER BY n DESC
    LIMIT 60
    """
    print(f"[SQL-CONSTRAIN] {query.strip()}")
    try:
        with db.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
    except Exception as exc:  # pymysql surfaces several unrelated error types
        raise CatalogError(f"CATALOG_UNAVAILABLE:{exc}") from exc
    return [row["category"] for row in rows]


_CANDIDATE_QUERY = """
SELECT
    s.sku_id, s.product_id, o.offer_id,
    p.title, p.brand_name, p.rating,
    pm.category,
    o.price_paise, o.availability_status, o.quantity,
    ({score_sql}) AS match_score
FROM skus s
JOIN offers o ON o.sku_id = s.sku_id AND o.catalog_version = s.catalog_version
JOIN products p ON p.product_id = s.product_id AND p.catalog_version = s.catalog_version
LEFT JOIN product_metadata pm ON pm.sku_id = s.sku_id
WHERE o.availability_status <> 'OUT_OF_STOCK'
  AND o.price_paise IS NOT NULL
  AND o.price_paise > 0
  {price_sql}
  {category_sql}
  AND ({match_sql})
ORDER BY match_score DESC, p.rating DESC, o.price_paise ASC
LIMIT {limit}
"""


def _like(word: str) -> str:
    """LIKE pattern for one word, plural-insensitive. Catalog titles are
    overwhelmingly singular ("Earring" 80 rows vs "Earrings" 1), and shoppers
    ask in the plural, so a trailing 's' is dropped - the pattern is a
    substring match either way, so it still matches the plural form."""

    word = word.strip().lower()
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return f"%{word}%"


def _fetch_candidates(spec: BasketSlotSpec, budget_paise: int | None) -> list[tuple[SearchEntry, float]]:
    """One plain SQL query for one object. Returns (entry, relevance) pairs,
    relevance in 0-1 and already sorted best-first."""

    keywords = [kw.strip() for kw in spec.keywords if kw and kw.strip()]
    if not keywords:
        keywords = [spec.label]

    score_parts: list[str] = []
    score_params: list = []
    for keyword in keywords:
        like = _like(keyword)
        # Title hits are worth more than brand/category hits - a "monitor" in
        # the title is the object itself, in the category it's just the aisle.
        score_parts.append(
            "CASE WHEN p.title LIKE %s THEN 3 "
            "WHEN p.brand_name LIKE %s THEN 2 "
            "WHEN pm.category LIKE %s THEN 1 ELSE 0 END"
        )
        score_params += [like, like, like]

    def _match_clause(required: list[str]) -> tuple[str, list]:
        parts: list[str] = []
        params_: list = []
        for keyword in required:
            like = _like(keyword)
            parts.append("(p.title LIKE %s OR p.brand_name LIKE %s OR pm.category LIKE %s)")
            params_ += [like, like, like]
        return " AND ".join(parts), params_

    price_sql = "AND o.price_paise <= %s" if budget_paise is not None else ""
    category_sql = "AND pm.category = %s" if spec.category else ""

    def _run(match_sql: str, match_params: list) -> list[dict]:
        params: list = list(score_params)
        if budget_paise is not None:
            # No single item may exceed the whole basket budget.
            params.append(budget_paise)
        if spec.category:
            params.append(spec.category)
        params += match_params
        query = _CANDIDATE_QUERY.format(
            score_sql=" + ".join(score_parts),
            price_sql=price_sql,
            category_sql=category_sql,
            match_sql=match_sql,
            limit=_CANDIDATES_PER_SLOT,
        )
        print(f"[SQL-CONSTRAIN] {query.strip()} -- params={params}")
        try:
            with db.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchall()
        except Exception as exc:
            raise CatalogError(f"CATALOG_UNAVAILABLE:{exc}") from exc

    # The label names the object ("formal trousers"); the keywords are only
    # qualifiers and are used for scoring, not for gating - keywords[0] is as
    # often "formal" as it is "trousers", which is how a formal shirt ends up
    # answering a request for formal trousers.
    #
    # Require every word of the label, then fall back to just its head noun.
    # Never widen to an OR - that is what lets a "keyboard light" answer a
    # request for a keyboard, or a bedsheet answer a request for a feeding
    # bottle. An unfilled slot the shopper can see is better than a
    # confidently wrong one.
    tokens = [word for word in spec.label.lower().split() if len(word) > 2 and word not in _FILLER]
    if not tokens:
        tokens = [word for word in keywords[0].lower().split() if len(word) > 2] or [keywords[0]]
    rows = _run(*_match_clause(tokens))
    if not rows and len(tokens) > 1:
        rows = _run(*_match_clause(tokens[-1:]))

    if not rows:
        return []

    max_score = max(float(row["match_score"] or 0) for row in rows) or 1.0
    out: list[tuple[SearchEntry, float]] = []
    for row in rows:
        match = float(row["match_score"] or 0) / max_score
        rating = float(row["rating"]) / 5.0 if row.get("rating") is not None else 0.0
        relevance = (match + _RATING_WEIGHT * rating) / (1.0 + _RATING_WEIGHT)
        out.append((_entry_from_row(row, relevance), relevance))
    return out


def _trim(pools: list[list[tuple[SearchEntry, float]]]) -> list[list[tuple[SearchEntry, float]]]:
    """Cut each pool to a size whose product stays under _MAX_COMBINATIONS,
    keeping the top-scoring candidates plus the three cheapest. The cheap
    ones matter: without them a slot whose good options are all expensive can
    make an otherwise-feasible basket look impossible."""

    if not pools:
        return pools
    per_slot = max(4, int(_MAX_COMBINATIONS ** (1.0 / len(pools))))
    trimmed: list[list[tuple[SearchEntry, float]]] = []
    for pool in pools:
        if len(pool) <= per_slot:
            trimmed.append(pool)
            continue
        keep = list(pool[:per_slot])
        cheapest = sorted(pool, key=lambda pair: pair[0].price_paise or 0)[:3]
        seen = {entry.sku_id for entry, _ in keep}
        keep += [pair for pair in cheapest if pair[0].sku_id not in seen]
        trimmed.append(keep)
    return trimmed


def _optimise(
    pools: list[list[tuple[SearchEntry, float]]], budget_paise: int | None
) -> list[tuple[SearchEntry, float]] | None:
    """Pick one candidate per slot maximising total relevance subject to
    sum(price) <= budget. Depth-first over slots, ordered best-score-first,
    pruned on both the cheapest remaining spend and the best remaining score
    - which is what keeps this exact rather than greedy."""

    if not pools or any(not pool for pool in pools):
        return None
    if budget_paise is None:
        # No budget: the constraint is vacuous, take the best of each, still
        # refusing to put one SKU in two slots.
        picked: list[tuple[SearchEntry, float]] = []
        used: set[str] = set()
        for pool in pools:
            choice = next((pair for pair in pool if pair[0].sku_id not in used), pool[0])
            used.add(choice[0].sku_id)
            picked.append(choice)
        return picked

    n = len(pools)
    # suffix_min_price[i] = cheapest possible spend on slots i..n-1
    # suffix_max_score[i] = best possible score from slots i..n-1
    suffix_min_price = [0] * (n + 1)
    suffix_max_score = [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_min_price[i] = suffix_min_price[i + 1] + min((e.price_paise or 0) for e, _ in pools[i])
        suffix_max_score[i] = suffix_max_score[i + 1] + max(score for _, score in pools[i])

    if suffix_min_price[0] > budget_paise:
        return None

    best: dict[str, object] = {"score": -1.0, "combo": None}
    chosen: list[tuple[SearchEntry, float]] = [None] * n  # type: ignore[list-item]
    # One SKU can't fill two slots - "a blanket and a bottle" answered with
    # the same product twice is not a basket.
    used: set[str] = set()

    def walk(index: int, spent: int, score: float) -> None:
        if spent + suffix_min_price[index] > budget_paise:
            return
        if score + suffix_max_score[index] <= float(best["score"]):
            return
        if index == n:
            best["score"] = score
            best["combo"] = list(chosen)
            return
        for entry, entry_score in pools[index]:
            if entry.sku_id in used:
                continue
            price = entry.price_paise or 0
            if spent + price + suffix_min_price[index + 1] > budget_paise:
                continue
            chosen[index] = (entry, entry_score)
            used.add(entry.sku_id)
            walk(index + 1, spent + price, score + entry_score)
            used.discard(entry.sku_id)

    walk(0, 0, 0.0)
    return best["combo"]  # type: ignore[return-value]


def _rupees(paise: int) -> str:
    return f"Rs {paise / 100:,.0f}"


def _explain(basket: Basket) -> str:
    lines = [
        f"{slot.label}: {slot.entry.title} - {_rupees(slot.entry.price_paise or 0)}"
        for slot in basket.slots
    ]
    total = f"Total {_rupees(basket.total_paise)}"
    if basket.budget_paise is not None:
        total += f" of {_rupees(basket.budget_paise)}"
        if basket.headroom_paise is not None:
            total += f" - {_rupees(basket.headroom_paise)} left over"
    lines.append(total)
    if basket.naive_total_paise and basket.naive_total_paise > basket.total_paise:
        lines.append(
            f"Taking the top pick for every slot would have cost "
            f"{_rupees(basket.naive_total_paise)}, so some relevance was traded for a basket "
            f"that fits."
        )
    if basket.unfilled:
        lines.append(f"No catalog match for: {', '.join(basket.unfilled)}.")
    return "\n".join(lines)


def solve(message: str) -> Basket:
    """Full constraint lane for one shopper message. Raises LLMError,
    CatalogError or ConstraintError - all hard failures, never a fallback."""

    decomposed = llm.decompose_basket_request(message, allowed_categories())

    budget_rupees = decomposed.get("budget_rupees")
    budget_paise: int | None = None
    if isinstance(budget_rupees, (int, float)) and budget_rupees > 0:
        budget_paise = int(round(float(budget_rupees) * 100))

    specs: list[BasketSlotSpec] = []
    for raw in decomposed.get("items", []):
        if not isinstance(raw, dict) or not raw.get("label"):
            continue
        specs.append(
            BasketSlotSpec(
                label=str(raw["label"]),
                keywords=[str(kw) for kw in (raw.get("keywords") or []) if str(kw).strip()],
                category=raw.get("category") or None,
            )
        )
    if not specs:
        raise ConstraintError("BASKET_NO_ITEMS")

    pools: list[list[tuple[SearchEntry, float]]] = []
    filled: list[BasketSlotSpec] = []
    unfilled: list[str] = []
    for spec in specs:
        candidates = _fetch_candidates(spec, budget_paise)
        if not candidates:
            unfilled.append(spec.label)
            continue
        pools.append(candidates)
        filled.append(spec)

    if not pools:
        raise ConstraintError("BASKET_NO_CANDIDATES")

    pool_sizes = [len(pool) for pool in pools]
    naive_total = sum((pool[0][0].price_paise or 0) for pool in pools)
    best_scores = [pool[0][1] for pool in pools]

    combo = _optimise(_trim(pools), budget_paise)
    if combo is None:
        raise ConstraintError("BASKET_INFEASIBLE")

    slots = [
        BasketSlot(
            label=spec.label,
            entry=entry,
            candidate_count=size,
            best_available_score=best_score,
            chosen_score=score,
        )
        for spec, (entry, score), size, best_score in zip(
            filled, combo, pool_sizes, best_scores, strict=True
        )
    ]
    total = sum((slot.entry.price_paise or 0) for slot in slots)
    basket = Basket(
        slots=slots,
        budget_paise=budget_paise,
        total_paise=total,
        headroom_paise=(budget_paise - total) if budget_paise is not None else None,
        naive_total_paise=naive_total,
        unfilled=unfilled,
    )
    basket.explanation = _explain(basket)
    return basket
