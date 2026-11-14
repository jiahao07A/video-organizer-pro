"""失败诊断业务入口通过真实 SDK/HTTP 的契约与缓存验证。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.analysis_recovery import RecoveryContext, RecoveryStore, effective_policy
from tests.support.failure_center import service as service


def test_diagnosis_sends_text_parts_and_persists_cache(service, tmp_path):
    requests = []
    invalid_requests = []
    diagnosis = {"cause": "临时服务超时", "confidence": 0.8, "evidence": "HTTP 503",
                 "actions": ["稍后重试"], "retry_options": {"concurrency": 1}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            valid = self.path == "/v1/chat/completions"
            for message in payload.get("messages", []):
                content = message.get("content")
                if isinstance(content, list):
                    valid = valid and bool(content) and all(
                        isinstance(part, dict) and part.get("type") == "text"
                        and isinstance(part.get("text"), str) for part in content
                    )
            user_content = payload["messages"][1]["content"]
            valid = valid and isinstance(user_content, list)
            if valid:
                status = 200
                body = {"id": "local-diagnosis", "object": "chat.completion", "created": 1,
                        "model": "diagnosis", "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(diagnosis, ensure_ascii=False)}}]}
            else:
                invalid_requests.append(payload)
                status = 400
                body = {"error": {"message": "content array requires type/text objects", "type": "invalid_request_error"}}
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        for provider in service.settings["model_providers"]:
            provider["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
        path = str(tmp_path / "synthetic.mp4")
        service.db.upsert_video({"path": path, "filename": "synthetic.mp4", "status": "failed"})
        store = RecoveryStore(service.db)
        context = RecoveryContext(path, service.settings, effective_policy(service.settings)[0], store)
        raw = "synthetic timeout secret-main data:image/jpeg;base64,QUJDREVGR0g= C:/private/client/clip.mp4"
        context.last_error = {"status_code": 503, "message": raw, "task_key": "video_classification",
                              "provider_id": "main", "model": "vision-main"}
        store.finish_failure(context, raw)

        # 不替换失败查询、诊断门面、网关或 SDK，直接由持久化失败记录发起。
        first = service.diagnose_analysis_failures(paths=[path])
        assert first["total"] == first["succeeded"] == 1
        assert first["failed"] == 0
        assert invalid_requests == []
        assert len(requests) == 1
        sent = json.loads(requests[0]["messages"][1]["content"][0]["text"])
        assert requests[0]["model"] == "diagnosis"
        assert sent["category"] == "network"
        assert sent["stage"] == "video_classification"
        assert sent["status_code"] == 503
        assert sent["provider_id"] == "main"
        assert sent["model"] == "vision-main"
        serialized = json.dumps(sent)
        for secret in ("secret-main", "C:/private/client/clip.mp4", "data:image", "QUJDREVGR0g="):
            assert secret not in serialized
        assert "timeout" in sent["message"]
        assert "503" in sent["evidence"]
        assert "<PATH>" in serialized and "<IMAGE_REDACTED>" in serialized

        cache = service.db.execute_query("SELECT result FROM analysis_failure_diagnoses")
        assert len(cache) == 1
        cached = json.loads(cache[0]["result"])
        assert cached["cause"] == diagnosis["cause"]
        assert cached["actions"] == diagnosis["actions"]
        second = service.diagnose_analysis_failures(paths=[path])
        assert second["groups"] == first["groups"]
        assert second["succeeded"] == 1
        assert len(requests) == 1, "缓存复用不应发出第二次收费诊断请求"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert not worker.is_alive()
