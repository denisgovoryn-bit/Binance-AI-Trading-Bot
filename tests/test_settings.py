from app.config.settings import Settings


def test_comma_separated_symbol_lists_are_normalized() -> None:
    settings = Settings(symbol_blacklist=" pepeusdt, lunausdt ")
    assert settings.symbol_blacklist == ("PEPEUSDT", "LUNAUSDT")


def test_safe_defaults_require_actionable_confidence() -> None:
    settings = Settings()
    assert settings.minimum_confidence == 80
    assert settings.database_url.startswith("sqlite+aiosqlite")


def test_analysis_periods_parse_from_environment_style_strings() -> None:
    settings = Settings(ema_periods="5, 10, 20")
    assert settings.ema_periods == (5, 10, 20)
