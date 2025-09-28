from dataclasses import asdict
from importlib.util import find_spec
import asyncio
from typing import Optional, Dict, Set
import uuid

import httpx
from nonebot import require, logger, on_message
from nonebot.adapters import Event, Bot
from nonebot.params import Depends
from nonebot.matcher import Matcher
from nonebot.permission import User, SuperUser, Permission
from nonebot.plugin import PluginMetadata, inherit_supported_adapters
from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent
from nonebot.rule import to_me

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

# 重构：使用对话会话管理替代单用户锁
active_sessions: Dict[str, Dict] = {}  # session_id -> session_data
user_sessions: Dict[str, Set[str]] = {}  # user_id -> set(session_ids)

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

deepseek.shortcut("爱音", {"command": "todeepseek --with-context","fuzzy": True,"prefix": False})
deepseek.shortcut("anon", {"command": "todeepseek --with-context","fuzzy": True,"prefix": False})
deepseek.shortcut("中止", {"command": "todeepseek --force-stop","fuzzy": False,"prefix": True})
deepseek.shortcut("查询余额", {"command": "todeepseek --balance", "fuzzy": False, "prefix": True})
deepseek.shortcut("模型列表", {"command": "todeepseek model --list", "fuzzy": False, "prefix": True})
deepseek.shortcut("设置默认模型", {"command": "todeepseek model --set-default", "fuzzy": True, "prefix": True})

# 新增图片处理函数
async def process_images(bot: Bot, event: Event) -> list[str]:
    """处理消息中的图片并返回识别文本列表"""
    images = [seg for seg in event.message if seg.type == "image"]
    ocr_texts = []
    
    for img in images:
        try:
            image_url = img.data["url"]
            result = await bot.call_api("ocr_image", image=image_url)
            
            # 处理不同的API返回格式
            if isinstance(result, list):
                # 处理返回的是列表的情况（如错误日志所示）
                texts = [item.get("text", "") for item in result if item.get("text")]
                if texts:
                    ocr_texts.extend(texts)
                    logger.success(f"成功识别图片内容：{texts}")
                else:
                    await bot.send(event, "未识别到图片中的文字", at_sender=True)
            elif isinstance(result, dict) and "texts" in result:
                # 处理返回的是字典且包含texts键的情况
                texts = [t.get("text", "") for t in result["texts"] if t.get("text")]
                if texts:
                    ocr_texts.extend(texts)
                    logger.success(f"成功识别图片内容：{texts}")
                else:
                    await bot.send(event, "未识别到图片中的文字", at_sender=True)
            else:
                # 处理其他未知格式
                logger.error(f"OCR API返回未知格式: {type(result)} - {result}")
                await bot.send(event, "图片识别服务返回未知格式", at_sender=True)
                
        except httpx.ReadTimeout:
            await bot.send(event, "图片识别超时，请重试", at_sender=True)
        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            await bot.send(event, f"图片识别失败：{str(e)}", at_sender=True)
    
    return ocr_texts

def create_session_id(user_id: str) -> str:
    """创建唯一的会话ID"""
    return f"{user_id}_{uuid.uuid4().hex[:8]}"

def register_session(session_id: str, user_id: str, task: asyncio.Task, event: Event):
    """注册会话"""
    active_sessions[session_id] = {
        "task": task,
        "user_id": user_id,
        "event": event,
        "active": True
    }
    if user_id not in user_sessions:
        user_sessions[user_id] = set()
    user_sessions[user_id].add(session_id)

def unregister_session(session_id: str, user_id: str):
    """注销会话"""
    if session_id in active_sessions:
        del active_sessions[session_id]
    if user_id in user_sessions and session_id in user_sessions[user_id]:
        user_sessions[user_id].remove(session_id)
        if not user_sessions[user_id]:
            del user_sessions[user_id]

def mark_session_inactive(session_id: str):
    """标记会话为不活跃（已被中止）"""
    if session_id in active_sessions:
        active_sessions[session_id]["active"] = False

def is_session_active(session_id: str) -> bool:
    """检查会话是否活跃"""
    return session_id in active_sessions and active_sessions[session_id]["active"]

async def cancel_all_sessions():
    """取消所有活跃会话"""
    cancelled_count = 0
    for session_id, session_data in list(active_sessions.items()):
        if session_data["active"]:  # 只取消活跃会话
            mark_session_inactive(session_id)  # 先标记为不活跃
            task = session_data["task"]
            if not task.done():
                task.cancel()
                cancelled_count += 1
    
    return cancelled_count

