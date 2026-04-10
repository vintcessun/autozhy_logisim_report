from src.utils.config_loader import ConfigManager
from pathlib import Path

def test_config_loading():
    """验证配置加载是否成功"""
    config_path = Path("config/config.toml")
    # 确保主路径存在
    if not config_path.exists():
        return
        
    config = ConfigManager.load_config(config_path)
    
    assert config.gemini.model_flash is not None
    assert config.gemini.model_pro is not None
    assert config.paths.logisim_path == "3rd/Logisim-ITA.exe"
    assert config.paths.bin_7z_path == "3rd/7z.exe"
