# GridKart frontend

React/Vite wrapper for the existing `fkgrid` FastAPI application. It does not contain catalog fixtures or duplicate shopping logic. All search, product, comparison, availability, and cart responses come from the existing session/turn API.

## Run locally

From the repository root, start the backend:

```bash
./run_server.sh
```

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to `http://127.0.0.1:8000`, avoiding any backend CORS change.

For a separately hosted frontend, set `VITE_API_BASE_URL` to the reachable API base URL before building. The backend must then allow the frontend origin at the deployment layer.

## API mapping

- `POST /v1/sessions` creates a conversation.
- `POST /v1/sessions/{session_id}/turns` sends every user request through the existing orchestrator.
- Structured `search_result`, `product_details`, `comparison`, `availability`, and `cart` fields are rendered directly.

The backend currently supplies no product image URL, original price, discount, review count, or delivery estimate. The UI intentionally does not fabricate these fields.
