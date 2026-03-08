# Warehouse Monitoring API Guide

This document explains how to set up, run, and test the standalone warehouse monitoring backend.

The backend is a standalone FastAPI service implemented in:

- `warehouse_monitoring_api.py`

It supports a 2-step workflow:

1. Index a video in Twelve Labs and get `index_id` and `video_id`
2. Analyze that indexed video for:
   - bag unloading counts
   - worker idleness and productivity
   - possible theft or suspicious removal

This guide is written for local testing first.

## 1. What This API Does

The backend exposes these endpoints:

- `GET /health`
- `POST /warehouse-monitoring/index-jobs`
- `GET /warehouse-monitoring/index-jobs/{job_id}`
- `POST /warehouse-monitoring/analysis-jobs`
- `GET /warehouse-monitoring/analysis-jobs/{job_id}`

The intended flow is:

1. Start the backend
2. Send a video to the indexing endpoint
3. Poll the indexing job until it completes
4. Read the returned `index_id` and `video_id`
5. Send those IDs to the analysis endpoint
6. Poll the analysis job until it completes
7. Read the final analysis JSON

## 2. Files Involved

These are the files you need to know:

- `warehouse_monitoring_api.py`
  - main backend service
  - all API routes
  - Twelve Labs upload/index logic
  - Twelve Labs analysis logic
  - result normalization logic
- `tests/test_warehouse_monitoring_api.py`
  - unit tests for the API and helper logic
- `requirements.txt`
  - Python dependencies required to run the backend
- `README.md`
  - short project summary and quick API commands
- `docs/frontend_integration.md`
  - concise handoff notes for frontend integration

## 3. Prerequisites

You need:

- Python 3.11+ recommended
- internet access from the backend machine
- a valid Twelve Labs API key
- a video clip to test with

The backend currently supports:

- JSON request with `file_path` pointing to a local video file on the same machine as the backend
- multipart file upload with `file=@video.mp4`

Current upload limit in this implementation:

- 200 MB per file

## 4. Install Dependencies

From the project root:

```bash
pip install -r requirements.txt
```

If you use `uv`, this also works:

```bash
uv pip install -r requirements.txt
```

Important runtime dependencies for this API:

- `fastapi`
- `uvicorn`
- `python-multipart`
- `requests`
- `python-dotenv`

## 5. Configure Environment Variables

Create a `.env` file in the project root if you do not already have one.

Add one of these keys:

```env
TWELVE_LABS_API_KEY=your_real_api_key_here
```

or

```env
TWELVELABS_API_KEY=your_real_api_key_here
```

The backend accepts either variable name.

Without one of these keys, indexing and analysis jobs will fail.

## 6. Start the Backend

From the project root:

```bash
uvicorn warehouse_monitoring_api:app --reload
```

Default server URL:

```text
http://127.0.0.1:8000
```

If you want to run it on port `8080`:

```bash
uvicorn warehouse_monitoring_api:app --reload --port 8080
```

You can also run the file directly:

```bash
python warehouse_monitoring_api.py
```

That uses:

- host: `127.0.0.1`
- port: `8080`

Optional environment overrides:

```env
WAREHOUSE_API_HOST=127.0.0.1
WAREHOUSE_API_PORT=8080
```

## 7. Confirm the Backend Is Running

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "ok"
}
```

If you started the server on port `8080`, replace `8000` with `8080` in all examples below.

## 8. Step 1: Create an Indexing Job

You have 2 supported input modes.

### Option A: Send a Local File Path

Use this when the backend is running on the same machine and can directly access the file.

```bash
curl -X POST http://127.0.0.1:8000/warehouse-monitoring/index-jobs \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/absolute/path/to/your/video.mp4",
    "index_name_prefix": "warehouse-monitoring"
  }'
```

Notes:

- `file_path` must exist on the backend machine
- use an absolute path for clarity
- `index_name_prefix` is optional

### Option B: Upload the File Directly

Use this when you want the backend itself to receive the file.

```bash
curl -X POST http://127.0.0.1:8000/warehouse-monitoring/index-jobs \
  -F file=@/absolute/path/to/your/video.mp4 \
  -F index_name_prefix=warehouse-monitoring
