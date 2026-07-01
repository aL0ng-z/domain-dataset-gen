from app.services import paddleocr_local_service_manager as manager


def test_running_paddleocr_services_are_reused(monkeypatch):
    seen: list[str] = []

    def fake_is_healthy(base_url):
        seen.append(base_url)
        return True

    monkeypatch.setattr(manager, "_is_healthy", fake_is_healthy)
    monkeypatch.setattr(
        manager,
        "_get_or_start_vlm_process",
        lambda port: (_ for _ in ()).throw(AssertionError("healthy VLM must not be restarted")),
    )
    monkeypatch.setattr(
        manager,
        "_get_or_start_api_process",
        lambda port, vlm_base_url, settings: (_ for _ in ()).throw(
            AssertionError("healthy API must not be restarted")
        ),
    )

    manager.ensure_paddleocr_local_service(
        {"base_url": "http://127.0.0.1:9020/layout-parsing", "vlm_base_url": "http://127.0.0.1:9021"}
    )

    assert seen == ["http://127.0.0.1:9021", "http://127.0.0.1:9020"]


def test_external_paddleocr_service_address_is_not_started_by_platform(monkeypatch):
    monkeypatch.setattr(
        manager,
        "_get_or_start_api_process",
        lambda port, vlm_base_url, settings: (_ for _ in ()).throw(
            AssertionError("external service must not be launched locally")
        ),
    )

    manager.ensure_paddleocr_local_service({"base_url": "http://gpu-server:9020/layout-parsing"})


def test_local_paddleocr_services_are_started_on_first_parse(monkeypatch):
    observed: list[tuple[str, int]] = []
    health_checks = {
        "http://localhost:9021": iter([False, True]),
        "http://localhost:9020": iter([False, True]),
    }

    class FakeProcess:
        def poll(self):
            return None

    def fake_is_healthy(base_url):
        return next(health_checks[base_url])

    monkeypatch.setattr(manager, "_is_healthy", fake_is_healthy)
    monkeypatch.setattr(
        manager,
        "_get_or_start_vlm_process",
        lambda port: observed.append(("vlm", port)) or FakeProcess(),
    )
    monkeypatch.setattr(
        manager,
        "_get_or_start_api_process",
        lambda port, vlm_base_url, settings: observed.append(("api", port)) or FakeProcess(),
    )
    monkeypatch.setattr(manager.time, "sleep", lambda seconds: None)

    manager.ensure_paddleocr_local_service(
        {
            "base_url": "http://localhost:9020/layout-parsing",
            "vlm_base_url": "http://localhost:9021",
            "startup_timeout_seconds": "1",
        }
    )

    assert observed == [("vlm", 9021), ("api", 9020)]


def test_paddleocr_local_service_accepts_api_root_or_layout_endpoint():
    assert manager._managed_local_service("http://localhost:9020", allowed_paths={"", "/", "/layout-parsing"}) == (
        9020,
        "http://localhost:9020",
    )
    assert manager._managed_local_service(
        "http://localhost:9020/layout-parsing", allowed_paths={"", "/", "/layout-parsing"}
    ) == (9020, "http://localhost:9020")
