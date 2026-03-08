# Warehouse Monitoring Backend

Standalone FastAPI backend for warehouse video indexing and analysis with Twelve Labs.

This project is separated from the highlight-reel application so it can be:

- pushed to its own Git repository
- deployed independently
- handed to a frontend developer as a clean backend integration target

## Features

- `POST /warehouse-monitoring/index-jobs`
- `GET /warehouse-monitoring/index-jobs/{job_id}`
- `POST /warehouse-monitoring/analysis-jobs`
- `GET /warehouse-monitoring/analysis-jobs/{job_id}`
- `GET /health`

The backend supports:

- indexing a local video path or uploaded file in Twelve Labs
- polling for job completion
- bag unloading analysis
- worker productivity analysis
- conservative suspicious-removal detection

## Project Layout

- `warehouse_monitoring_api.py`: FastAPI app and Twelve Labs integration
- `requirements.txt`: standalone backend dependencies
- `.env.example`: environment variable template
- `docs/warehouse_monitoring_api_guide.md`: setup and test guide
- `docs/frontend_integration.md`: frontend handoff notes
- `tests/test_warehouse_monitoring_api.py`: unit tests

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set your Twelve Labs API key in `.env`:

```env
TWELVE_LABS_API_KEY=your_real_api_key_here
```

## Run

```bash
python -m uvicorn warehouse_monitoring_api:app --reload --port 8000
```

## Test

```bash
python -m unittest tests.test_warehouse_monitoring_api
```

## Frontend Handoff

The frontend should use this sequence:

1. `POST /warehouse-monitoring/index-jobs`
2. Poll `GET /warehouse-monitoring/index-jobs/{job_id}` until `status == "completed"`
3. Read `result.index_id` and `result.video_id`
4. Confirm:
   - `result.ready_for_search == true`
   - `result.completion_basis == "indexed_asset_ready"`
5. `POST /warehouse-monitoring/analysis-jobs`
6. Poll `GET /warehouse-monitoring/analysis-jobs/{job_id}` until `status == "completed"`

See [docs/frontend_integration.md](docs/frontend_integration.md) for the exact payload shapes.
