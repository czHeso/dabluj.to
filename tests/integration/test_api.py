"""HTTP API and WebSocket behaviour, including the localhost security guards."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dabuj.api.app import create_app
from dabuj.api.security import allowed_origins
from dabuj.api.server import find_free_port
from dabuj.application.context import AppContext
from dabuj.jobs.manager import JobKind, JobManager
from dabuj.pipeline.progress import JobStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def jobs() -> Iterator[JobManager]:
    manager = JobManager(concurrency=1)
    yield manager
    manager.shutdown(wait=2.0)


@pytest.fixture
def client(context: AppContext, jobs: JobManager) -> Iterator[TestClient]:
    """A client that looks like a real browser on loopback.

    ``base_url`` matters: TestClient defaults to ``http://testserver``, which
    the DNS-rebinding guard correctly rejects. Pointing it at 127.0.0.1 makes
    the tests exercise the same path a real browser takes.
    """
    app = create_app(context, jobs=jobs)
    with TestClient(app, base_url="http://127.0.0.1:7860") as test_client:
        yield test_client


@pytest.fixture
def project_id(client: TestClient, tone_wav: Path) -> str:
    response = client.post("/api/projects", json={"source_path": str(tone_wav), "name": "Test"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestSystem:
    def test_health(self, client: TestClient) -> None:
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_system_report(self, client: TestClient) -> None:
        payload = client.get("/api/system").json()

        assert payload["system"]["cpu"]["name"]
        assert payload["recommendation"]["profile"] in ("low", "balanced", "high", "ultra")
        assert payload["privacy"]["telemetry"] is False

    def test_checks(self, client: TestClient) -> None:
        payload = client.get("/api/system/checks").json()
        assert any(check["name"] == "FFmpeg" for check in payload["checks"])

    def test_settings_report_local_only_by_default(self, client: TestClient) -> None:
        payload = client.get("/api/settings").json()

        assert payload["privacy"]["local_only"] is True
        assert payload["privacy"]["telemetry"] is False


class TestSecurity:
    def test_a_foreign_origin_is_blocked(self, client: TestClient) -> None:
        """A malicious page must not be able to drive the local API."""
        response = client.get("/api/health", headers={"Origin": "https://evil.example"})

        assert response.status_code == 403
        assert response.json()["code"] == "cross_origin_blocked"

    def test_the_apps_own_origin_is_allowed(self, client: TestClient) -> None:
        response = client.get("/api/health", headers={"Origin": "http://127.0.0.1:7860"})
        assert response.status_code == 200

    def test_dns_rebinding_is_blocked(self, client: TestClient) -> None:
        """A rebound request arrives with the attacker's Host header."""
        response = client.get("/api/health", headers={"Host": "evil.example"})

        assert response.status_code == 421
        assert response.json()["code"] == "bad_host"

    def test_no_wildcard_cors(self, client: TestClient) -> None:
        response = client.get("/api/health", headers={"Origin": "http://localhost:7860"})
        assert response.headers.get("access-control-allow-origin") != "*"

    def test_allowed_origins_never_include_a_wildcard(self) -> None:
        assert "*" not in allowed_origins("127.0.0.1", 7860)

    def test_the_effective_port_is_allowed_not_the_configured_one(
        self, context: AppContext, jobs: JobManager
    ) -> None:
        """Regression: `--port` and auto-port fallback must not block the UI.

        The server may listen on a port other than the configured preference.
        The browser then sends that real port in its Origin header, so an
        allow-list built from the configured port rejects the app's own UI.
        """
        app = create_app(context, jobs=jobs, port=7871)
        with TestClient(app, base_url="http://127.0.0.1:7871") as client:
            response = client.get("/api/health", headers={"Origin": "http://127.0.0.1:7871"})
            assert response.status_code == 200

    def test_a_relative_source_path_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/projects", json={"source_path": "relative.wav"})

        assert response.status_code == 400
        assert "absolute path" in response.json()["summary"].lower()

    def test_a_nonexistent_source_path_is_rejected(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        response = client.post("/api/projects", json={"source_path": str(tmp_path / "nope.wav")})
        assert response.status_code == 400


class TestErrors:
    def test_errors_use_one_shape(self, client: TestClient) -> None:
        payload = client.get("/api/projects/no-such-project").json()

        assert set(payload) >= {"code", "summary", "suggestions"}
        assert payload["code"] == "not_found"

    def test_unknown_model_is_404(self, client: TestClient) -> None:
        assert client.get("/api/models/no-such-model").status_code == 404

    def test_unknown_export_format_is_400(self, client: TestClient, project_id: str) -> None:
        response = client.post(f"/api/projects/{project_id}/export", json={"format": "docx"})
        assert response.status_code == 400


class TestModels:
    def test_catalog_is_listed_with_licences(self, client: TestClient) -> None:
        models = client.get("/api/models").json()["models"]

        assert models
        assert all(model["license"] for model in models)
        assert all(model["installed"] is False for model in models)

    def test_filter_by_task(self, client: TestClient) -> None:
        models = client.get("/api/models", params={"task": "asr"}).json()["models"]
        assert all(model["task"] == "asr" for model in models)


class TestProjects:
    def test_create_and_fetch(self, client: TestClient, project_id: str) -> None:
        payload = client.get(f"/api/projects/{project_id}").json()

        assert payload["name"] == "Test"
        assert payload["schema_version"] >= 1

    def test_listing(self, client: TestClient, project_id: str) -> None:
        projects = client.get("/api/projects").json()["projects"]
        assert [project["id"] for project in projects] == [project_id]

    def test_delete(self, client: TestClient, project_id: str) -> None:
        assert client.delete(f"/api/projects/{project_id}").status_code == 200
        assert client.get(f"/api/projects/{project_id}").status_code == 404


class TestTranscriptEditing:
    @pytest.fixture
    def project_with_transcript(
        self, context: AppContext, client: TestClient, project_id: str
    ) -> str:
        from dabuj.domain.transcript import Segment, Transcript

        project = context.projects.open(project_id)
        project.document.transcript = Transcript(
            segments=[
                Segment(id="seg_1", start=0.0, end=2.0, raw_text="Hello world"),
                Segment(id="seg_2", start=2.0, end=4.0, raw_text="Second segment"),
            ],
            language="en",
        )
        context.projects.save(project)
        return project_id

    def test_editing_preserves_the_original(
        self, client: TestClient, project_with_transcript: str
    ) -> None:
        response = client.patch(
            f"/api/projects/{project_with_transcript}/segments/seg_1",
            json={"text": "Hello there"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["edited_text"] == "Hello there"
        assert payload["raw_text"] == "Hello world"

    def test_edit_persists(self, client: TestClient, project_with_transcript: str) -> None:
        client.patch(
            f"/api/projects/{project_with_transcript}/segments/seg_1",
            json={"text": "Persisted"},
        )
        transcript = client.get(f"/api/projects/{project_with_transcript}/transcript").json()[
            "transcript"
        ]

        assert transcript["segments"][0]["edited_text"] == "Persisted"

    def test_split(self, client: TestClient, project_with_transcript: str) -> None:
        response = client.post(
            f"/api/projects/{project_with_transcript}/segments/seg_1/split",
            json={"timestamp": 1.0},
        )

        assert response.status_code == 200
        assert len(response.json()["segments"]) == 3

    def test_split_outside_the_segment_is_rejected(
        self, client: TestClient, project_with_transcript: str
    ) -> None:
        response = client.post(
            f"/api/projects/{project_with_transcript}/segments/seg_1/split",
            json={"timestamp": 99.0},
        )
        assert response.status_code == 400

    def test_merge(self, client: TestClient, project_with_transcript: str) -> None:
        response = client.post(
            f"/api/projects/{project_with_transcript}/segments/merge",
            json={"first_id": "seg_1", "second_id": "seg_2"},
        )

        assert response.status_code == 200
        assert len(response.json()["segments"]) == 1

    def test_end_before_start_is_rejected(
        self, client: TestClient, project_with_transcript: str
    ) -> None:
        response = client.patch(
            f"/api/projects/{project_with_transcript}/segments/seg_1",
            json={"start": 5.0, "end": 1.0},
        )
        assert response.status_code == 400

    def test_unknown_segment_is_404(self, client: TestClient, project_with_transcript: str) -> None:
        response = client.patch(
            f"/api/projects/{project_with_transcript}/segments/seg_nope",
            json={"text": "x"},
        )
        assert response.status_code == 404

    def test_rename_speaker(self, client: TestClient, project_with_transcript: str) -> None:
        response = client.patch(
            f"/api/projects/{project_with_transcript}/speakers/SPEAKER_00",
            json={"display_name": "Anna"},
        )

        assert response.status_code == 200
        assert response.json()["display_name"] == "Anna"

    def test_export_after_editing(self, client: TestClient, project_with_transcript: str) -> None:
        client.patch(
            f"/api/projects/{project_with_transcript}/segments/seg_1",
            json={"text": "Edited text"},
        )
        response = client.post(
            f"/api/projects/{project_with_transcript}/export", json={"format": "srt"}
        )

        assert response.status_code == 200
        assert Path(response.json()["path"]).read_text(encoding="utf-8").count("Edited text")

    def test_export_without_a_transcript_is_a_clear_error(
        self, client: TestClient, project_id: str
    ) -> None:
        response = client.post(f"/api/projects/{project_id}/export", json={"format": "srt"})

        assert response.status_code == 400
        assert "no transcript" in response.json()["summary"].lower()


class TestJobs:
    def test_jobs_start_empty(self, client: TestClient) -> None:
        assert client.get("/api/jobs").json()["jobs"] == []

    def test_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/nope").status_code == 404

    def test_a_submitted_job_runs_and_completes(self, jobs: JobManager) -> None:
        jobs.start()
        job = jobs.submit(JobKind.TRANSCRIBE, "test", lambda reporter, token: "done")

        _wait_for_terminal(jobs, job.id)

        assert jobs.get(job.id).status is JobStatus.COMPLETED
        assert jobs.get(job.id).result == "done"

    def test_a_failing_job_is_recorded_not_crashed(self, jobs: JobManager) -> None:
        jobs.start()

        def _boom(reporter, token):  # type: ignore[no-untyped-def]
            raise RuntimeError("kaboom")

        job = jobs.submit(JobKind.TRANSCRIBE, "test", _boom)
        _wait_for_terminal(jobs, job.id)

        finished = jobs.get(job.id)
        assert finished.status is JobStatus.FAILED
        assert finished.error is not None
        assert "kaboom" in finished.error["reason"]

    def test_the_worker_survives_a_failed_job(self, jobs: JobManager) -> None:
        jobs.start()

        def _boom(reporter, token):  # type: ignore[no-untyped-def]
            raise RuntimeError("kaboom")

        first = jobs.submit(JobKind.TRANSCRIBE, "bad", _boom)
        _wait_for_terminal(jobs, first.id)

        second = jobs.submit(JobKind.TRANSCRIBE, "good", lambda r, t: "ok")
        _wait_for_terminal(jobs, second.id)

        assert jobs.get(second.id).status is JobStatus.COMPLETED

    def test_cancelling_a_queued_job_stops_it_running(self, jobs: JobManager) -> None:
        """It must never start, not merely stop early."""
        ran: list[str] = []

        def _record(reporter, token):  # type: ignore[no-untyped-def]
            ran.append("yes")

        job = jobs.submit(JobKind.TRANSCRIBE, "test", _record)
        jobs.cancel(job.id)
        jobs.start()
        time.sleep(0.4)

        assert jobs.get(job.id).status is JobStatus.CANCELLED
        assert ran == []

    def test_cancelling_a_running_job(self, jobs: JobManager) -> None:
        jobs.start()

        def _long(reporter, token):  # type: ignore[no-untyped-def]
            token.wait(5.0)
            token.raise_if_cancelled("Job")

        job = jobs.submit(JobKind.TRANSCRIBE, "test", _long)
        time.sleep(0.3)
        jobs.cancel(job.id)
        _wait_for_terminal(jobs, job.id)

        assert jobs.get(job.id).status is JobStatus.CANCELLED

    def test_jobs_run_serially_by_default(self, jobs: JobManager) -> None:
        """Two GPU jobs at once would exhaust VRAM."""
        concurrent = 0
        peak = 0

        def _track(reporter, token):  # type: ignore[no-untyped-def]
            nonlocal concurrent, peak
            concurrent += 1
            peak = max(peak, concurrent)
            time.sleep(0.15)
            concurrent -= 1

        jobs.start()
        submitted = [jobs.submit(JobKind.TRANSCRIBE, f"j{i}", _track) for i in range(4)]
        for job in submitted:
            _wait_for_terminal(jobs, job.id)

        assert peak == 1


class TestWebSocket:
    def test_streams_progress_to_completion(self, client: TestClient, jobs: JobManager) -> None:
        from dabuj.pipeline.stages import Stage

        def _work(reporter, token):  # type: ignore[no-untyped-def]
            reporter.job_started()
            reporter.stage_started(Stage.ASR)
            for step in range(3):
                reporter.update(Stage.ASR, fraction=(step + 1) / 3)
                time.sleep(0.05)
            reporter.stage_finished(Stage.ASR)
            return "ok"

        job = jobs.submit(JobKind.TRANSCRIBE, "ws test", _work, stages=(Stage.ASR,))

        with client.websocket_connect(f"/ws/jobs/{job.id}") as socket:
            first = socket.receive_json()
            assert first["type"] == "snapshot"

            messages = [first]
            for _ in range(40):
                message = socket.receive_json()
                messages.append(message)
                if message["type"] == "final":
                    break

        assert messages[-1]["type"] == "final"
        assert messages[-1]["job"]["status"] == "completed"
        assert any(message["type"] == "progress" for message in messages)

    def test_unknown_job_is_rejected(self, client: TestClient) -> None:
        with client.websocket_connect("/ws/jobs/nope") as socket:
            assert socket.receive_json()["code"] == "not_found"


class TestPortSelection:
    def test_finds_the_preferred_port(self) -> None:
        port = find_free_port("127.0.0.1", 7860)
        assert 7860 <= port < 7880

    def test_skips_a_taken_port(self) -> None:
        import socket as socket_module

        with socket_module.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen(1)
            occupied = taken.getsockname()[1]

            assert find_free_port("127.0.0.1", occupied) != occupied


def _wait_for_terminal(jobs: JobManager, job_id: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if jobs.get(job_id).status.is_terminal:
            return
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")
