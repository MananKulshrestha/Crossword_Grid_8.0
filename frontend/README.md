# FK GRiD Frontend

This is the Vite and React frontend for the FK GRiD shopping assistant.

## Requirements

- Node.js 20 or later
- npm
- The FK GRiD API running locally when using the shopping assistant

## Run locally

From this directory:

```bash
npm install
cp .env.example .env.local
npm run dev
```

Vite prints the local URL after it starts, usually `http://localhost:5173`.

The default API URL is `http://127.0.0.1:8000`. To use a different API, set
`VITE_API_BASE_URL` in `.env.local`:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## Start the API

In a second terminal, from the repository root, start the backend API:

```bash
PYTHONPATH=src FKGRID_MODEL_MODE=fake uvicorn fkgrid.api.main:app --host 127.0.0.1 --port 8000
```

Use `FKGRID_MODEL_MODE=live` and configure the provider environment variables
described in the repository README when connecting to the live model.

## Other commands

```bash
npm run build
npm run lint
npm run preview
```

`npm run build` creates a production bundle in `dist/`. `npm run preview`
serves that bundle locally after a build.
