import json
import os
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import UploadFile as StarletteUploadFile

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


PROJECT_ROOT = Path(__file__).resolve().parent


def load_project_env(env_path: Path) -> None:
    # Prefer python-dotenv when available, but still support local runs without it.
    if load_dotenv(env_path):
        return

    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


load_project_env(PROJECT_ROOT / ".env")

BASE_URL = "https://api.twelvelabs.io/v1.3"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_DIRECTORY = Path("data/uploads/warehouse_monitoring")
UPLOAD_CHUNK_SIZE = 1024 * 1024
DEFAULT_INDEX_PREFIX = "warehouse-monitoring"
DEFAULT_RATE_LIMIT_RETRIES = 3

app = FastAPI(title="Warehouse Monitoring API", version="1.0.0")

# Enable CORS for frontend connectivity (Vercel -> Render)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production if necessary
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TwelveLabsAPIError(RuntimeError):
    """Raised when Twelve Labs returns an unexpected response."""


class IndexJobRequest(BaseModel):
    file_path: str
    index_name_prefix: str = DEFAULT_INDEX_PREFIX


class AnalysisJobRequest(BaseModel):
    index_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)


class SearchPreset(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    queries: list[str]
    search_options: list[str]
    transcription_options: list[str] | None = None
    threshold: str = "medium"
    page_limit: int = 6


class InMemoryJobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, *, job_type: str, input_payload: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        timestamp = utc_now()
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "job_type": job_type,
                "status": "queued",
                "input": input_payload,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        return job_id

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            self._jobs[job_id].update(changes)
            self._jobs[job_id]["updated_at"] = utc_now()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else dict(job)


JOB_STORE = InMemoryJobStore()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_filename(filename: str | None) -> str:
    safe_name = os.path.basename((filename or "").strip()).replace("\x00", "")
    return safe_name or "upload.mp4"


def sanitize_index_prefix(value: str | None) -> str:
    cleaned = "".join(
        character.lower()
        for character in (value or DEFAULT_INDEX_PREFIX)
        if character.isalnum() or character in {"-", "_"}
    ).strip("-_")
    return cleaned or DEFAULT_INDEX_PREFIX


def ensure_upload_directory() -> None:
    UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)


def start_background_job(target: Any, **kwargs: Any) -> None:
    worker = threading.Thread(target=target, kwargs=kwargs, daemon=True)
    worker.start()


def build_service() -> "TwelveLabsWarehouseMonitoringService":
    return TwelveLabsWarehouseMonitoringService()


def normalize_segment_list(
    segments: list[dict[str, Any]],
    *,
    reason_key: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for segment in segments:
        normalized.append(
            {
                "start_sec": float(segment.get("start_sec", 0)),
                "end_sec": float(segment.get("end_sec", 0)),
                reason_key: str(segment.get(reason_key, "")).strip(),
            }
        )
    return normalized


def sum_segment_duration(segments: list[dict[str, Any]]) -> float:
    total = 0.0
    for segment in segments:
        start = float(segment.get("start_sec", 0))
        end = float(segment.get("end_sec", 0))
        total += max(end - start, 0.0)
    return round(total, 2)


def compute_productivity_metrics(active_seconds: float, idle_seconds: float) -> tuple[float, float]:
    denominator = max(active_seconds + idle_seconds, 0.0)
    if denominator == 0:
        return 0.0, 0.0
    score = round(active_seconds / denominator, 4)
    return score, round(score * 100, 2)


def infer_worker_status(productivity_score: float) -> str:
    if productivity_score < 0.35:
        return "idle"
    if productivity_score < 0.7:
        return "mixed"
    return "active"


def extract_analysis_payload(raw_data: Any) -> dict[str, Any]:
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, str):
        parsed = json.loads(raw_data)
        if not isinstance(parsed, dict):
            raise TwelveLabsAPIError("Structured analysis payload must decode to an object.")
        return parsed
    raise TwelveLabsAPIError("Structured analysis payload was not a JSON object.")


def pick_thumbnail_url(
    *,
    start_sec: float,
    end_sec: float,
    evidence_windows: list[dict[str, Any]],
) -> str | None:
    overlapping = [
        window
        for window in evidence_windows
        if float(window.get("start_sec", 0)) <= end_sec
        and float(window.get("end_sec", 0)) >= start_sec
    ]
    candidates = overlapping or evidence_windows
    for candidate in candidates:
        thumbnail_url = candidate.get("thumbnail_url")
        if thumbnail_url:
            return str(thumbnail_url)
    return None


