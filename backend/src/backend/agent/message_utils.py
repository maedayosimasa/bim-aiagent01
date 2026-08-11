"""AIMessageからテキスト本文を取り出す共通ヘルパー。

Claude(langchain-anthropic)のレスポンスはcontentがstrの場合と、
text/thinking等のブロックのlistの場合の両方がありうるため、
agent/service.pyとagent/report_graph.pyの両方でこの抽出ロジックを共有する。
"""

from langchain_core.messages import AIMessage


def message_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    if isinstance(message.content, list):
        return "".join(
            block.get("text", "")
            for block in message.content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""
