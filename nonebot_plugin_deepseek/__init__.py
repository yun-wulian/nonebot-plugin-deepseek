from dataclasses import asdict
from importlib.util import find_spec
import asyncio
from typing import Optional

import httpx
from nonebot import require, logger, on_message
from nonebot.adapters import Event,Bot
from nonebot.params import Depends
from nonebot.matcher import Matcher
from nonebot.permission import User, SuperUser, Permission
from nonebot.plugin import PluginMetadata, inherit_supported_adapters
from nonebot.adapters.onebot.v11 import PrivateMessageEvent

require("nonebot_plugin_waiter")
require("nonebot_plugin_alconna")
require("nonebot_plugin_localstore")
from arclet.alconna import config as alc_config
from nonebot_plugin_waiter import Waiter, prompt
from nonebot_plugin_alconna.uniseg import UniMessage
from nonebot_plugin_alconna.builtins.extensions.reply import ReplyMergeExtension
from nonebot_plugin_alconna import (
    Args,
    Field,
    Match,
    Query,
    Option,
    Alconna,
    MultiVar,
    Namespace,
    Subcommand,
    CommandMeta,
    on_alconna,
)

from .apis import API
from . import hook as hook
from .function_call import registry
from .exception import RequestException
from .extension import CleanDocExtension
from .utils import extract_content_and_think
from .config import Config, config, model_config

current_user: Optional[str] = None
lock = asyncio.Lock()

__plugin_meta__ = PluginMetadata(
    name="DeepSeek",
    description="接入 DeepSeek 模型，提供智能对话与问答功能",
    usage="/deepseek -h",
    type="application",
    config=Config,
    homepage="https://github.com/KomoriDev/nonebot-plugin-deepseek",
    supported_adapters=inherit_supported_adapters("nonebot_plugin_alconna"),
    extra={
        "unique_name": "DeepSeek",
        "author": "Komorebi <mute231010@gmail.com>",
        "version": "0.1.5",
    },
)

if not config.md_to_pic:
    is_to_pic = False

ns = Namespace("deepseek", disable_builtin_options=set())
alc_config.namespaces["deepseek"] = ns

deepseek = on_alconna(
    Alconna(
        "todeepseek",
        Args["content?#内容", MultiVar("str")],
        Option(
            "--use-model",
            Args[
                "model#模型名称",
                config.get_enable_models(),
                Field(completion=lambda: f"请输入模型名，预期为：{config.get_enable_models()} 其中之一"),
            ],
            help_text="指定模型",
        ),
        Option("--with-context", help_text="启用多轮对话"),
        Option("--force-stop", help_text="强制中断当前对话"),
        Subcommand("--balance", help_text="查看余额"),
        Subcommand(
            "model",
            Option("-l|--list", help_text="支持的模型列表"),
            Option(
                "--set-default",
                Args[
                    "model#模型名称",
                    config.get_enable_models(),
                    Field(completion=lambda: f"请输入模型名，预期为：{config.get_enable_models()} 其中之一"),
                ],
                dest="set",
                help_text="设置默认模型",
            ),
            help_text="模型相关设置",
        ),
        namespace=alc_config.namespaces["deepseek"],
        meta=CommandMeta(
            description=__plugin_meta__.description,
            usage=__plugin_meta__.usage,
        ),
    ),
    priority=100,
    use_cmd_start=True,
    skip_for_unmatch=False,
    comp_config={"lite": True},
    extensions=[ReplyMergeExtension, CleanDocExtension],
)

deepseek.shortcut("霞", {"command": "todeepseek --with-context","fuzzy": True,"prefix": False})
deepseek.shortcut("中止", {"command": "todeepseek --force-stop","fuzzy": False,"prefix": True})
deepseek.shortcut("查询余额", {"command": "todeepseek --balance", "fuzzy": False, "prefix": True})
deepseek.shortcut("模型列表", {"command": "todeepseek model --list", "fuzzy": False, "prefix": True})
deepseek.shortcut("设置默认模型", {"command": "todeepseek model --set-default", "fuzzy": True, "prefix": True})