def normalize_bag_report(
    bag_report: dict[str, Any],
    evidence_windows: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_events = []
    for event in bag_report.get("events", []):
        start_sec = float(event.get("start_sec", 0))
        end_sec = float(event.get("end_sec", 0))
        normalized_events.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "count_estimate": int(event.get("count_estimate", 0)),
                "description": str(event.get("description", "")).strip(),
                "thumbnail_url": pick_thumbnail_url(
                    start_sec=start_sec,
                    end_sec=end_sec,
                    evidence_windows=evidence_windows,
                ),
            }
        )

    return {
        "estimated_total_bags_unloaded": int(
            bag_report.get("estimated_total_bags_unloaded", 0)
        ),
        "count_confidence": str(bag_report.get("count_confidence", "low")).strip() or "low",
        "events": normalized_events,
        "notes": str(bag_report.get("notes", "")).strip(),
    }


def normalize_productivity_report(productivity_report: dict[str, Any]) -> dict[str, Any]:
    workers = []
    idle_workers = 0

    for worker in productivity_report.get("workers", []):
        idle_segments = normalize_segment_list(
            list(worker.get("idle_segments", [])),
            reason_key="reason",
        )
        active_segments = normalize_segment_list(
            list(worker.get("active_segments", [])),
            reason_key="activity",
        )

        idle_seconds = float(worker.get("idle_seconds_estimate", sum_segment_duration(idle_segments)))
        active_seconds = float(
            worker.get("active_seconds_estimate", sum_segment_duration(active_segments))
        )
        productivity_score, productivity_percent = compute_productivity_metrics(
            active_seconds=active_seconds,
            idle_seconds=idle_seconds,
        )
        status = infer_worker_status(productivity_score)
        if status == "idle":
            idle_workers += 1

        workers.append(
            {
                "worker_tag": str(worker.get("worker_tag", "")).strip(),
                "appearance_summary": str(worker.get("appearance_summary", "")).strip(),
                "idle_seconds_estimate": round(idle_seconds, 2),
                "active_seconds_estimate": round(active_seconds, 2),
                "productivity_score": productivity_score,
                "productivity_percent": productivity_percent,
                "status": status,
                "idle_segments": idle_segments,
                "active_segments": active_segments,
            }
        )

    observed_worker_count = int(productivity_report.get("observed_worker_count", len(workers)))
    summary = str(productivity_report.get("summary", "")).strip()
    if not summary:
        summary = (
            f"Observed {observed_worker_count} workers. "
            f"{idle_workers} mostly idle and {max(observed_worker_count - idle_workers, 0)} active or mixed."
        )

    return {
        "observed_worker_count": observed_worker_count,
        "workers": workers,
        "summary": summary,
        "notes": str(productivity_report.get("notes", "")).strip(),
    }


