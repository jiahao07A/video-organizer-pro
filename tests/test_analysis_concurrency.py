"""32条分析管线穿过真实SDK/本地HTTP，观测同时在途而非仅线程数。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from openai import OpenAI

from core.video_organizer_service import SettingsManager, VideoOrganizerService


def test_32_analysis_jobs_reach_http_service_concurrently(tmp_path, monkeypatch):
    lock = threading.Lock()
    release = threading.Event()
    active = 0
    peak = 0
    arrivals = 0

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *_):
            pass
        def do_POST(self):
            nonlocal active, peak, arrivals
            self.rfile.read(int(self.headers['Content-Length']))
            with lock:
                active += 1
                arrivals += 1
                peak = max(peak, active)
                if arrivals == 32:
                    release.set()
            # 首个请求必须等其余31个也进入，串行客户端无法通过。
            all_arrived = release.wait(12)
            data = json.dumps({'id': 'local', 'choices': [{'message': {'content': json.dumps({'category': 'Broll', 'summary': 'ok', 'tags': [], 'tag_groups': {}})}}]}).encode()
            self.send_response(200 if all_arrived else 500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            with lock:
                active -= 1

    class Server(ThreadingHTTPServer):
        request_queue_size = 128
        daemon_threads = True

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = OpenAI(api_key='local-test', base_url=f'http://127.0.0.1:{server.server_port}/v1', max_retries=0, timeout=20)
    try:
        settings = SettingsManager.deep_copy_defaults()
        settings['processing'].update({'max_workers': 32, 'extract_parallel': 4, 'analysis_retry': {'call_extra_attempts': 0, 'item_max_attempts': 1, 'batch_rerun_enabled': False}})
        service = VideoOrganizerService(settings=settings, db_path=str(tmp_path/'analysis.db'), on_log=lambda *_: None)
        paths = []
        for i in range(32):
            path = tmp_path/f'{i}.mp4'
            path.write_bytes(b'synthetic')
            paths.append(str(path))
        service.db.insert_videos_if_absent([{'path': p, 'filename': Path(p).name, 'status': 'pending'} for p in paths])
        monkeypatch.setattr(service.ai.gateway, '_client_for_route', lambda *_: client)
        monkeypatch.setattr(service.processor, 'extract_frames', lambda *_a, **_kw: {'frames': ['synthetic'], 'thumbnail': None, 'phash': ''})
        monkeypatch.setattr(service.ai, 'apply_tag_normalization', lambda data, **_: data)
        monkeypatch.setattr(service.tag_processor, 'process', lambda tags, category: (tags, category))
        result = service.run_analysis(paths, force_reanalyze=True)
        assert result['succeeded'] == 32
        assert arrivals == 32
        assert peak == 32
    finally:
        release.set()
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
