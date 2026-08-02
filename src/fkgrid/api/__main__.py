"""Run the local FastAPI demo with ``python -m fkgrid.api``."""

from fkgrid.api.main import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
