# Future fixes

| Issue | → | What is needed |
|---|---|---|
| Conversation state, acknowledged results, and the prototype cart are lost when Uvicorn restarts or when requests are routed to another worker. | → | Add a durable session store (SQLite for the prototype, PostgreSQL for production) covering messages/query state, acknowledged result sets, cart state, TTL cleanup, optimistic concurrency, and idempotency reservations. |
| A session cannot currently be resumed after a process failure or deployed instance replacement. | → | Persist session snapshots and replay-safe turn results, then add restart, multi-worker, and recovery tests. |

The current numeric cart issue does not belong here: it is handled in-memory by
resolving the requested ordinal against the session's acknowledged result set.
