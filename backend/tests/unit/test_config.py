from riftlab.config import Settings


def test_settings_load_without_riot_key(monkeypatch):
    monkeypatch.delenv("RIFTLAB_RIOT_API_KEY", raising=False)
    settings = Settings()
    assert settings.riot_api_key is None
    assert settings.demo_mode is True


def test_settings_demo_mode_false_when_riot_key_present(monkeypatch):
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "fake-key-for-test")
    settings = Settings()
    assert settings.demo_mode is False


def test_settings_empty_riot_key_env_var_stays_in_demo_mode(monkeypatch):
    """Compose expande ${RIFTLAB_RIOT_API_KEY:-} para string vazia quando a
    variavel nao e definida; isso nao pode ligar o modo REAL sem chave real."""
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "")
    settings = Settings()
    assert settings.riot_api_key is None
    assert settings.demo_mode is True


def test_settings_env_overrides_api_port(monkeypatch):
    monkeypatch.setenv("RIFTLAB_API_PORT", "9001")
    settings = Settings()
    assert settings.api_port == 9001
