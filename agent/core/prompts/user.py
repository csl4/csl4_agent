"""用户消息构造：把用户问句（+可选图片）拼成 user 消息的 content parts（支持多模态）。"""

from typing import Any, Dict, List, Optional


def build_user_prompt(
    user_input: str,
    images: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """构建用户消息，可选择附带图片。

    参数:
        user_input: 用户的文本输入。
        images: 可选的图片字典列表，包含 'url' 或 'base64' 键。

    返回:
        用户消息的 content parts 列表。
    """
    content: List[Dict[str, Any]] = [{"type": "text", "text": user_input}]

    if images:
        for img in images:
            if "url" in img:
                content.append({"type": "image_url", "image_url": {"url": img["url"]}})
            elif "base64" in img:
                data_uri = f"data:image/{img.get('format', 'png')};base64,{img['base64']}"
                content.append({"type": "image_url", "image_url": {"url": data_uri}})

    return content


__all__ = ["build_user_prompt"]
