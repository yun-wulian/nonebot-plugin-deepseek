import httpx
from nonebot.log import logger

from ..config import config

# from ..function_call import registry
from ..exception import RequestException
from ..schemas import Balance, ChatCompletions


class API:
    _headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {config.api_key}",
    }

    @classmethod
    async def chat(cls, message: list[dict[str, str]], model: str = "deepseek-chat") -> ChatCompletions:
        """普通对话"""
        json = {
            "messages": [{"content": config.prompt, "role": "user"}] + message if config.prompt else message,
            "model": model,
        }
        logger.debug(f"使用模型 {model}")
        # if model == "deepseek-chat":
        #     json.update({"tools": registry.to_json()})
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{config.get_model_url(model)}/chat/completions",
                headers={**cls._headers, "Content-Type": "application/json"},
                json=json,
                timeout=50,
            )
        if error := response.json().get("error"):
            raise RequestException(error["message"])
        return ChatCompletions(**response.json())

    @classmethod
    async def query_balance(cls) -> Balance:
        """查询账号余额"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{config.get_model_url('deepseek-chat')}/user/balance",
                headers=cls._headers,
            )

        return Balance(**response.json())