async def cancel_user_sessions(user_id: str):
    """取消指定用户的所有会话"""
    if user_id not in user_sessions:
        return 0
    
    cancelled_count = 0
    for session_id in list(user_sessions[user_id]):
        if is_session_active(session_id):  # 只取消活跃会话
            mark_session_inactive(session_id)  # 先标记为不活跃
            task = active_sessions[session_id]["task"]
            if not task.done():
                task.cancel()
                cancelled_count += 1
    
    return cancelled_count

@deepseek.assign("force-stop")
async def force_stop(
    bot: Bot,
    event: Event,
    is_superuser: bool = Depends(SuperUser())
):
    user_id = event.get_user_id()
    
    # 先标记所有相关会话为不活跃
    if is_superuser:
        # 超级用户可以中止所有对话
        sessions_to_cancel = list(active_sessions.items())
    else:
        # 普通用户只能中止自己的对话
        sessions_to_cancel = [(sid, data) for sid, data in active_sessions.items() 
                             if data["user_id"] == user_id]
    
    cancelled_count = 0
    for session_id, session_data in sessions_to_cancel:
        if session_data["active"]:
            mark_session_inactive(session_id)
            cancelled_count += 1
    
    # 然后取消任务
    for session_id, session_data in sessions_to_cancel:
        if not session_data["task"].done():
            session_data["task"].cancel()
            try:
                await session_data["task"]
            except asyncio.CancelledError:
                pass
    
    if cancelled_count > 0:
        await bot.send(event, f"已强制中断 {cancelled_count} 个进行中的对话")
    else:
        await bot.send(event, "当前没有进行中的对话")
    
    await deepseek.finish()

async def handle_chat_core(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    content: Match[tuple[str, ...]],
    model_name: Query[str],
    is_superuser: bool
):
    user_id = event.get_user_id()
    session_id = create_session_id(user_id)
    
    # 创建会话任务
    async def chat_task():
        try:
            # 处理图片内容
            text_input = []

            if not content.available:
                # 检查会话是否仍然活跃
                if not is_session_active(session_id):
                    return
                    
                # 使用简单的prompt，但添加取消检查
                try:
                    resp = await asyncio.wait_for(
                        prompt("你想对 DeepSeek 说什么呢？（可以发送图片或文字）"), 
                        timeout=600
                    )
                except asyncio.TimeoutError:
                    if is_session_active(session_id):
                        await matcher.finish("等待超时")
                    return
                    
                if resp is None:
                    if is_session_active(session_id):
                        await matcher.finish("等待超时")
                    return
                    
                # 再次检查会话状态
                if not is_session_active(session_id):
                    return
                    
                text = resp.extract_plain_text()
                if text in ["结束", "取消", "done"]:
                    await matcher.finish("已结束对话")
                text_input.append(text)
            else:
                text_input = list(content.result)

            # 检查会话是否仍然活跃
            if not is_session_active(session_id):
                return
                
            ocr_texts = await process_images(bot, event)
            # 合并文本和图片内容
            combined_content = "\n".join(text_input + ocr_texts)
            if not combined_content.strip():
                await matcher.finish("请输入有效内容或发送包含文字的图片")

            if not model_name.available:
                model_name.result = model_config.default_model

            message = []
            if system_prompt := (config.prompt + (config.sub_prompt if is_superuser and config.sub_prompt else "")):
                message.append({"role": "system", "content": system_prompt})
            message.append({"role": "user", "content": combined_content})
            logger.info(f"完整输入内容：{message}")

            try:
                async def handler(e: Event):
                    # 检查会话是否仍然活跃
                    if not is_session_active(session_id):
                        return False
                    
                    # 处理多轮对话中的图片
                    ocr_texts = await process_images(bot, e)
                    text = e.get_plaintext().strip().lower()
                    
                    if text in ["结束", "取消", "done"] and not ocr_texts:
                        return False
                    
                    combined = "\n".join([text] + ocr_texts)
                    return combined if combined else False

                logger.info("已进入多轮对话")
                permission = Permission(User.from_event(event, perm=matcher.permission))
                waiter = Waiter(waits=["message"], handler=handler, matcher=deepseek, permission=permission)
                waiter.future.set_result("")

                async for resp in waiter(default=False):
                    # 检查会话是否仍然活跃
                    if not is_session_active(session_id):
                        break
                    
                    if resp is False:
                        if is_session_active(session_id):
                            await bot.send(event, "好的，再见！（微笑地挥手）", at_sender=True)
                        break
                    
                    if resp:
                        message.append({"role": "user", "content": resp})
                    
                    # 检查会话是否仍然活跃
                    if not is_session_active(session_id):
                        break
                        
                    completion = await API.chat(message, model=model_name.result)
                    
                    # 检查会话是否仍然活跃（API请求完成后）
                    if not is_session_active(session_id):
                        break
                        
                    result = completion.choices[0].message
                    ds_content, ds_think = extract_content_and_think(result)
                    logger.info(ds_think)

                    assistant_message = {
                        "role": "assistant",
                        "content": ds_content,
                    }
                    if result.tool_calls:
                        assistant_message["tool_calls"] = result.tool_calls
                    message.append(assistant_message)

                    if result.tool_calls:
                        # 检查会话是否仍然活跃
                        if not is_session_active(session_id):
                            break
                            
                        fc_result = await registry.execute_tool_call(result.tool_calls[0])
                        
                        # 检查会话是否仍然活跃（工具调用完成后）
                        if not is_session_active(session_id):
                            break
                            
                        message.append({
                            "role": "tool",
                            "tool_call_id": result.tool_calls[0].id,
                            "content": fc_result,
                        })
                        continue

                    # 检查会话是否仍然活跃
                    if not is_session_active(session_id):
                        break
                        
                    output = ds_content if ds_content else "error:未获取到有效回复"
                    await bot.send(event, output, at_sender=True)

            except httpx.ReadTimeout:
                # 检查会话是否仍然活跃
                if is_session_active(session_id):
                    await bot.send(event, "请求超时，请重试", at_sender=True)
            except RequestException as e:
                # 检查会话是否仍然活跃
                if is_session_active(session_id):
                    await matcher.finish(str(e))
                
        except asyncio.CancelledError:
            logger.info(f"会话 {session_id} 被取消")
            # 不重新抛出，让任务正常结束
        except Exception as e:
            # 检查会话是否仍然活跃
            if is_session_active(session_id):
                # 过滤 FinishedException
                if "FinishedException" not in str(e):
                    logger.error(f"处理出错：{str(e)}")
                    await bot.send(event, f"处理出错：{str(e)}", at_sender=True)
        finally:
            # 清理会话
            unregister_session(session_id, user_id)

    # 创建并注册任务
    task = asyncio.create_task(chat_task())
    register_session(session_id, user_id, task, event)
    
    try:
        await task
    except asyncio.CancelledError:
        # 任务被取消，不需要额外处理
        pass

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
    if event.get_user_id() == "2702043878":
        return
    logger.info("已触发群聊对话")
    await handle_chat_core(
        bot=bot,
        event=event,
        matcher=matcher,
        content=content,
        model_name=model_name,
        is_superuser=is_superuser
    )

