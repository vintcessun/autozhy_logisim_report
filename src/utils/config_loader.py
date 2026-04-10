import tomllib
from pathlib import Path
from pydantic import BaseModel, Field

class GeminiConfig(BaseModel):
    """Gemini API 配置模型"""
    api_key: str
    base_url: str | None = None
    model_pro: str = "gemini-1.5-pro"
    model_flash: str = "gemini-1.5-flash"

class PathConfig(BaseModel):
    """外部工具路径配置"""
    logisim_path: str = "3rd/Logisim-ITA.exe"
    bin_7z_path: str = "3rd/7z.exe"

class OllamaConfig(BaseModel):
    """Ollama 后端配置"""
    endpoint: str = "http://localhost:11434"
    model_name: str = "gemma4:e2b"

class AppConfig(BaseModel):
    """全局应用配置"""
    gemini: GeminiConfig
    paths: PathConfig
    ollama: OllamaConfig

class ConfigManager:
    """配置管理器，使用 PascalCase 命名类"""
    
    @staticmethod
    def load_config(config_path: Path | str = "config/config.toml") -> AppConfig:
        """加载 TOML 配置文件"""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件未找到: {path.absolute()}")
            
        with open(path, "rb") as f:
            data = tomllib.load(f)
            
        return AppConfig(**data)

# 便捷单例访问（可选）
# config = ConfigManager.load_config()
