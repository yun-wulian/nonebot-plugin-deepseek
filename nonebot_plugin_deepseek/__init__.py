from importlib.util import find_spec

import httpx
from nonebot import require
from nonebot.params import Depends
from nonebot.permission import SuperUser
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

require("nonebot_plugin_waiter")
require("nonebot_plugin_alconna")
from nonebot_plugin_waiter import prompt
from nonebot_plugin_alconna import Match, Command
from nonebot_plugin_alconna.uniseg import UniMessage

if find_spec("nonebot_plugin_htmlrender"):
    require("nonebot_plugin_htmlrender")
    from nonebot_plugin_htmlrender import md_to_pic

    is_to_pic = True
else:
    is_to_pic = False

from .apis import API
from .config import Config, config
from .extension import CleanDocExtension

__plugin_meta__ = PluginMetadata(
    name="DeepSeek",
    description="接入 DeepSeek 模型，提供智能对话与问答功能",
    usage="/deepseek",
    type="application",
    config=Config,
    homepage="https://github.com/KomoriDev/nonebot-plugin-deepseek",
    supported_adapters=inherit_supported_adapters("nonebot_plugin_alconna"),
    extra={
        "unique_name": "DeepSeek",
        "author": "Komorebi <mute231010@gmail.com>",
        "version": "0.1.0",
    },
)

if not config.md_to_pic:
    is_to_pic = False

deepseek = (
    Command("deepseek [...content]")
    .option("--balance")
    .build(use_cmd_start=True, extensions=[CleanDocExtension])
)
deepseek.shortcut(
    "余额",
    {
        "command": "deepseek --balance",
        "fuzzy": False,
        "prefix": True,
    },
)


@deepseek.assign("$main")
async def _(content: Match[UniMessage]):
    if not content.available:
        resp = await prompt("你想对 DeepSeek 说什么呢？", timeout=60)
        if resp is None:
            await deepseek.finish("等待超时")
        chat_content = resp.extract_plain_text()
    else:
        chat_content = content.result.extract_plain_text()

    try:
        completion = await API.chat(chat_content)
        result = completion.choices[0].message.content
        if is_to_pic and result:
            await UniMessage.image(raw=await md_to_pic(result)).finish()  # type: ignore
        else:
            await deepseek.finish(completion.choices[0].message.content)
    except httpx.ReadTimeout:
        await deepseek.finish("网络超时，再试试吧")


@deepseek.assign("balance")
async def _(is_superuser: bool = Depends(SuperUser())):
    if not is_superuser:
        return

    balances = await API.query_balance()

    await deepseek.finish(
        "".join(
            f"""
            货币：{balance.currency}
            总的可用余额: {balance.total_balance}
            未过期的赠金余额: {balance.granted_balance}
            充值余额: {balance.topped_up_balance}
            """
            for balance in balances.balance_infos
        )
    )
