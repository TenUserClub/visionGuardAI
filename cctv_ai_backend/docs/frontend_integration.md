# Frontend Integration Notes

This backend is job-based. The frontend should never assume indexing or analysis is synchronous.

## Base URL

```text
http://127.0.0.1:8000
```

## Endpoints

### Health

```http
GET /health
```

Response:

```json
{
  "status": "ok"
}
```

### Create index job

```http
POST /warehouse-monitoring/index-jobs
Content-Type: application/json
```

Request:

```json
{
  "file_path": "/absolute/path/to/video.mp4",
  "index_name_prefix": "warehouse-monitoring"
}
```

Accepted response:

```json
{
  "job_id": "abc123",
  "status": "queued",
  "status_url": "/warehouse-monitoring/index-jobs/abc123"
}
```

### Poll index job

```http
GET /warehouse-monitoring/index-jobs/{job_id}
```

Frontend-ready conditions:

- top-level `status == "completed"`
- `result.ready_for_search == true`
- `result.completion_basis == "indexed_asset_ready"`
- `result.upstream_status.indexed_asset == "ready"`

Use these fields from the completed payload:

- `result.index_id`
- `result.video_id`
- `result.system_metadata`

### Create analysis job

```http
POST /warehouse-monitoring/analysis-jobs
Content-Type: application/json
```

Request:

```json
{
  "index_id": "index_123",
  "video_id": "video_123"
}
```

Accepted response:

```json
{
  "job_id": "def456",
  "status": "queued",
  "status_url": "/warehouse-monitoring/analysis-jobs/def456"
}
```

### Poll analysis job

```http
GET /warehouse-monitoring/analysis-jobs/{job_id}
```

Use these fields from the completed payload:

- `result.bag_unloading`
- `result.worker_productivity`
- `result.theft_detection`
- `result.marengo_evidence`
- `result.disclaimer`

## UI Notes

- Analysis may stay `running` longer when the provider rate-limits requests.
- `theft_detection` is intentionally conservative and should be presented as suspicious activity, not final proof.
- Thumbnail URLs are temporary signed URLs and may expire.