# 修复：使用状态标记避免重复触发
processing_events = set()

at_me_matcher = on_message(priority=10, block=False, rule=to_me())  # 提高优先级，避免重复触发

@at_me_matcher.handle()
async def handle_at_me(event: GroupMessageEvent, matcher: Matcher, bot: Bot, is_superuser: bool = Depends(SuperUser())):
    """处理@机器人的消息"""
    if (event.get_user_id() == "2702043878" or 
        event.get_plaintext().startswith("/") or 
        event.get_plaintext().startswith("#")):
        return
    
    # 避免重复处理
    event_key = f"at_me_{event.message_id}"
    if event_key in processing_events:
        return
    processing_events.add(event_key)
    
    try:
        logger.info("已触发at我")
        await handle_chat_core(
            bot=bot,
            event=event,
            matcher=matcher,
            content=Match(result=(event.get_plaintext().strip(),), available=True),
            model_name=Query(""),  # 使用默认模型
            is_superuser=is_superuser
        )
    finally:
        processing_events.discard(event_key)


private_matcher = on_message(priority=5, block=False)  # 降低优先级，避免与命令冲突

@private_matcher.handle()
async def handle_private_chat(event: PrivateMessageEvent, matcher: Matcher, bot: Bot, is_superuser: bool = Depends(SuperUser())):
    """处理私聊消息"""
    if (not isinstance(event, PrivateMessageEvent) or 
        event.get_plaintext().startswith("/") or 
        event.get_plaintext().startswith("#") or 
        not is_superuser):
        return
    
    # 避免重复处理
    event_key = f"private_{event.message_id}"
    if event_key in processing_events:
        return
    processing_events.add(event_key)
    
    try:
        logger.info("已触发私聊对话")
        await handle_chat_core(
            bot=bot,
            event=event,
            matcher=matcher,
            content=Match(result=(event.get_plaintext().strip(),), available=True),
            model_name=Query(""),  # 使用默认模型
            is_superuser=is_superuser
        )
    finally:
        processing_events.discard(event_key)