```

Notes:

- the API stores the uploaded file temporarily under `data/uploads/warehouse_monitoring/`
- after the indexing job finishes, the temp upload is deleted automatically

### Expected Response

The API immediately returns a job record, not the final indexing result:

```json
{
  "job_id": "a1b2c3d4...",
  "status": "queued",
  "status_url": "/warehouse-monitoring/index-jobs/a1b2c3d4..."
}
```

## 9. Step 2: Poll the Indexing Job

Use the `job_id` returned above:

```bash
curl http://127.0.0.1:8000/warehouse-monitoring/index-jobs/YOUR_JOB_ID
```

### Possible Status Values

- `queued`
- `running`
- `completed`
- `failed`

For index jobs, `completed` means the backend has confirmed the specific indexed asset is `ready` for downstream API usage such as search and analysis. The Twelve Labs dashboard may still show indexing briefly if its UI or broader index state lags behind the indexed-asset readiness returned by the API.

### Completed Index Job Example

```json
{
  "job_id": "abc123",
  "job_type": "index",
  "status": "completed",
  "input": {
    "file_path": "/absolute/path/to/video.mp4",
    "index_name_prefix": "warehouse-monitoring"
  },
  "created_at": "2026-03-07T10:00:00+00:00",
  "updated_at": "2026-03-07T10:02:30+00:00",
  "result": {
    "index_id": "index_123",
    "index_name": "warehouse-monitoring-20260307-100000-ab12cd34",
    "asset_id": "asset_123",
    "indexed_asset_id": "indexed_123",
    "video_id": "video_123",
    "status": "ready",
    "completion_basis": "indexed_asset_ready",
    "ready_for_search": true,
    "upstream_status": {
      "asset": "ready",
      "indexed_asset": "ready"
    },
    "system_metadata": {}
  }
}
```

Use `result.ready_for_search` and `result.completion_basis` as the authoritative signal that the video is ready for this backend's follow-up analysis flow.

### Failed Index Job Example

```json
{
  "job_id": "abc123",
  "job_type": "index",
  "status": "failed",
  "error": {
    "type": "ValueError",
    "message": "Missing Twelve Labs API key..."
  }
}
```

## 10. Step 3: Create an Analysis Job

Once indexing is complete, use the returned `index_id` and `video_id`. If you want to confirm the backend is ready to proceed even when the Twelve Labs dashboard still says indexing, check for:

- `result.ready_for_search = true`
- `result.completion_basis = "indexed_asset_ready"`
- `result.upstream_status.indexed_asset = "ready"`

```bash
curl -X POST http://127.0.0.1:8000/warehouse-monitoring/analysis-jobs \
  -H "Content-Type: application/json" \
  -d '{
    "index_id": "index_123",
    "video_id": "video_123"
  }'
