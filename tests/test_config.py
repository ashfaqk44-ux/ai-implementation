from src.config import Settings


def test_defaults_load_without_any_keys(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "PIXXI_TOKEN", "PIXXI_MODE", "PIXXI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.pixxi_mode == "mock"
    assert s.pixxi_token is None
    assert s.anthropic_api_key is None
    assert s.pixxi_min_read_interval == 2.0  # real API rule: >= 2 s between reads
    assert s.pixxi_page_size <= 500  # real API max page size
    assert s.dormancy_days == 30
    assert s.budget_tolerance == 0.10
    assert s.llm_temperature == 0.0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("PIXXI_BASE_URL", "https://dataapi.pixxicrm.ae")
    monkeypatch.setenv("PIXXI_MODE", "real")
    monkeypatch.setenv("DORMANCY_DAYS", "45")
    s = Settings(_env_file=None)
    assert s.pixxi_base_url == "https://dataapi.pixxicrm.ae"
    assert s.pixxi_mode == "real"
    assert s.dormancy_days == 45


def test_secrets_are_masked(monkeypatch):
    secret = "sk-test-DO-NOT-PRINT"
    for var in ("ANTHROPIC_API_KEY", "PIXXI_TOKEN", "WEBHOOK_SECRET"):
        monkeypatch.setenv(var, secret)
    s = Settings(_env_file=None)
    assert secret not in repr(s)
    assert secret not in str(s)
    assert s.pixxi_token.get_secret_value() == secret