def normalize_theft_report(
    theft_report: dict[str, Any],
    evidence_windows: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_incidents = []
    for incident in theft_report.get("incidents", []):
        start_sec = float(incident.get("start_sec", 0))
        end_sec = float(incident.get("end_sec", 0))
        confidence = str(incident.get("confidence", "low")).strip().lower() or "low"
        normalized_incidents.append(
            {
                "worker_tag": str(incident.get("worker_tag", "")).strip(),
                "start_sec": start_sec,
                "end_sec": end_sec,
                "evidence_timestamp_sec": float(
                    incident.get("evidence_timestamp_sec", start_sec)
                ),
                "item_description": str(incident.get("item_description", "")).strip(),
                "suspected_quantity": int(incident.get("suspected_quantity", 0)),
                "reason": str(incident.get("reason", "")).strip(),
                "confidence": confidence,
                "thumbnail_url": pick_thumbnail_url(
                    start_sec=start_sec,
                    end_sec=end_sec,
                    evidence_windows=evidence_windows,
                ),
            }
        )

    confident_incidents = [
        incident
        for incident in normalized_incidents
        if incident["confidence"] == "high"
    ]
    theft_detected = bool(theft_report.get("theft_detected")) and bool(confident_incidents)

    return {
        "theft_detected": theft_detected,
        "suspected_incident_count": len(confident_incidents) if theft_detected else 0,
        "incidents": confident_incidents if theft_detected else [],
        "notes": str(theft_report.get("notes", "")).strip(),
    }


def filter_hits_for_video(
    hits: list[dict[str, Any]],
    *,
    video_id: str,
) -> list[dict[str, Any]]:
    filtered = [hit for hit in hits if hit.get("video_id") in {None, "", video_id}]
    return filtered if filtered else hits


class TwelveLabsWarehouseMonitoringService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        poll_interval_seconds: int = 5,
        timeout_seconds: int = 120,
        rate_limit_retries: int = DEFAULT_RATE_LIMIT_RETRIES,
    ) -> None:
        resolved_key = (
            api_key
            or os.getenv("TWELVE_LABS_API_KEY")
            or os.getenv("TWELVELABS_API_KEY")
        )
        if not resolved_key:
            raise ValueError(
                "Missing Twelve Labs API key. Set TWELVE_LABS_API_KEY or TWELVELABS_API_KEY."
            )

        self.base_url = base_url.rstrip("/")
        self.poll_interval_seconds = poll_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.rate_limit_retries = rate_limit_retries
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": resolved_key})

    def index_video_from_path(
        self,
        *,
        file_path: str,
        index_name_prefix: str,
    ) -> dict[str, Any]:
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)

        index_name = self.build_index_name(index_name_prefix)
        index_id = self.create_index(index_name=index_name)
        asset = self.create_asset(file_path=file_path)
        ready_asset = self.wait_for_asset(asset_id=asset["_id"])
        indexed_asset = self.create_indexed_asset(index_id=index_id, asset_id=ready_asset["_id"])
        ready_indexed_asset = self.wait_for_indexed_asset(
            index_id=index_id,
            indexed_asset_id=indexed_asset["_id"],
        )
        video_id = ready_indexed_asset.get("video_id") or ready_indexed_asset["_id"]
        return {
            "index_id": index_id,
            "index_name": index_name,
            "asset_id": ready_asset["_id"],
            "indexed_asset_id": ready_indexed_asset["_id"],
            "video_id": video_id,
            "status": ready_indexed_asset.get("status", "ready"),
            "completion_basis": "indexed_asset_ready",
            "ready_for_search": True,
            "upstream_status": {
                "asset": ready_asset.get("status", "unknown"),
                "indexed_asset": ready_indexed_asset.get("status", "unknown"),
            },
            "system_metadata": ready_indexed_asset.get("system_metadata", {}),
        }

    def analyze_video(
        self,
        *,
        index_id: str,
        video_id: str,
    ) -> dict[str, Any]:
        import concurrent.futures

        def _bag():
            return self.run_structured_analysis(
                video_id=video_id,
                prompt=self.bag_prompt(),
                schema=self.bag_schema(),
                max_tokens=2048,
            )

        def _productivity():
            return self.run_structured_analysis(
                video_id=video_id,
                prompt=self.productivity_prompt(),
                schema=self.productivity_schema(),
                max_tokens=2048,
            )

        def _theft():
            return self.run_structured_analysis(
                video_id=video_id,
                prompt=self.theft_prompt(),
                schema=self.theft_schema(),
                max_tokens=2048,
            )

        def _marengo():
            return self.collect_search_evidence(index_id=index_id, video_id=video_id)

        # Run all 4 calls simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            fut_bag = executor.submit(_bag)
            fut_prod = executor.submit(_productivity)
            fut_theft = executor.submit(_theft)
            fut_marengo = executor.submit(_marengo)

            raw_bag = fut_bag.result()
            raw_prod = fut_prod.result()
            raw_theft = fut_theft.result()
            marengo_evidence = fut_marengo.result()

        bag_report = normalize_bag_report(raw_bag, marengo_evidence["bag_unloading"])
        productivity_report = normalize_productivity_report(raw_prod)
        theft_report = normalize_theft_report(raw_theft, marengo_evidence["possible_theft"])

        return {
            "analysis_generated_at": utc_now(),
            "index_id": index_id,
            "video_id": video_id,
            "bag_unloading": bag_report,
            "worker_productivity": productivity_report,
            "theft_detection": theft_report,
            "marengo_evidence": marengo_evidence,
            "disclaimer": (
                "Worker idleness, productivity, and theft outputs are model-generated "
                "observations and should be reviewed before being treated as final proof."
            ),
        }

    def create_index(self, *, index_name: str) -> str:
        payload = {
            "index_name": index_name,
            "models": [
                {
                    "model_name": "marengo3.0",
                    "model_options": ["visual", "audio"],
                },
                {
                    "model_name": "pegasus1.2",
                    "model_options": ["visual", "audio"],
                },
            ],
            "addons": ["thumbnail"],
        }
        response = self.request("POST", "/indexes", expected_codes={201}, json=payload)
        index_id = response.json().get("_id")
        if not index_id:
            raise TwelveLabsAPIError("Index creation succeeded but no index ID was returned.")
        return index_id

    def create_asset(self, *, file_path: str) -> dict[str, Any]:
        file_size = os.path.getsize(file_path)
        if file_size > MAX_UPLOAD_BYTES:
            raise ValueError("Upload exceeds the 200MB direct-upload limit.")

        with open(file_path, "rb") as file_handle:
            response = self.request(
                "POST",
                "/assets",
                expected_codes={201},
                data={"method": "direct", "filename": os.path.basename(file_path)},
                files={
                    "file": (
                        os.path.basename(file_path),
                        file_handle,
                        "application/octet-stream",
                    )
                },
            )
        payload = response.json()
        if not payload.get("_id"):
            raise TwelveLabsAPIError("Asset upload succeeded but no asset ID was returned.")
        return payload

    def wait_for_asset(self, *, asset_id: str) -> dict[str, Any]:
        while True:
            response = self.request("GET", f"/assets/{asset_id}", expected_codes={200})
            payload = response.json()
            status = payload.get("status")
            if status == "ready":
                return payload
            if status == "failed":
                raise TwelveLabsAPIError(f"Asset upload failed for asset '{asset_id}'.")
            time.sleep(self.poll_interval_seconds)

    def create_indexed_asset(self, *, index_id: str, asset_id: str) -> dict[str, Any]:
        response = self.request(
            "POST",
            f"/indexes/{index_id}/indexed-assets",
            expected_codes={201, 202},
            json={"asset_id": asset_id},
        )
        payload = response.json()
        if not payload.get("_id"):
            raise TwelveLabsAPIError(
                "Indexed asset creation succeeded but no indexed asset ID was returned."
            )
        return payload

    def wait_for_indexed_asset(self, *, index_id: str, indexed_asset_id: str) -> dict[str, Any]:
        while True:
            response = self.request(
                "GET",
                f"/indexes/{index_id}/indexed-assets/{indexed_asset_id}",
                expected_codes={200},
            )
            payload = response.json()
            status = payload.get("status")
            if status == "ready":
                return payload
            if status == "failed":
                raise TwelveLabsAPIError(
                    f"Indexing failed for indexed asset '{indexed_asset_id}'."
                )
            time.sleep(self.poll_interval_seconds)

    def search(
        self,
        *,
        index_id: str,
        query_text: str,
        search_options: list[str],
        transcription_options: list[str] | None = None,
        threshold: str = "medium",
        page_limit: int = 6,
    ) -> list[dict[str, Any]]:
        files: list[tuple[str, tuple[None, str]]] = [
            ("index_id", (None, index_id)),
            ("query_text", (None, query_text)),
            ("threshold", (None, threshold)),
            ("page_limit", (None, str(page_limit))),
        ]
        for option in search_options:
            files.append(("search_options", (None, option)))
        for option in transcription_options or []:
            files.append(("transcription_options", (None, option)))

        response = self.request(
            "POST",
            "/search",
            expected_codes={200},
            files=files,
        )
        return response.json().get("data", [])

    def run_structured_analysis(
        self,
        *,
        video_id: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]:
        response = self.request(
            "POST",
            "/analyze",
            expected_codes={200},
            json={
                "video_id": video_id,
                "prompt": prompt,
                "temperature": 0.1,
                "stream": False,
                "max_tokens": max_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": schema,
                },
            },
        )
        payload = response.json()
        if payload.get("finish_reason") == "length":
            # Try to parse partial data instead of hard-failing.
            # Twelve Labs may still return a valid (though truncated) JSON object.
            try:
                return extract_analysis_payload(payload.get("data"))
            except Exception:
                raise TwelveLabsAPIError(
                    "Structured analysis was truncated and the partial payload could not be parsed. "
                    "Consider shortening the video clip or simplifying the analysis schema."
                )
        return extract_analysis_payload(payload.get("data"))

    def collect_search_evidence(
        self,
        *,
        index_id: str,
        video_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        presets = [
            SearchPreset(
                name="bag_unloading",
                queries=[
                    "goods being unloaded from the back of a truck",
                    "workers carrying sacks, boxes, or inventory from truck to warehouse",
                    "count each sack, box, or goods item that clearly leaves the truck",
                ],
                search_options=["visual", "audio"],
            ),
            SearchPreset(
                name="worker_idle",
                queries=[
                    "worker standing still and not helping with unloading",
                    "worker waiting while other workers carry goods",
                    "person near the truck with no active loading or unloading task",
                ],
                search_options=["visual", "audio"],
            ),
            SearchPreset(
                name="possible_theft",
                queries=[
                    "worker carrying goods away from the normal unloading area",
                    "person removes inventory from the warehouse workflow without returning",
                    "possible theft or unauthorized removal of goods",
                ],
                search_options=["visual", "audio"],
            ),
        ]

        evidence: dict[str, list[dict[str, Any]]] = {}
        for preset in presets:
            category_hits: list[dict[str, Any]] = []
            for query in preset.queries:
                hits = self.search(
                    index_id=index_id,
                    query_text=query,
                    search_options=preset.search_options,
                    transcription_options=preset.transcription_options,
                    threshold=preset.threshold,
                    page_limit=preset.page_limit,
                )
                for hit in filter_hits_for_video(hits, video_id=video_id):
                    category_hits.append(
                        {
                            "query": query,
                            "start_sec": float(hit.get("start", 0)),
                            "end_sec": float(hit.get("end", 0)),
                            "score": hit.get("score"),
                            "confidence": hit.get("confidence"),
                            "thumbnail_url": hit.get("thumbnail_url"),
                            "video_id": hit.get("video_id"),
                        }
                    )
            evidence[preset.name] = self.deduplicate_hits(category_hits)
        return evidence

    def request(
        self,
        method: str,
        path: str,
        *,
        expected_codes: set[int],
        **kwargs: Any,
    ) -> requests.Response:
        for attempt in range(self.rate_limit_retries + 1):
            response = self.session.request(
                method=method,
                url=f"{self.base_url}{path}",
                timeout=self.timeout_seconds,
                **kwargs,
            )
            if response.status_code in expected_codes:
                return response

            if response.status_code == 429 and attempt < self.rate_limit_retries:
                retry_delay = self.retry_delay_seconds(response)
                if retry_delay is not None:
                    time.sleep(retry_delay)
                    continue

            raise TwelveLabsAPIError(
                f"Twelve Labs request failed for {method} {path}: "
                f"{response.status_code} {response.text.strip()}"
            )

        raise TwelveLabsAPIError(
            f"Twelve Labs request failed for {method} {path}: exhausted rate limit retries."
        )

    @staticmethod
    def retry_delay_seconds(response: requests.Response) -> float | None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 1.0)
            except ValueError:
                pass

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        message = str(payload.get("message", "")).strip()
        if not message:
            return None

        match = re.search(r"after (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", message)
        if not match:
            return None

        reset_at = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        return max((reset_at - datetime.now(timezone.utc)).total_seconds() + 1.0, 1.0)

    @staticmethod
    def deduplicate_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduplicated: list[dict[str, Any]] = []
        seen: set[tuple[Any, Any, Any]] = set()
        for hit in sorted(hits, key=lambda item: (item["start_sec"], item["end_sec"])):
            key = (hit["query"], hit["start_sec"], hit["end_sec"])
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(hit)
        return deduplicated

    @staticmethod
    def build_index_name(prefix: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{sanitize_index_prefix(prefix)}-{timestamp}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def bag_prompt() -> str:
        return (
            "Review this warehouse clip and count only goods or inventory items that clearly leave the truck, "
            "such as sacks, bags, boxes, cartons, or other carried items. Do not count humans. "
            "Be conservative and avoid guessing. Return chronological events with timestamps in seconds. "
            "If visibility is poor, keep counts low and explain uncertainty in notes."
        )

    @staticmethod
    def productivity_prompt() -> str:
        return (
            "Review this warehouse unloading clip and assign anonymous tags like worker_1, worker_2. "
            "A worker is idle only when visibly standing, waiting, or lingering without helping the workflow. "
            "A worker is active when carrying goods, moving inventory, or directly assisting unloading. "
            "Estimate idle and active total time in seconds. "
            "Return at most 5 idle segments and 5 active segments per worker with timestamps. "
            "Be concise — summarise rather than listing every moment."
        )

    @staticmethod
    def theft_prompt() -> str:
        return (
            "Review this warehouse clip for possible theft or suspicious removal of goods. "
            "Be highly conservative. Only report an incident when there is strong direct visual evidence that a person "
            "clearly removes goods from the normal unloading flow and carries them away from the monitored work area "
            "without any visible return during the clip. "
            "Do not infer theft from missing paperwork, lack of supervision, unusual behavior, or incomplete context. "
            "Do not report theft when direction of movement is unclear, when goods may be handed to another worker, "
            "when the person may still be participating in unloading, or when the item later returns to the work area. "
            "If the evidence is ambiguous or only moderately suspicious, return theft_detected=false and no incidents."
        )

    @staticmethod
    def bag_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "estimated_total_bags_unloaded": {"type": "integer"},
                "count_confidence": {"type": "string"},
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_sec": {"type": "number"},
                            "end_sec": {"type": "number"},
                            "count_estimate": {"type": "integer"},
                            "description": {"type": "string"},
                        },
                        "required": [
                            "start_sec",
                            "end_sec",
                            "count_estimate",
                            "description",
                        ],
                    },
                },
                "notes": {"type": "string"},
            },
            "required": [
                "estimated_total_bags_unloaded",
                "count_confidence",
                "events",
                "notes",
            ],
        }

    @staticmethod
    def productivity_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "observed_worker_count": {"type": "integer"},
                "workers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "worker_tag": {"type": "string"},
                            "idle_seconds_estimate": {"type": "number"},
                            "active_seconds_estimate": {"type": "number"},
                            "idle_segments": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "start_sec": {"type": "number"},
                                        "end_sec": {"type": "number"},
                                        "reason": {"type": "string"},
                                    },
                                    "required": ["start_sec", "end_sec", "reason"],
                                },
                            },
                            "active_segments": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "start_sec": {"type": "number"},
                                        "end_sec": {"type": "number"},
                                        "activity": {"type": "string"},
                                    },
                                    "required": ["start_sec", "end_sec", "activity"],
                                },
                            },
                        },
                        "required": [
                            "worker_tag",
                            "idle_seconds_estimate",
                            "active_seconds_estimate",
                            "idle_segments",
                            "active_segments",
                        ],
                    },
                },
                "summary": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["observed_worker_count", "workers", "summary", "notes"],
        }

    @staticmethod
    def theft_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "theft_detected": {"type": "boolean"},
                "incidents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "worker_tag": {"type": "string"},
                            "start_sec": {"type": "number"},
                            "end_sec": {"type": "number"},
                            "evidence_timestamp_sec": {"type": "number"},
                            "item_description": {"type": "string"},
                            "suspected_quantity": {"type": "integer"},
                            "reason": {"type": "string"},
                            "confidence": {"type": "string"},
                        },
                        "required": [
                            "worker_tag",
                            "start_sec",
                            "end_sec",
                            "evidence_timestamp_sec",
                            "item_description",
                            "suspected_quantity",
                            "reason",
                            "confidence",
                        ],
                    },
                },
                "notes": {"type": "string"},
            },
            "required": ["theft_detected", "incidents", "notes"],
        }