```

Expected response:

```json
{
  "job_id": "def456",
  "status": "queued",
  "status_url": "/warehouse-monitoring/analysis-jobs/def456"
}
```

## 11. Step 4: Poll the Analysis Job

```bash
curl http://127.0.0.1:8000/warehouse-monitoring/analysis-jobs/YOUR_ANALYSIS_JOB_ID
```

### Completed Analysis Job Example

```json
{
  "job_id": "def456",
  "job_type": "analysis",
  "status": "completed",
  "input": {
    "index_id": "index_123",
    "video_id": "video_123"
  },
  "result": {
    "analysis_generated_at": "2026-03-07T10:05:00+00:00",
    "index_id": "index_123",
    "video_id": "video_123",
    "bag_unloading": {
      "estimated_total_bags_unloaded": 10,
      "count_confidence": "medium",
      "events": [
        {
          "start_sec": 12.0,
          "end_sec": 18.0,
          "count_estimate": 3,
          "description": "Three white sacks leave the truck.",
          "thumbnail_url": "https://..."
        }
      ],
      "notes": "Conservative estimate."
    },
    "worker_productivity": {
      "observed_worker_count": 2,
      "workers": [
        {
          "worker_tag": "worker_1",
          "appearance_summary": "orange vest, dark pants",
          "idle_seconds_estimate": 18.0,
          "active_seconds_estimate": 55.0,
          "productivity_score": 0.7534,
          "productivity_percent": 75.34,
          "status": "active",
          "idle_segments": [
            {
              "start_sec": 30.0,
              "end_sec": 35.0,
              "reason": "waiting near truck"
            }
          ],
          "active_segments": [
            {
              "start_sec": 12.0,
              "end_sec": 18.0,
              "activity": "carrying sacks"
            }
          ]
        }
      ],
      "summary": "Observed 2 workers. 0 mostly idle and 2 active or mixed.",
      "notes": ""
    },
    "theft_detection": {
      "theft_detected": false,
      "suspected_incident_count": 0,
      "incidents": [],
      "notes": ""
    },
    "marengo_evidence": {
      "bag_unloading": [],
      "worker_idle": [],
      "possible_theft": []
    },
    "disclaimer": "Worker idleness, productivity, and theft outputs are model-generated observations and should be reviewed before being treated as final proof."
  }
}
```

## 12. Meaning of the Analysis Output

### `bag_unloading`

This section is the estimated bag or sack count.

Fields:

- `estimated_total_bags_unloaded`
  - total conservative estimate
- `count_confidence`
  - model confidence label
- `events`
  - list of unload events over time
- `notes`
  - explanation or uncertainty note

Each event contains:

- `start_sec`
- `end_sec`
- `count_estimate`
- `description`
- `thumbnail_url`

### `worker_productivity`

This section describes workers using clip-local anonymous labels.

Important:

- these are not real identities
- `worker_1` in one clip is not guaranteed to be the same person in another clip

Fields:

- `observed_worker_count`
- `workers`
- `summary`
- `notes`

Each worker contains:

- `worker_tag`
- `appearance_summary`
- `idle_seconds_estimate`
- `active_seconds_estimate`
- `productivity_score`
- `productivity_percent`
- `status`
- `idle_segments`
- `active_segments`

### Productivity Calculation

The backend computes:

```text
productivity_score = active_seconds / (active_seconds + idle_seconds)
```

If the denominator is `0`, the score is `0`.

Status mapping:

- score `< 0.35` -> `idle`
- score `< 0.70` -> `mixed`
- score `>= 0.70` -> `active`

### `theft_detection`

This section is conservative by design.

Fields:

- `theft_detected`
- `suspected_incident_count`
- `incidents`
- `notes`

Each incident contains:

- `worker_tag`
- `start_sec`
- `end_sec`
- `evidence_timestamp_sec`
- `item_description`
- `suspected_quantity`
- `reason`
- `confidence`
- `thumbnail_url`

Important:

- the backend only returns incidents when the model marks theft and confidence is `high`
- weak or uncertain theft findings are suppressed and returned as:
  - `theft_detected: false`
  - `incidents: []`

### `marengo_evidence`

This is the raw evidence bucket used for audit/debug.

Categories:

- `bag_unloading`
- `worker_idle`
- `possible_theft`

Each evidence item may include:

- `query`
- `start_sec`
- `end_sec`
- `score`
- `confidence`
- `thumbnail_url`
- `video_id`

## 13. Fast Manual Test Flow

Use this exact sequence.

### 1. Start server

```bash
uvicorn warehouse_monitoring_api:app --reload
```

### 2. Create indexing job

```bash
curl -X POST http://127.0.0.1:8000/warehouse-monitoring/index-jobs \
  -H "Content-Type: application/json" \
  -d '{"file_path":"/absolute/path/to/test_clip.mp4"}'
```

### 3. Poll until completed

```bash
curl http://127.0.0.1:8000/warehouse-monitoring/index-jobs/YOUR_INDEX_JOB_ID
```

### 4. Copy `index_id` and `video_id`

From:

- `result.index_id`
- `result.video_id`

### 5. Create analysis job

```bash
curl -X POST http://127.0.0.1:8000/warehouse-monitoring/analysis-jobs \
  -H "Content-Type: application/json" \
  -d '{"index_id":"index_123","video_id":"video_123"}'
