"""音频转录前处理的文件所有权与清理边界。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import subprocess
import threading
import shutil

import pytest

from core.ai_gateway import AiRequestGateway
from core.analysis_job_policy import ApiCallFailure
from core.video_organizer_service import AudioTranscriber


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg 来生成并提取实际媒体音轨")
@pytest.mark.parametrize("cancel", [False, True], ids=["resume", "cancel"])
def test_throttled_analysis_submits_audio_once_without_resending_images(tmp_path, monkeypatch, cancel):
    from core.analysis_recovery import RecoveryContext, RecoveryDeferred
    from core.video_organizer_service import SettingsManager, VideoOrganizerService
    from tests.support.review_sdk_server import sdk_server

    monkeypatch.setenv("VIDEO_ORGANIZER_ANALYSIS_LOG_DIR", str(tmp_path / "metrics"))
    video = tmp_path / "sample.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=64x64:rate=4", "-f", "lavfi", "-i", "sine=frequency=300",
                    "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", str(video)], check=True, timeout=20)
    with sdk_server() as (url, calls):
        settings = SettingsManager.deep_copy_defaults()
        settings["api"].update(key="synthetic-review", base_url=url)
        settings["processing"].update(request_interval=30 if cancel else 2, max_workers=1, enable_scene_detection=False,
                                      enable_audio_transcription=True, max_frames=2,
                                      analysis_retry={"call_extra_attempts": 0, "item_max_attempts": 1,
                                                      "batch_rerun_enabled": False})
        service = VideoOrganizerService(settings=settings, db_path=str(tmp_path / "test.db"))
        if cancel:
            # 等到音频真正被节流时，从公开入口取消，不需等待 30 秒。
            before_request = RecoveryContext.before_request
            def cancel_on_audio_wait(context):
                try:
                    return before_request(context)
                except RecoveryDeferred:
                    if context.task_key == "content_description":
                        service.request_cancel_analysis()
                    raise
            monkeypatch.setattr(RecoveryContext, "before_request", cancel_on_audio_wait)
        result = service.run_analysis([str(video)])
        if cancel:
            assert service.is_analysis_cancelled() and result["succeeded"] == 0
            assert [path for path, _ in calls] == ["/v1/chat/completions"]
            return
        assert result["succeeded"] == 1 and result["failed"] == 0
        row = service.db.get_video_by_path(str(video))
        assert row["transcription"] == "合成转录"
        assert [path for path, _ in calls] == ["/v1/chat/completions", "/v1/audio/transcriptions"]
        assert b"whisper-1" in calls[1][1] and b"audio.mp3" in calls[1][1]


def test_missing_ffmpeg_preserves_preexisting_audio(tmp_path, monkeypatch):
    video = tmp_path / "existing.mp4"
    audio = tmp_path / "existing.mp4.mp3"
    video.write_bytes(b"fake video")
    audio.write_bytes(b"existing user audio")

    def missing_ffmpeg(*args, **kwargs):
        raise FileNotFoundError("ffmpeg executable is unavailable")

    monkeypatch.setattr("core.video_organizer_service.subprocess.run", missing_ffmpeg)
    result = AudioTranscriber.transcribe(str(video), gateway=AiRequestGateway({}))

    assert result.startswith("转录失败")
    assert audio.exists()
    assert audio.read_bytes() == b"existing user audio"


@pytest.mark.parametrize("outcome", ["success", "extract-failure", "api-failure", "api-exception"])
def test_transcription_owns_only_its_temporary_audio(tmp_path, monkeypatch, outcome):
    video = tmp_path / "existing.mp4"
    existing_audio = tmp_path / "existing.mp4.mp3"
    video.write_bytes(b"fake video")
    existing_audio.write_bytes(b"existing user audio")
    outputs = []

    def fake_ffmpeg(command, **kwargs):
        if command[1] == "-version":
            return SimpleNamespace(returncode=0)
        output = Path(command[-1])
        assert output != existing_audio
        outputs.append(output)
        output.write_bytes(b"extracted audio")
        if outcome == "extract-failure":
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    class FakeGateway:
        def transcribe_audio(self, audio_path, **kwargs):
            assert Path(audio_path).read_bytes() == b"extracted audio"
            assert kwargs["task_key"] == "content_description"
            assert kwargs["model"] == "whisper-1"
            if outcome == "api-exception":
                raise RuntimeError("fake transcription failure")
            return None if outcome == "api-failure" else "transcript"

        def get_last_failure(self):
            return ApiCallFailure("fake API failure", retriable=False)

    monkeypatch.setattr("core.video_organizer_service.subprocess.run", fake_ffmpeg)
    result = AudioTranscriber.transcribe(str(video), gateway=FakeGateway())

    if outcome == "success":
        assert result == "transcript"
    else:
        assert result.startswith("转录失败")
    assert existing_audio.read_bytes() == b"existing user audio"
    assert len(outputs) == 1
    assert not outputs[0].exists()
    assert not outputs[0].parent.exists()


def test_parallel_transcriptions_use_independent_temporary_audio(tmp_path, monkeypatch):
    video = tmp_path / "parallel.mp4"
    video.write_bytes(b"fake video")
    outputs = []
    both_transcribing = threading.Barrier(2)

    def fake_ffmpeg(command, **kwargs):
        if command[1] != "-version":
            Path(command[-1]).write_bytes(b"extracted audio")
        return SimpleNamespace(returncode=0)

    class FakeGateway:
        def transcribe_audio(self, audio_path, **kwargs):
            output = Path(audio_path)
            outputs.append(output)
            both_transcribing.wait(timeout=5)
            assert output.read_bytes() == b"extracted audio"
            return "transcript"

    monkeypatch.setattr("core.video_organizer_service.subprocess.run", fake_ffmpeg)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: AudioTranscriber.transcribe(str(video), gateway=FakeGateway()), range(2)))

    assert results == ["transcript", "transcript"]
    assert len({p.parent for p in outputs}) == 2
    assert all(not p.parent.exists() for p in outputs)
