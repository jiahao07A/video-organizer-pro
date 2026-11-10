"""共享隔离测试辅助。"""
import pytest
from core.video_organizer_service import SettingsManager, VideoOrganizerService


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr("core.video_organizer_service.TAG_CONFIG_FILE", str(tmp_path / "tags.json"))
    monkeypatch.setattr("core.video_organizer_service.get_settings_file_path", lambda: str(tmp_path / "settings.json"))
    return VideoOrganizerService(settings=SettingsManager.deep_copy_defaults(), db_path=str(tmp_path / "test.db"), on_log=lambda *_: None)
