

import base64
import json
import re

import requests

from core.loggingx import build_logger

log = build_logger("miloto.ext.origimg")

def locate_button(image_path: str, vision_url: str, vision_key: str,
                  model: str, button_hint: str, timeout: int = 25) -> tuple | None:

    try:
        from PIL import Image
        with Image.open(image_path) as im:
            w, h = im.size
    except Exception as exc:
        log.warning(f"读截图尺寸失败: {exc}")
        return None

    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception:
        return None

    prompt = (
        f"这张图是微信电脑版窗口截图。请在图中找到「{button_hint}」的精确位置。"
        f"只回答一个 JSON，不要任何解释：{{\"x\": <0到1之间的小数，按钮中心横坐标占比>, "
        f"\"y\": <0到1之间的小数，按钮中心纵坐标占比>}}"
    )
    payload = {
        "model": model or "vision",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if vision_key:
        headers["Authorization"] = f"Bearer {vision_key}"

    url = vision_url.rstrip("/") + "/v1/chat/completions"
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        content = _extract_text(data)
    except Exception as exc:
        log.warning(f"视觉定位请求失败: {exc}")
        return None

    coord = _parse_coord(content)
    if coord is None:
        log.warning("视觉模型未返回坐标，将走经验坐标回退")
        return None
    px = int(coord[0] * w)
    py = int(coord[1] * h)
    return (px, py)

def _extract_text(data: dict) -> str:

    try:
        if "choices" in data and data["choices"]:
            c = data["choices"][0]
            if isinstance(c, dict):
                if "message" in c and "content" in c["message"]:
                    return c["message"]["content"] or ""
                if "text" in c:
                    return c["text"] or ""
        if "content" in data:
            return str(data["content"])
    except Exception:
        pass
    return ""

def _parse_coord(text: str):

    try:
        m = re.search(r'\{\s*"x"\s*:\s*([0-9.]+)\s*,\s*"y"\s*:\s*([0-9.]+)\s*\}', text)
        if m:
            return (float(m.group(1)), float(m.group(2)))
        m = re.search(r'x\s*=\s*([0-9.]+)\s*,?\s*y\s*=\s*([0-9.]+)', text)
        if m:
            return (float(m.group(1)), float(m.group(2)))
    except Exception:
        pass
    return None
