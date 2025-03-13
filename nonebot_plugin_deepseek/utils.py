import re

from .schemas import Message


def extract_content_and_think(message: Message) -> tuple[str, str]:
    thinking = message.reasoning_content

    if not thinking:
        think_blocks = re.findall(r"<think>(.*?)</think>", message.content or "", flags=re.DOTALL)
        thinking = "\n".join([block.strip() for block in think_blocks if block.strip()])

    content = re.sub(r"<think>.*?</think>", "", message.content or "", flags=re.DOTALL).strip()

    return content, thinking
