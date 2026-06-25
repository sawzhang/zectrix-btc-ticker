"""
BTC 实时价格看板 — 推送到 Zectrix 墨水屏
每分钟从 OKX 公开 API 获取行情，生成 400×300 黑白图片推送到设备

依赖：pip install requests Pillow
配置：复制 config.example.json → config.json，填入 API Key 和设备 ID
"""

import io
import json
import time
import datetime
import requests
from PIL import Image, ImageDraw, ImageFont

# ── 从 config.json 读取配置 ────────────────────────────
with open("config.json") as _f:
    _cfg = json.load(_f)

API_KEY   = _cfg["api_key"]
DEVICE_ID = _cfg["device_id"]
PAGE_ID   = str(_cfg.get("page_id", 3))
INTERVAL  = int(_cfg.get("interval_seconds", 60))

ZECTRIX_BASE = "https://cloud.zectrix.com"
OKX_URL      = "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT"

# 画布尺寸（4.2" 墨水屏）
W, H = 400, 300
# ─────────────────────────────────────────────────────


def get_btc_price() -> dict:
    resp = requests.get(OKX_URL, timeout=10)
    resp.raise_for_status()
    d = resp.json()["data"][0]
    price = float(d["last"])
    open_ = float(d["open24h"])
    change_pct = (price - open_) / open_ * 100 if open_ else 0
    return {
        "price":      price,
        "change_pct": change_pct,
        "high":       float(d["high24h"]),
        "low":        float(d["low24h"]),
        "open":       open_,
        "volume":     float(d["vol24h"]),
    }


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """尝试加载系统字体，兜底用默认字体。"""
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",          # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
        "/System/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def generate_image(data: dict) -> Image.Image:
    img  = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    font_title  = load_font(18)
    font_price  = load_font(52)
    font_change = load_font(22)
    font_label  = load_font(14)
    font_value  = load_font(16)
    font_time   = load_font(12)

    price      = data["price"]
    change_pct = data["change_pct"]
    sign       = "+" if change_pct >= 0 else ""
    now_str    = datetime.datetime.now().strftime("%m-%d %H:%M")

    # ── 顶栏 ──
    draw.rectangle([0, 0, W, 38], fill="black")
    draw.text((12, 9), "◆ BTC 行情看板", font=font_title, fill="white")
    draw.text((W - 105, 12), f"更新 {now_str}", font=font_time, fill="white")

    # ── 主价格 ──
    price_str = f"${price:,.2f}"
    bbox = draw.textbbox((0, 0), price_str, font=font_price)
    pw = bbox[2] - bbox[0]
    draw.text(((W - pw) // 2, 52), price_str, font=font_price, fill="black")

    # ── 涨跌幅 badge ──
    change_str = f"{sign}{change_pct:.2f}%"
    cbbox = draw.textbbox((0, 0), change_str, font=font_change)
    cw = cbbox[2] - cbbox[0] + 20
    cx = (W - cw) // 2
    badge_fill = "black" if change_pct < 0 else "black"
    draw.rectangle([cx, 118, cx + cw, 145], fill=badge_fill, outline="black")
    draw.text((cx + 10, 121), change_str, font=font_change, fill="white")

    # ── 分割线 ──
    draw.line([20, 158, W - 20, 158], fill="black", width=1)

    # ── 四格行情数据 ──
    fields = [
        ("最高", f"${data['high']:,.2f}"),
        ("最低", f"${data['low']:,.2f}"),
        ("开盘", f"${data['open']:,.2f}"),
        ("成交量", f"{data['volume']:,.1f} BTC"),
    ]
    col_w = W // 2
    for i, (label, value) in enumerate(fields):
        col = i % 2
        row = i // 2
        x = col * col_w + 20
        y = 168 + row * 44
        draw.text((x, y), label, font=font_label, fill="black")
        draw.text((x, y + 18), value, font=font_value, fill="black")
        if col == 0:
            draw.line([W // 2, 163, W // 2, 260], fill="black", width=1)

    # ── 底栏 ──
    draw.rectangle([0, H - 28, W, H], fill="black")
    draw.text((12, H - 21), "BTC/USDT  OKX  投资有风险，入市需谨慎",
              font=font_time, fill="white")

    return img


def push_to_device(img: Image.Image) -> dict:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    url = f"{ZECTRIX_BASE}/open/v1/devices/{DEVICE_ID}/display/image"
    resp = requests.post(
        url,
        headers={"X-API-Key": API_KEY},
        files={"images": ("btc.png", buf, "image/png")},
        data={"pageId": PAGE_ID, "dither": "false"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    import sys
    preview_mode = "--preview" in sys.argv

    if preview_mode:
        print("预览模式：生成图片但不推送到设备")
        data = get_btc_price()
        img  = generate_image(data)
        out  = "btc_preview.png"
        img.save(out)
        print(f"BTC ${data['price']:,.2f}  {data['change_pct']:+.2f}%")
        print(f"预览图已保存：{out}")
        return

    print(f"BTC 看板启动 → 设备 {DEVICE_ID} Page {PAGE_ID}，每 {INTERVAL}s 刷新")
    while True:
        try:
            data   = get_btc_price()
            img    = generate_image(data)
            result = push_to_device(img)
            ts     = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] BTC ${data['price']:,.2f}  {data['change_pct']:+.2f}%  → {result}")
        except Exception as e:
            print(f"[错误] {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