def run_index_job(
    *,
    job_id: str,
    file_path: str,
    index_name_prefix: str,
    delete_after: bool,
) -> None:
    try:
        JOB_STORE.update(job_id, status="running")
        result = build_service().index_video_from_path(
            file_path=file_path,
            index_name_prefix=index_name_prefix,
        )
        JOB_STORE.update(job_id, status="completed", result=result)
    except Exception as exc:
        traceback.print_exc()
        JOB_STORE.update(
            job_id,
            status="failed",
            error={"type": exc.__class__.__name__, "message": str(exc)},
        )
    finally:
        if delete_after:
            path = Path(file_path)
            if path.exists():
                path.unlink()


def run_analysis_job(
    *,
    job_id: str,
    index_id: str,
    video_id: str,
) -> None:
    try:
        JOB_STORE.update(job_id, status="running")
        result = build_service().analyze_video(index_id=index_id, video_id=video_id)
        JOB_STORE.update(job_id, status="completed", result=result)
    except Exception as exc:
        traceback.print_exc()
        JOB_STORE.update(
            job_id,
            status="failed",
            error={"type": exc.__class__.__name__, "message": str(exc)},
        )


async def save_upload_to_temp(upload: UploadFile) -> str:
    ensure_upload_directory()
    safe_name = sanitize_filename(upload.filename)
    temp_path = UPLOAD_DIRECTORY / f"{uuid.uuid4().hex}_{safe_name}"
    total_bytes = 0

    with temp_path.open("wb") as file_handle:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_BYTES:
                file_handle.close()
                temp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail="Uploaded file exceeds the 200MB limit.",
                )
            file_handle.write(chunk)

    await upload.close()
    return str(temp_path)