# 提取核心处理逻辑为独立函数
async def handle_chat_core(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    content: Match[tuple[str, ...]],
    model_name: Query[str],
    is_superuser: bool
):
    global current_user
    
    user_id = event.get_user_id()
    async with lock:
        if current_user and current_user != user_id:
            await bot.send(event, "接口占用中，请稍后再试", at_sender=True)
            return
        current_user = user_id
    
    try:
        if not content.available:
            resp = await prompt("你想对 DeepSeek 说什么呢？", timeout=60)
            if resp is None:
                await matcher.finish("等待超时")
            text = resp.extract_plain_text()
            if text in ["结束", "取消", "done"]:
                await matcher.finish("已结束对话")
            chat_content = text
        else:
            chat_content = " ".join(content.result)

        if not model_name.available:
            model_name.result = model_config.default_model

        message = []
        if system_prompt := (config.prompt + (config.sub_prompt if is_superuser and config.sub_prompt else "")):
            message.append({"role": "system", "content": system_prompt})
        #logger.info(f"Applied system prompt: {system_prompt}")
        message.append({"role": "user", "content": chat_content})
        logger.info(message)

        try:
            def handler(e: Event):
                text = e.get_plaintext().strip().lower()
                if text in ["结束", "取消", "done"]:
                    logger.info("已结束对话")
                    return False
                return text
            logger.info("已进入多轮对话")
            permission = Permission(User.from_event(event, perm=matcher.permission))
            waiter = Waiter(waits=["message"], handler=handler, matcher=deepseek, permission=permission)
            waiter.future.set_result("")

            async for resp in waiter(default=False):
                async with lock:
                    if current_user != user_id:
                        break
                
                if resp is False:
                    await bot.send(event, "好的，再见！（微笑地挥手）", at_sender=True)
                    await matcher.finish()
                
                if resp and isinstance(resp, str):
                    # 添加用户消息
                    message.append({"role": "user", "content": resp})
                
                # 调用API
                completion = await API.chat(message, model=model_name.result)
                result = completion.choices[0].message
                ds_content, ds_think = extract_content_and_think(result)
                logger.info(ds_think)
                # 添加助手消息
                assistant_message = {
                    "role": "assistant",
                    "content": ds_content,
                }
                if result.tool_calls:
                    assistant_message["tool_calls"] = result.tool_calls
                message.append(assistant_message)
                # 处理工具调用
                if result.tool_calls:
                    fc_result = await registry.execute_tool_call(result.tool_calls[0])
                    message.append({
                        "role": "tool",
                        "tool_call_id": result.tool_calls[0].id,
                        "content": fc_result,
                    })
                    continue

                # 发送回复
                output = ds_content if ds_content else "error:未获取到有效回复"
                await bot.send(event, output, at_sender=True)

        except httpx.ReadTimeout:
            await bot.send(event, "请求超时，请重试", at_sender=True)
        except RequestException as e:
            await matcher.finish(str(e))
        finally:
            async with lock:
                if current_user == user_id:
                    current_user = None
    except Exception as e:
        async with lock:
            if current_user == user_id:
                current_user = None
        raise e

@deepseek.assign("force-stop")
async def force_stop(
    bot: Bot,
    event: Event,
    is_superuser: bool = Depends(SuperUser())
):
    if not is_superuser:
        return
    global current_user
    async with lock:
        if current_user:
            target_user = current_user
            current_user = None  # 清空占用状态
            await bot.send(event, f"已强制中断用户 {target_user} 的对话")
        else:
            await bot.send(event, "当前没有进行中的对话")
    await deepseek.finish()

@deepseek.assign("balance")
async def _(is_superuser: bool = Depends(SuperUser())):
    if not is_superuser:
        return
    try:
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
    except ValueError as e:
        await deepseek.finish(str(e))


@deepseek.assign("model.list")
async def _():
    model_list = "\n".join(
        f"- {model}（默认）" if model == model_config.default_model else f"- {model}"
        for model in config.get_enable_models()
    )
    message = (
        f"支持的模型列表: \n{model_list}\n"
        "输入 `/deepseek [内容] --use-model [模型名]` 单次选择模型\n"
        "输入 `/deepseek model --set-default [模型名]` 设置默认模型"
    )
    await deepseek.finish(message)


@deepseek.assign("model.set")
async def _(
    is_superuser: bool = Depends(SuperUser()),
    model: Query[str] = Query("model.set.model"),
):
    if not is_superuser:
        return
    model_config.default_model = model.result
    model_config.save()
    await deepseek.finish(f"已设置默认模型为：{model.result}")


@deepseek.handle()
async def _(
    bot:Bot,
    event: Event,
    matcher: Matcher,
    content: Match[tuple[str, ...]],
    model_name: Query[str] = Query("use-model.model"),
    is_superuser: bool = Depends(SuperUser())
):
    logger.info("已触发群聊对话")
    await handle_chat_core(
        bot=bot,
        event=event,
        matcher=matcher,
        content=content,
        model_name=model_name,
        is_superuser=is_superuser
    )

private_matcher = on_message(priority=99, block=False)  # 设置优先级低于命令处理器
@private_matcher.handle()
async def handle_private_chat(event: PrivateMessageEvent, matcher: Matcher, bot: Bot,is_superuser: bool = Depends(SuperUser())):
    # 确保是私聊且消息非命令触发
    if not isinstance(event, PrivateMessageEvent) or event.get_plaintext().startswith("/") or not is_superuser:
        return
    logger.info("已触发私聊对话")
    # 直接调用原有处理逻辑
    await handle_chat_core(
        bot=bot,
        event=event,
        matcher=matcher,
        content=Match(result=(event.get_plaintext().strip(),),available=True),
        model_name=Query(""),  # 使用默认模型
        is_superuser=is_superuser
    )