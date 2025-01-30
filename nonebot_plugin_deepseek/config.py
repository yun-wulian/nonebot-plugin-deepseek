import json
from pathlib import Path

from pydantic import Field, BaseModel
import nonebot_plugin_localstore as store
from nonebot import logger, get_plugin_config


class ModelConfig:
    def __init__(self) -> None:
        self.file: Path = store.get_plugin_config_dir() / "config.json"
        self.default_model: str = config.get_enable_models()[0]
        self.default_prompt: str = config.prompt  # 暂时用不到
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
        config_data = {
            "default_model": self.default_model,
            "default_prompt": self.default_prompt,
        }
        with open(self.file, "w") as f:
            json.dump(config_data, f, indent=2)
        self.load()


class CustomModel(BaseModel):
    name: str
    """Model Name"""
    base_url: str = "https://api.deepseek.com"
    """Custom base URL for this model (optional)"""


class ScopedConfig(BaseModel):
    api_key: str = ""
    """Your API Key from deepseek"""
    enable_models: list[CustomModel] = [
        CustomModel(name="deepseek-chat"),
        CustomModel(name="deepseek-reasoner"),
    ]
    """List of models configurations"""
    prompt: str = ""
    """Character Preset"""
    md_to_pic: bool = False
    """Text to Image"""
    enable_send_thinking: bool = False
    """Whether to send model thinking chain"""

    def get_enable_models(self) -> list[str]:
        return [model.name for model in self.enable_models]

    def get_model_url(self, model_name: str) -> str:
        """Get the base_url corresponding to the model"""
        for model in self.enable_models:
            if model.name == model_name:
                return model.base_url
        raise ValueError(f"Model {model_name} not enabled")


class Config(BaseModel):
    deepseek: ScopedConfig = Field(default_factory=ScopedConfig)
    """DeepSeek Plugin Config"""


config = (get_plugin_config(Config)).deepseek
model_config = ModelConfig()
logger.debug(f"load deepseek model: {config.get_enable_models()}")
