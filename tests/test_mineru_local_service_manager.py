from app.services import mineru_local_service_manager as manager


def test_running_local_service_is_reused(monkeypatch):
    monkeypatch.setattr(manager, "_is_healthy", lambda base_url: True)
    monkeypatch.setattr(
        manager,
        "_get_or_start_process",
        lambda port: (_ for _ in ()).throw(AssertionError("healthy service must not be restarted")),
    )

    manager.ensure_mineru_local_service({"base_url": "http://127.0.0.1:9010"})


def test_remote_service_address_is_not_started_by_platform(monkeypatch):
    monkeypatch.setattr(
        manager,
        "_get_or_start_process",
        lambda port: (_ for _ in ()).throw(AssertionError("external service must not be launched locally")),
    )

    manager.ensure_mineru_local_service({"base_url": "http://gpu-server:9010"})


def test_local_service_is_started_on_first_parse(monkeypatch):
    observed: list[int] = []
    health_checks = iter([False, True])

    class FakeProcess:
        def poll(self):
            return None

    monkeypatch.setattr(manager, "_is_healthy", lambda base_url: next(health_checks))
    monkeypatch.setattr(manager, "_get_or_start_process", lambda port: observed.append(port) or FakeProcess())
    monkeypatch.setattr(manager.time, "sleep", lambda seconds: None)

    manager.ensure_mineru_local_service({"base_url": "http://localhost:9010", "startup_timeout_seconds": "1"})

    assert observed == [9010]


def test_local_service_without_explicit_port_uses_http_default_port():
    assert manager._managed_local_port("http://localhost") == 80
