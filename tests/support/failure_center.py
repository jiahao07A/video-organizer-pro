"""共享隔离测试辅助。"""
import copy
import pytest
from core.model_providers import make_provider
from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService


@pytest.fixture
def service(tmp_path, monkeypatch):
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["model_providers"] = [make_provider(provider_id=name, display_name=name, api_key=f"secret-{name}", base_url=f"https://{name}.invalid/v1") for name in ("main", "backup")]
    settings["current_provider_id"] = "main"
    settings["task_model_routing"] = {"video_classification": {"provider_id": "main", "model": "vision-main"}, "error_diagnosis": {"provider_id": "main", "model": "diagnosis"}}
    settings["processing"].update({"max_workers": 2, "enable_audio_transcription": False, "enable_metadata_injection": False, "auto_sync_xmp_after_analysis": False})
    svc = VideoOrganizerService(settings=settings, on_log=lambda _: None, db_path=str(tmp_path / "v.db"), results_json=str(tmp_path / "r.json"), results_csv=str(tmp_path / "r.csv"))
    monkeypatch.setattr(svc.processor, "extract_frames", lambda *a, **kw: {"frames": ["fake"], "thumbnail": None, "phash": ""})
    monkeypatch.setattr(svc.processor, "get_file_hash", lambda p: p)
    monkeypatch.setattr(svc, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(svc.file_manager, "save_results_to_csv", lambda *_: None)
    return svc
