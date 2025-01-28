from nonebot import get_driver
from nonebot.log import logger
from nonebot_plugin_alconna import command_manager

driver = get_driver()


@driver.on_startup
async def _() -> None:
    command_manager.load_cache()
    logger.debug("DeekSeek shortcuts cache loaded")


@driver.on_shutdown
async def _() -> None:
    command_manager.dump_cache()
    logger.debug("DeekSeek shortcuts cache dumped")
