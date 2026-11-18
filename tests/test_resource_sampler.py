from core.analysis_job_metrics import ResourceSampler, new_job_metrics


def test_resource_sampler_is_stoppable_without_psutil(monkeypatch):
    monkeypatch.setitem(__import__('sys').modules, 'psutil', None)
    tracker = new_job_metrics()
    sampler = ResourceSampler(tracker, interval=0.01).start()
    sampler.stop()
    assert sampler._thread is None
    assert tracker.snapshot()['resource_sampling']['status'] == 'unavailable'


def test_resource_peak_is_monotonic():
    tracker = new_job_metrics()
    tracker.record_resource_peak(rss_mb=10, cpu_percent=20)
    tracker.record_resource_peak(rss_mb=5, cpu_percent=40)
    assert tracker.snapshot()['resource_peak'] == {'rss_mb': 10.0, 'cpu_percent': 40.0}


def test_sampler_failure_stops_thread_and_records_reason(monkeypatch):
    import sys
    import time
    from types import SimpleNamespace

    class FailingProcess:
        def memory_info(self):
            raise RuntimeError('synthetic failure')

        def io_counters(self):
            return SimpleNamespace(read_bytes=0, write_bytes=0)

    tracker = new_job_metrics()
    sampler = ResourceSampler(tracker, interval=0.001)
    sampler._process = FailingProcess()
    sampler._thread = __import__('threading').Thread(target=sampler._run, daemon=True)
    sampler._thread.start()
    sampler._thread.join(1)
    sampler.stop()
    assert not sampler._thread
    assert tracker.snapshot()['resource_sampling'] == {'status': 'unavailable', 'reason': 'RuntimeError'}