def get_job_or_404(job_id: str, expected_job_type: str) -> dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job or job.get("job_type") != expected_job_type:
        raise HTTPException(status_code=404, detail=f"Unknown {expected_job_type} job.")
    return job


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "VisionGuard Warehouse Monitoring API is running. Access /docs for swagger UI."}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/warehouse-monitoring/index-jobs", status_code=202)
async def create_index_job(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("application/json"):
        try:
            payload = IndexJobRequest.model_validate(await request.json())
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        file_path = payload.file_path
        if not os.path.isfile(file_path):
            raise HTTPException(
                status_code=400,
                detail=f"file_path does not exist on the backend: {file_path}",
            )
        index_name_prefix = sanitize_index_prefix(payload.index_name_prefix)
        delete_after = False
        input_payload = {"file_path": file_path, "index_name_prefix": index_name_prefix}
    elif content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        file_path_field = form.get("file_path")
        if upload and file_path_field:
            if isinstance(upload, (UploadFile, StarletteUploadFile)):
                await upload.close()
            raise HTTPException(
                status_code=400,
                detail="Provide either a multipart file or a JSON file_path, not both.",
            )
        if not isinstance(upload, (UploadFile, StarletteUploadFile)):
            raise HTTPException(
                status_code=400,
                detail="Multipart requests must include a file field named 'file'.",
            )
        file_path = await save_upload_to_temp(upload)
        index_name_prefix = sanitize_index_prefix(str(form.get("index_name_prefix", DEFAULT_INDEX_PREFIX)))
        delete_after = True
        input_payload = {
            "filename": sanitize_filename(upload.filename),
            "index_name_prefix": index_name_prefix,
        }
    else:
        raise HTTPException(
            status_code=415,
            detail="Use application/json with file_path or multipart/form-data with file.",
        )

    job_id = JOB_STORE.create(job_type="index", input_payload=input_payload)
    background_tasks.add_task(
        start_background_job,
        run_index_job,
        job_id=job_id,
        file_path=file_path,
        index_name_prefix=index_name_prefix,
        delete_after=delete_after,
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/warehouse-monitoring/index-jobs/{job_id}",
    }


@app.get("/warehouse-monitoring/index-jobs/{job_id}")
def get_index_job(job_id: str) -> dict[str, Any]:
    return get_job_or_404(job_id, "index")


@app.post("/warehouse-monitoring/analysis-jobs", status_code=202)
def create_analysis_job(
    payload: AnalysisJobRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    job_id = JOB_STORE.create(
        job_type="analysis",
        input_payload={"index_id": payload.index_id, "video_id": payload.video_id},
    )
    background_tasks.add_task(
        start_background_job,
        run_analysis_job,
        job_id=job_id,
        index_id=payload.index_id,
        video_id=payload.video_id,
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/warehouse-monitoring/analysis-jobs/{job_id}",
    }


@app.get("/warehouse-monitoring/analysis-jobs/{job_id}")
def get_analysis_job(job_id: str) -> dict[str, Any]:
    return get_job_or_404(job_id, "analysis")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "warehouse_monitoring_api:app",
        host=os.getenv("WAREHOUSE_API_HOST", "127.0.0.1"),
        port=int(os.getenv("WAREHOUSE_API_PORT", "8080")),
        reload=False,
    )
