import inspect
from typing import Union

from nonebot_plugin_alconna.uniseg import UniMessage
from nonebot_plugin_alconna.extension import Extension
from nonebot.internal.adapter import Bot, Event, Message


class CleanDocExtension(Extension):
    @property
    def priority(self) -> int:
        return 15

    @property
    def id(self) -> str:
        return "CleanDoc"

    async def send_wrapper(
        self, bot: Bot, event: Event, send: Union[str, Message, UniMessage]
    ) -> str:
        plain_text = (
            send.extract_plain_text()
            if isinstance(send, Union[Message, UniMessage])
            else send
        )
        return inspect.cleandoc(plain_text)
