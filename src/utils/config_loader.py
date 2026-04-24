import tomllib
from pathlib import Path
from pydantic import BaseModel, Field


class GeminiConfig(BaseModel):
    """Gemini API 配置模型"""

    api_key: str
    base_url: str | None = None
    model_pro: str = "gemini-1.5-pro"
    model_flash: str = "gemini-1.5-flash"
    timeout_seconds: float = 0


class HeadlessConfig(BaseModel):
    """Headless WebSocket API 配置"""

    port: int = 9924


class AppConfig(BaseModel):
    """全局应用配置"""

    gemini: GeminiConfig
    headless: HeadlessConfig = Field(default_factory=HeadlessConfig)


class ConfigManager:
    """配置管理器"""

    @staticmethod
    def load_config(config_path: Path | str = "config/config.toml") -> AppConfig:
        """加载 TOML 配置文件"""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件未找到: {path.absolute()}")

        with open(path, "rb") as f:
            data = tomllib.load(f)

        return AppConfig(**data)
