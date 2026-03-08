import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import warehouse_monitoring_api as api


class MockResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeMonitoringService:
    def index_video_from_path(self, *, file_path, index_name_prefix):
        return {
            "index_id": "index_123",
            "index_name": f"{index_name_prefix}-123",
            "asset_id": "asset_123",
            "indexed_asset_id": "indexed_123",
            "video_id": "video_123",
            "status": "ready",
            "completion_basis": "indexed_asset_ready",
            "ready_for_search": True,
            "upstream_status": {
                "asset": "ready",
                "indexed_asset": "ready",
            },
            "system_metadata": {},
        }

    def analyze_video(self, *, index_id, video_id):
        return {
            "analysis_generated_at": "2026-03-07T00:00:00+00:00",
            "index_id": index_id,
            "video_id": video_id,
            "bag_unloading": {
                "estimated_total_bags_unloaded": 10,
                "count_confidence": "medium",
                "events": [
                    {
                        "start_sec": 1.0,
                        "end_sec": 4.0,
                        "count_estimate": 3,
                        "description": "Three bags leave the truck.",
                        "thumbnail_url": "https://example.com/thumb.jpg",
                    }
                ],
                "notes": "Conservative estimate.",
            },
            "worker_productivity": {
                "observed_worker_count": 1,
                "workers": [
                    {
                        "worker_tag": "worker_1",
                        "appearance_summary": "Blue shirt",
                        "idle_seconds_estimate": 10.0,
                        "active_seconds_estimate": 30.0,
                        "productivity_score": 0.75,
                        "productivity_percent": 75.0,
                        "status": "active",
                        "idle_segments": [],
                        "active_segments": [],
                    }
                ],
                "summary": "Observed 1 worker.",
                "notes": "",
            },
            "theft_detection": {
                "theft_detected": False,
                "suspected_incident_count": 0,
                "incidents": [],
                "notes": "",
            },
            "marengo_evidence": {
                "bag_unloading": [],
                "worker_idle": [],
                "possible_theft": [],
            },
            "disclaimer": "Model-generated observation.",
        }


class ApiEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(api.app)
        api.JOB_STORE = api.InMemoryJobStore()

    def test_json_file_path_validation(self):
        response = self.client.post(
            "/warehouse-monitoring/index-jobs",
            json={"file_path": "/tmp/does-not-exist.mp4"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("file_path does not exist", response.json()["detail"])

    @patch("warehouse_monitoring_api.start_background_job")
    def test_multipart_upload_creates_job(self, start_background_job):
        response = self.client.post(
            "/warehouse-monitoring/index-jobs",
            files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
            data={"index_name_prefix": "Warehouse Demo"},
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertIn("/warehouse-monitoring/index-jobs/", payload["status_url"])
        start_background_job.assert_called_once()

    def test_reject_both_input_modes(self):
        response = self.client.post(
            "/warehouse-monitoring/index-jobs",
            files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
            data={"file_path": "/tmp/clip.mp4"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not both", response.json()["detail"])

    @patch("warehouse_monitoring_api.start_background_job")
    def test_analysis_job_endpoint_creates_job(self, start_background_job):
        response = self.client.post(
            "/warehouse-monitoring/analysis-jobs",
            json={"index_id": "index_1", "video_id": "video_1"},
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertIn("/warehouse-monitoring/analysis-jobs/", payload["status_url"])
        start_background_job.assert_called_once()

    def test_unknown_job_returns_404(self):
        response = self.client.get("/warehouse-monitoring/index-jobs/missing")
        self.assertEqual(response.status_code, 404)

    def test_get_index_job_includes_readiness_metadata(self):
        job_id = api.JOB_STORE.create(
            job_type="index",
            input_payload={"file_path": "/tmp/example.mp4", "index_name_prefix": "warehouse-monitoring"},
        )
        api.JOB_STORE.update(
            job_id,
            status="completed",
            result={
                "index_id": "index_123",
                "index_name": "warehouse-monitoring-123",
                "asset_id": "asset_123",
                "indexed_asset_id": "indexed_123",
                "video_id": "video_123",
                "status": "ready",
                "completion_basis": "indexed_asset_ready",
                "ready_for_search": True,
                "upstream_status": {
                    "asset": "ready",
                    "indexed_asset": "ready",
                },
                "system_metadata": {},
            },
        )

        response = self.client.get(f"/warehouse-monitoring/index-jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["result"]["status"], "ready")
        self.assertEqual(payload["result"]["completion_basis"], "indexed_asset_ready")
        self.assertTrue(payload["result"]["ready_for_search"])
        self.assertEqual(payload["result"]["upstream_status"]["asset"], "ready")
        self.assertEqual(payload["result"]["upstream_status"]["indexed_asset"], "ready")


class HelperFunctionTests(unittest.TestCase):
    def test_productivity_score_calculation(self):
        score, percent = api.compute_productivity_metrics(active_seconds=30, idle_seconds=10)
        self.assertEqual(score, 0.75)
        self.assertEqual(percent, 75.0)

    def test_sanitize_functions(self):
        self.assertEqual(api.sanitize_filename("../clip.mp4"), "clip.mp4")
        self.assertEqual(api.sanitize_index_prefix(" Warehouse Demo!! "), "warehousedemo")

    def test_missing_thumbnail_falls_back_to_none(self):
        normalized = api.normalize_bag_report(
            {
                "estimated_total_bags_unloaded": 2,
                "count_confidence": "medium",
                "events": [
                    {
                        "start_sec": 1,
                        "end_sec": 2,
                        "count_estimate": 2,
                        "description": "Two bags moved",
                    }
                ],
                "notes": "",
            },
            [{"start_sec": 1, "end_sec": 2, "thumbnail_url": None}],
        )
        self.assertIsNone(normalized["events"][0]["thumbnail_url"])

    def test_normalize_productivity_report_builds_summary(self):
        report = api.normalize_productivity_report(
            {
                "observed_worker_count": 1,
                "workers": [
                    {
                        "worker_tag": "worker_1",
                        "appearance_summary": "orange vest",
                        "idle_segments": [{"start_sec": 1, "end_sec": 5, "reason": "waiting"}],
                        "active_segments": [{"start_sec": 5, "end_sec": 15, "activity": "carrying sacks"}],
                    }
                ],
                "summary": "",
                "notes": "Test",
            }
        )
        self.assertEqual(report["workers"][0]["productivity_percent"], 71.43)
        self.assertEqual(report["workers"][0]["status"], "active")
        self.assertIn("Observed 1 workers", report["summary"])

    def test_normalize_theft_report_requires_high_confidence(self):
        report = api.normalize_theft_report(
            {
                "theft_detected": True,
                "incidents": [
                    {
                        "worker_tag": "worker_1",
                        "start_sec": 10,
                        "end_sec": 15,
                        "evidence_timestamp_sec": 12,
                        "item_description": "box",
                        "suspected_quantity": 1,
                        "reason": "Medium-confidence suspicion",
                        "confidence": "medium",
                    },
                    {
                        "worker_tag": "worker_2",
                        "start_sec": 20,
                        "end_sec": 25,
                        "evidence_timestamp_sec": 22,
                        "item_description": "sack",
                        "suspected_quantity": 1,
                        "reason": "Clear removal from work area",
                        "confidence": "high",
                    },
                ],
                "notes": "Test",
            },
            [{"start_sec": 20, "end_sec": 25, "thumbnail_url": "https://example.com/high.jpg"}],
        )
        self.assertTrue(report["theft_detected"])
        self.assertEqual(report["suspected_incident_count"], 1)
        self.assertEqual(len(report["incidents"]), 1)
        self.assertEqual(report["incidents"][0]["confidence"], "high")
        self.assertEqual(report["incidents"][0]["thumbnail_url"], "https://example.com/high.jpg")

    def test_theft_prompt_disallows_context_only_inference(self):
        prompt = api.TwelveLabsWarehouseMonitoringService.theft_prompt()
        self.assertIn("strong direct visual evidence", prompt)
        self.assertIn("Do not infer theft from missing paperwork", prompt)
        self.assertIn("theft_detected=false", prompt)


class JobWorkerTests(unittest.TestCase):
    def setUp(self):
        api.JOB_STORE = api.InMemoryJobStore()

    @patch("warehouse_monitoring_api.build_service", return_value=FakeMonitoringService())
    def test_index_job_lifecycle(self, _build_service):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as handle:
            job_id = api.JOB_STORE.create(
                job_type="index",
                input_payload={"file_path": handle.name},
            )
            api.run_index_job(
                job_id=job_id,
                file_path=handle.name,
                index_name_prefix="warehouse-monitoring",
                delete_after=False,
            )
            job = api.JOB_STORE.get(job_id)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["video_id"], "video_123")
            self.assertEqual(job["result"]["status"], "ready")
            self.assertEqual(job["result"]["completion_basis"], "indexed_asset_ready")
            self.assertTrue(job["result"]["ready_for_search"])
            self.assertEqual(job["result"]["upstream_status"]["asset"], "ready")
            self.assertEqual(job["result"]["upstream_status"]["indexed_asset"], "ready")

    @patch("warehouse_monitoring_api.build_service", return_value=FakeMonitoringService())
    def test_analysis_job_response_shape(self, _build_service):
        job_id = api.JOB_STORE.create(
            job_type="analysis",
            input_payload={"index_id": "index_123", "video_id": "video_123"},
        )
        api.run_analysis_job(job_id=job_id, index_id="index_123", video_id="video_123")
        job = api.JOB_STORE.get(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertIn("bag_unloading", job["result"])
        self.assertIn("worker_productivity", job["result"])
        self.assertIn("theft_detection", job["result"])


class TwelveLabsServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = api.TwelveLabsWarehouseMonitoringService(api_key="test-key", poll_interval_seconds=0)

    def test_extract_analysis_payload_accepts_string_and_dict(self):
        self.assertEqual(api.extract_analysis_payload({"x": 1}), {"x": 1})
        self.assertEqual(api.extract_analysis_payload('{"x": 2}'), {"x": 2})

    def test_successful_indexing_flow(self):
        mock_session = Mock()
        mock_session.request.side_effect = [
            MockResponse(201, {"_id": "index_1"}),
            MockResponse(201, {"_id": "asset_1"}),
            MockResponse(200, {"_id": "asset_1", "status": "ready"}),
            MockResponse(202, {"_id": "indexed_1"}),
            MockResponse(200, {"_id": "indexed_1", "video_id": "video_1", "status": "ready"}),
        ]
        self.service.session = mock_session

        with tempfile.NamedTemporaryFile(suffix=".mp4") as handle:
            handle.write(b"video-bytes")
            handle.flush()
            result = self.service.index_video_from_path(
                file_path=handle.name,
                index_name_prefix="warehouse-monitoring",
            )

        self.assertEqual(result["index_id"], "index_1")
        self.assertEqual(result["video_id"], "video_1")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["completion_basis"], "indexed_asset_ready")
        self.assertTrue(result["ready_for_search"])
        self.assertEqual(result["upstream_status"]["asset"], "ready")
        self.assertEqual(result["upstream_status"]["indexed_asset"], "ready")

    def test_indexing_failure_propagates(self):
        mock_session = Mock()
        mock_session.request.side_effect = [
            MockResponse(201, {"_id": "index_1"}),
            MockResponse(201, {"_id": "asset_1"}),
            MockResponse(200, {"_id": "asset_1", "status": "ready"}),
            MockResponse(202, {"_id": "indexed_1"}),
            MockResponse(200, {"_id": "indexed_1", "status": "failed"}),
        ]
        self.service.session = mock_session

        with tempfile.NamedTemporaryFile(suffix=".mp4") as handle:
            handle.write(b"video-bytes")
            handle.flush()
            with self.assertRaises(api.TwelveLabsAPIError):
                self.service.index_video_from_path(
                    file_path=handle.name,
                    index_name_prefix="warehouse-monitoring",
                )

    @patch("warehouse_monitoring_api.time.sleep")
    def test_request_retries_on_rate_limit_reset_message(self, sleep_mock):
        mock_session = Mock()
        mock_session.request.side_effect = [
            MockResponse(
                429,
                {
                    "code": "too_many_requests",
                    "message": (
                        "You have exceeded the rate limit (8req/1minute). "
                        "Please try again later after 2026-03-07T17:09:36Z."
                    ),
                },
            ),
            MockResponse(200, {"data": []}),
        ]
        self.service.session = mock_session

        with patch("warehouse_monitoring_api.datetime") as datetime_mock:
            datetime_mock.now.return_value = datetime.fromisoformat("2026-03-07T17:09:30+00:00")
            datetime_mock.fromisoformat.side_effect = datetime.fromisoformat
            datetime_mock.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            response = self.service.request(
                "POST",
                "/search",
                expected_codes={200},
                files=[],
            )

        self.assertEqual(response.status_code, 200)
        sleep_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
