import json
from pathlib import Path

from nonebot import get_plugin_config
from pydantic import Field, BaseModel
import nonebot_plugin_localstore as store


class ModelConfig:
    def __init__(self) -> None:
        self.file: Path = store.get_plugin_config_dir() / "config.json"
        self.default_model: str = "deepseek-chat"
        self.default_prompt: str = "You are a helpful assistant."  # 暂时用不到
        self.load()

    def load(self):
        if not self.file.exists():
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.save()
            return

        with open(self.file) as f:
            data = json.load(f)
            self.default_model = data.get("default_model", self.default_model)
            self.default_prompt = data.get("default_prompt", self.default_prompt)

    def save(self):
        config_data = {"default_model": self.default_model, "default_prompt": self.default_prompt}
        with open(self.file, "w") as f:
            json.dump(config_data, f, indent=2)
        self.load()


class ScopedConfig(BaseModel):
    base_url: str = "https://api.deepseek.com"
    """API Base url"""
    api_key: str = ""
    """Your API Key from deepseek"""
    enable_models: list[str] = ["deepseek-chat", "deepseek-reasoner"]
    """List of models used"""
    prompt: str = "You are a helpful assistant."
    """Character Preset"""
    md_to_pic: bool = False
    """Text to Image"""


class Config(BaseModel):
    deepseek: ScopedConfig = Field(default_factory=ScopedConfig)
    """DeepSeek Plugin Confige"""


config = (get_plugin_config(Config)).deepseek
model_config = ModelConfig()
