"""共享隔离测试辅助。"""
import copy
import json
from types import SimpleNamespace
import pytest
from core.model_providers import make_provider
from core.video_organizer_service import DEFAULT_SETTINGS, VideoOrganizerService


class HttpError(Exception):
    def __init__(self, code=400, message="bad request", error_code="bad_input"):
        super().__init__(message)
        self.status_code = code
        self.body = {"error": {"code": error_code}}
        self.request_id = "request-1"

def response():
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
        "category": "Other", "tags": [], "summary": "新分析结果", "tag_groups": {},
    })))])

class Client:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if callable(result):
            result = result()
        if isinstance(result, BaseException):
            raise result
        return result

@pytest.fixture
def service(tmp_path, monkeypatch):
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["model_providers"] = [make_provider(provider_id=name, display_name=name, api_key=f"secret-{name}", base_url=f"https://{name}.invalid/v1") for name in ("main", "backup", "last")]
    settings["current_provider_id"] = "main"
    settings["task_model_routing"] = {"video_classification": {"provider_id": "main", "model": "vision-main"}}
    settings["processing"].update({"max_workers": 2, "enable_audio_transcription": False,
                                   "enable_metadata_injection": False, "auto_sync_xmp_after_analysis": False,
                                   "http400_recovery": {"global": {"delay_seconds": 0, "jitter": 0}}})
    svc = VideoOrganizerService(settings=settings, on_log=lambda _: None,
                                db_path=str(tmp_path / "v.db"), results_json=str(tmp_path / "r.json"), results_csv=str(tmp_path / "r.csv"))
    monkeypatch.setattr(svc.processor, "extract_frames", lambda *a, **kw: {"frames": ["fake-jpeg"], "thumbnail": None, "phash": ""})
    monkeypatch.setattr(svc.processor, "get_file_hash", lambda p: p)
    monkeypatch.setattr(svc, "_save_l3_backup", lambda: None)
    monkeypatch.setattr(svc.file_manager, "save_results_to_csv", lambda *_: None)
    svc._retry_sleeper = lambda _: None
    return svc

def video(service, tmp_path, name="v.mp4", status="pending"):
    path = tmp_path / name
    path.write_bytes(b"video")
    service.db.upsert_video({"path": str(path), "filename": name, "status": status,
                             "tags": ["旧标签"], "category": "旧分类", "summary": "旧摘要"})
    return str(path)

def configure(service, **policy):
    service.settings["processing"]["http400_recovery"]["global"].update(policy)

def clients(service, monkeypatch, **outputs):
    result = {key: Client(values) for key, values in outputs.items()}
    monkeypatch.setattr(service.ai.gateway, "client_for_route", lambda route: result[route["provider_id"]])
    return result

def fallback(name="backup", **values):
    return {"provider_id": name, "model": "vision-" + name, **values}
