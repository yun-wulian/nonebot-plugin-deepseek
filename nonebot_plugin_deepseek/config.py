from nonebot import get_plugin_config
from pydantic import Field, BaseModel


class ScopedConfig(BaseModel):
    base_url: str = "https://api.deepseek.com"
    """API Base url"""
    api_key: str = ""
    """Your API Key from deepseek"""
    prompt: str = "You are a helpful assistant."
    """Character Preset"""
    md_to_pic: bool = False
    """Text to Image"""


class Config(BaseModel):
    deepseek: ScopedConfig = Field(default_factory=ScopedConfig)
    """DeepSeek Plugin Confige"""


config = (get_plugin_config(Config)).deepseek