```

### 6. Poll until completed

```bash
curl http://127.0.0.1:8000/warehouse-monitoring/analysis-jobs/YOUR_ANALYSIS_JOB_ID
```

If Twelve Labs rate limits search or analysis requests, the backend now waits for the provider's reset time and retries automatically. That means an analysis job may remain `running` longer instead of failing immediately on a temporary `429 too_many_requests` response.

### 7. Inspect output

Check:

- `bag_unloading.estimated_total_bags_unloaded`
- `worker_productivity.workers`
- `theft_detection.incidents`
- `marengo_evidence`

## 14. How to Run Automated Tests

Run unit tests:

```bash
python -m unittest discover -s tests -v
```

Run quick syntax validation:

```bash
python -m py_compile warehouse_monitoring_api.py tests/test_warehouse_monitoring_api.py
```

Current test coverage includes:

- JSON `file_path` validation
- multipart upload handling
- rejection when mixed input modes are sent
- job store lifecycle
- response shape checks
- productivity score logic
- Twelve Labs indexing flow with mocks
- indexing failure propagation with mocks
- structured JSON parsing behavior
- thumbnail fallback behavior

## 15. Common Errors and Fixes

### Error: `file_path does not exist on the backend`

Cause:

- the backend cannot find the file on disk

Fix:

- use an absolute path
- make sure the file exists on the machine running the backend
- make sure the backend process has permission to read it

### Error: `Missing Twelve Labs API key`

Cause:

- `.env` file missing or wrong variable name

Fix:

- set `TWELVE_LABS_API_KEY` or `TWELVELABS_API_KEY`
- restart the backend after updating `.env`

### Error: upload too large

Cause:

- file is larger than 200 MB

Fix:

- use a smaller clip
- compress the clip before uploading
- extend the implementation later for multipart/large-asset upload flow

### Error: indexing job fails inside Twelve Labs

Cause:

- unsupported file
- bad upload
- remote API issue

Fix:

- test with a short `.mp4`
- confirm Twelve Labs key is valid
- inspect the returned job `error.message`

### Error: analysis job fails

Cause:

- invalid `index_id`
- invalid `video_id`
- video not ready
- Twelve Labs analyze response format changed

Fix:

- confirm the IDs came from a completed indexing job
- re-run the indexing step
- inspect the failure message in the analysis job

## 16. Important Current Limitations

- job state is in-memory only
  - if the server restarts, jobs are lost
- no live streaming
  - this is clip-based async processing
- no persistent database
- no authentication on the local API
- no local frame extraction
  - thumbnails come from Twelve Labs search results when available
- worker IDs are anonymous per clip only
  - they are not real-world identity tracking

## 17. Suggested First Real Test

Use a short clip that clearly shows:

- a truck
- white sacks being unloaded
- at least 1 worker pausing or standing idle
- at least 1 worker carrying sacks

That will make it easier to confirm:

- bag counts are reasonable
- worker segmentation exists
- productivity output is not empty
- evidence windows include useful timestamps

## 18. Suggested cURL Session

### Index

```bash
curl -X POST http://127.0.0.1:8000/warehouse-monitoring/index-jobs \
  -H "Content-Type: application/json" \
  -d '{"file_path":"/Users/yourname/Desktop/test_clip.mp4","index_name_prefix":"warehouse-monitoring"}'
```

### Poll index job

```bash
curl http://127.0.0.1:8000/warehouse-monitoring/index-jobs/REPLACE_WITH_JOB_ID
```

### Analyze

```bash
curl -X POST http://127.0.0.1:8000/warehouse-monitoring/analysis-jobs \
  -H "Content-Type: application/json" \
  -d '{"index_id":"REPLACE_WITH_INDEX_ID","video_id":"REPLACE_WITH_VIDEO_ID"}'
```

### Poll analysis job

```bash
curl http://127.0.0.1:8000/warehouse-monitoring/analysis-jobs/REPLACE_WITH_ANALYSIS_JOB_ID
```

## 19. If You Want to Hand This to Frontend Later

The frontend only needs to know this sequence:

1. call `POST /warehouse-monitoring/index-jobs`
2. poll the returned `status_url`
3. when completed, read `result.index_id` and `result.video_id`
4. call `POST /warehouse-monitoring/analysis-jobs`
5. poll the returned `status_url`
6. when completed, render `result.bag_unloading`, `result.worker_productivity`, and `result.theft_detection`

## 20. Current Command Reference

Start backend:

```bash
uvicorn warehouse_monitoring_api:app --reload
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

Syntax check:

```bash
python -m py_compile warehouse_monitoring_api.py tests/test_warehouse_monitoring_api.py
```
