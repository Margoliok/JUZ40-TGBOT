from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str | None = None
    database_url: str = "sqlite+aiosqlite:///./data/hr_bot.db"
    admin_username: str = "admin"
    admin_password: str = "admin123"
    session_secret: str = "change-me-in-production"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    superuser_telegram_id: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
