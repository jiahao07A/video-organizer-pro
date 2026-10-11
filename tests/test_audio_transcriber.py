"""音频转录前处理的文件所有权与清理边界。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import subprocess
import threading

import pytest

from core.ai_gateway import AiRequestGateway
from core.analysis_job_policy import ApiCallFailure
from core.video_organizer_service import AudioTranscriber


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
