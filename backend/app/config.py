"""统一读取旅游助手的应用和外部服务配置。"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """配置值优先来自环境变量，其次来自 backend/.env，最后使用默认值。"""

    app_name: str = "旅游助手"
    app_version: str = "0.1.0"
    app_debug: bool = False

    llm_model_id: str = ""
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    amap_api_key: SecretStr = SecretStr("")

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()


def get_settings() -> Settings:
    """返回已创建的配置对象。"""
    return settings
