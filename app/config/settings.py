"""Environment-driven application settings."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration with safe local defaults."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "development"
    log_level: str = "INFO"
    runtime_log_file: str = ".runtime/crypto-signal-bot.log"
    runtime_log_max_bytes: int = Field(default=10_485_760, ge=1_048_576)
    runtime_log_backup_count: int = Field(default=10, ge=1, le=100)
    runtime_pid_file: str = ".runtime/crypto-signal-bot.pid"
    runtime_heartbeat_seconds: int = Field(default=30, ge=10)
    runtime_alert_cooldown_seconds: int = Field(default=900, ge=60)
    runtime_websocket_stale_seconds: int = Field(default=120, ge=30)
    runtime_scanner_stale_multiplier: float = Field(default=2.5, ge=1.5, le=10)
    database_url: str = "sqlite+aiosqlite:///./crypto_signal_bot.db"
    redis_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_enabled: bool = False
    binance_private_api_enabled: bool = False
    binance_api_key: str | None = Field(default=None, repr=False)
    binance_api_secret: str | None = Field(default=None, repr=False)
    bybit_private_api_enabled: bool = False
    bybit_api_key: str | None = Field(default=None, repr=False)
    bybit_api_secret: str | None = Field(default=None, repr=False)
    bybit_api_region: str = "STANDARD"
    private_account_refresh_seconds: float = Field(default=30.0, ge=5)
    telegram_admin_ids: Annotated[tuple[int, ...], NoDecode] = ()
    telegram_notification_cooldown_seconds: int = Field(default=300, ge=0)
    telegram_default_language: str = "en"
    telegram_max_message_length: int = Field(default=4096, ge=500, le=4096)
    minimum_confidence: float = Field(default=80.0, ge=0, le=100)
    minimum_24h_volume_usdt: float = Field(default=10_000_000, ge=0)
    maximum_spread_bps: float = Field(default=20.0, gt=0)
    scan_interval_seconds: float = Field(default=30.0, gt=0)
    realtime_update_interval_seconds: float = Field(default=5.0, gt=0)
    symbol_universe_enabled: bool = True
    symbol_universe_refresh_minutes: int = Field(default=15, ge=1)
    symbol_universe_max_per_market: int = Field(default=50, ge=5, le=200)
    symbol_universe_min_24h_quote_volume: float = Field(default=10_000_000, ge=0)
    symbol_universe_max_spread_pct: float = Field(default=0.20, gt=0, le=10)
    symbol_universe_min_listing_age_days: int = Field(default=7, ge=0)
    symbol_universe_quote_asset: str = "USDT"
    symbol_universe_blacklist: Annotated[tuple[str, ...], NoDecode] = ()
    symbol_universe_whitelist: Annotated[tuple[str, ...], NoDecode] = ()
    symbol_universe_include_stable_pairs: bool = False
    directional_scanner_enabled: bool = True
    arbitrage_scanner_enabled: bool = True
    directional_full_scan_minutes: int = Field(default=15, ge=5)
    arbitrage_runtime_max_symbols_per_market: int = Field(default=10, ge=1, le=50)
    ema_periods: Annotated[tuple[int, ...], NoDecode] = (9, 20, 50, 100, 200)
    rsi_period: int = Field(default=14, ge=2)
    macd_fast_period: int = Field(default=12, ge=2)
    macd_slow_period: int = Field(default=26, ge=3)
    macd_signal_period: int = Field(default=9, ge=2)
    atr_period: int = Field(default=14, ge=2)
    adx_period: int = Field(default=14, ge=2)
    bollinger_period: int = Field(default=20, ge=2)
    bollinger_stddev: float = Field(default=2.0, gt=0)
    volume_baseline: int = Field(default=20, ge=3)
    swing_window: int = Field(default=2, ge=1)
    zone_tolerance_atr: float = Field(default=0.5, gt=0)
    breakout_atr_threshold: float = Field(default=0.1, ge=0)
    breakout_persistence: int = Field(default=2, ge=1)
    range_lookback: int = Field(default=20, ge=5)
    range_max_width_atr: float = Field(default=6.0, gt=0)
    volatility_lookback: int = Field(default=100, ge=20)
    signal_weight_trend: float = Field(default=20, ge=0)
    signal_weight_structure: float = Field(default=20, ge=0)
    signal_weight_momentum: float = Field(default=15, ge=0)
    signal_weight_volume: float = Field(default=10, ge=0)
    signal_weight_volatility: float = Field(default=10, ge=0)
    signal_weight_entry: float = Field(default=10, ge=0)
    signal_weight_higher_timeframe: float = Field(default=10, ge=0)
    signal_weight_btc_context: float = Field(default=5, ge=0)
    signal_watchlist_threshold: float = Field(default=70, ge=0, le=100)
    signal_strong_threshold: float = Field(default=80, ge=0, le=100)
    signal_very_strong_threshold: float = Field(default=90, ge=0, le=100)
    signal_stale_after_seconds: int = Field(default=300, ge=1)
    max_entry_extension_atr: float = Field(default=2.0, gt=0)
    stop_atr_buffer: float = Field(default=0.25, ge=0)
    max_stop_distance_atr: float = Field(default=4.0, gt=0)
    minimum_acceptable_r: float = Field(default=1.5, gt=0)
    setup_expiry_minutes: int = Field(default=60, ge=1)
    pending_setup_min_score: float = Field(default=80, ge=0, le=100)
    entry_confirm_min_score: float = Field(default=80, ge=0, le=100)
    max_confidence_drop_before_entry: float = Field(default=8, ge=0, le=100)
    max_entry_spread_bps: float = Field(default=15, gt=0)
    max_entry_age_seconds: int = Field(default=5, ge=1)
    max_distance_from_entry_zone_at_creation_atr: float = Field(default=2, gt=0)
    entry_approaching_distance_atr: float = Field(default=0.5, gt=0)
    revalidation_timeframe: str = "15m"
    signal_update_min_score_delta: float = Field(default=3.0, gt=0)
    signal_deduplication_cooldown_seconds: int = Field(default=900, ge=0)
    scanner_max_concurrency: int = Field(default=4, ge=1, le=50)
    monitoring_enabled: bool = True
    analysis_refresh_mode: str = "CANDLE_CLOSE"
    weakening_score_delta: float = Field(default=8.0, gt=0)
    weakening_min_conditions: int = Field(default=2, ge=1)
    weakening_hysteresis: float = Field(default=3.0, ge=0)
    weakening_cooldown_seconds: int = Field(default=300, ge=0)
    stop_management_policy: str = "STATIC"
    breakeven_buffer_bps: float = Field(default=0.0, ge=0)
    monitoring_stale_quote_ms: int = Field(default=5_000, ge=100)
    event_dedup_window_seconds: int = Field(default=60, ge=0)
    monitor_max_concurrency: int = Field(default=20, ge=1, le=200)
    monitor_reconnect_initial_seconds: float = Field(default=1.0, gt=0)
    monitor_reconnect_max_seconds: float = Field(default=30.0, gt=0)
    target_max_count: int = Field(default=3, ge=1, le=3)
    target_min_spacing_atr: float = Field(default=0.25, gt=0)
    paper_target_allocations: Annotated[tuple[float, ...], NoDecode] = (30, 30, 40)
    paper_default_profile: str = "BALANCED"
    paper_trade_mode: str = "AUTO_PAPER"
    paper_max_concurrent_positions: int = Field(default=10, ge=1)
    paper_futures_leverage: float = Field(default=1.0, ge=1, le=3)
    paper_conservative_exposure_pct: float = Field(default=5, gt=0, le=100)
    paper_balanced_exposure_pct: float = Field(default=10, gt=0, le=100)
    paper_aggressive_exposure_pct: float = Field(default=20, gt=0, le=100)
    paper_entry_slippage_bps: float = Field(default=2, ge=0)
    paper_stop_slippage_bps: float = Field(default=5, ge=0)
    fee_binance_spot_bps: float = Field(default=10, ge=0)
    fee_binance_futures_bps: float = Field(default=5, ge=0)
    fee_bybit_spot_bps: float = Field(default=10, ge=0)
    fee_bybit_futures_bps: float = Field(default=5.5, ge=0)
    arbitrage_target_notionals: Annotated[tuple[float, ...], NoDecode] = (100, 250, 500, 1000)
    arbitrage_safety_buffer_bps: float = Field(default=2, ge=0)
    arbitrage_min_net_edge_bps: float = Field(default=5, ge=0)
    arbitrage_max_slippage_bps: float = Field(default=10, ge=0)
    arbitrage_min_liquidity_ratio: float = Field(default=1.0, gt=0, le=1)
    arbitrage_max_quote_age_ms: int = Field(default=1_000, ge=10)
    arbitrage_max_cross_exchange_skew_ms: int = Field(default=500, ge=0)
    arbitrage_min_consecutive_updates: int = Field(default=3, ge=1)
    arbitrage_min_persistence_ms: int = Field(default=300, ge=0)
    arbitrage_max_temporary_gap_ms: int = Field(default=500, ge=0)
    arbitrage_cooldown_seconds: int = Field(default=30, ge=0)
    arbitrage_funding_interval_hours: int = Field(default=8, ge=1, le=24)
    arbitrage_max_funding_time_skew_minutes: int = Field(default=30, ge=0)
    arbitrage_max_basis_risk_bps: float = Field(default=50, ge=0)
    arbitrage_watchlist_score: float = Field(default=70, ge=0, le=100)
    arbitrage_strong_score: float = Field(default=80, ge=0, le=100)
    arbitrage_very_strong_score: float = Field(default=90, ge=0, le=100)
    arbitrage_update_min_edge_delta_bps: float = Field(default=2, ge=0)
    paper_arbitrage_max_allocation_pct: float = Field(default=10, gt=0, le=100)
    paper_arbitrage_exit_edge_bps: float = Field(default=0, ge=0)
    paper_arbitrage_max_holding_minutes: int = Field(default=60, ge=1)
    enabled_setup_types: Annotated[tuple[str, ...], NoDecode] = (
        "BREAKOUT",
        "RETEST",
        "PULLBACK",
        "TREND_CONTINUATION",
    )
    symbol_blacklist: Annotated[tuple[str, ...], NoDecode] = ()
    symbol_whitelist: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator(
        "symbol_blacklist",
        "symbol_whitelist",
        "symbol_universe_blacklist",
        "symbol_universe_whitelist",
        mode="before",
    )
    @classmethod
    def parse_symbol_list(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip().upper() for item in value.split(",") if item.strip())
        return value

    @field_validator("symbol_universe_quote_asset", mode="before")
    @classmethod
    def normalize_universe_quote(cls, value: object) -> str:
        quote = str(value).strip().upper()
        if not quote:
            raise ValueError("SYMBOL_UNIVERSE_QUOTE_ASSET cannot be empty")
        return quote

    @field_validator("bybit_api_region", mode="before")
    @classmethod
    def validate_bybit_region(cls, value: object) -> str:
        region = str(value).strip().upper()
        aliases = {"GLOBAL": "STANDARD", "EU": "EEA"}
        region = aliases.get(region, region)
        if region not in {"AUTO", "STANDARD", "EEA", "TESTNET", "DEMO"}:
            raise ValueError("BYBIT_API_REGION must be STANDARD, EEA, TESTNET, or DEMO")
        return region

    @field_validator("ema_periods", mode="before")
    @classmethod
    def parse_integer_list(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("paper_target_allocations", mode="before")
    @classmethod
    def parse_float_list(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(float(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("arbitrage_target_notionals", mode="before")
    @classmethod
    def parse_arbitrage_notionals(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(float(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("enabled_setup_types", mode="before")
    @classmethod
    def parse_setup_types(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip().upper() for item in value.split(",") if item.strip())
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""

    return Settings()
