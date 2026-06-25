"""
Claude Code Usage 看板 — 推送到 Zectrix 墨水屏
读取 ~/.claude/ 本地数据，生成 400×300 使用统计看板

依赖：pip install requests Pillow
"""

import csv
import glob
import io
import json
import os
import sys
import time
import datetime
import requests
from PIL import Image, ImageDraw, ImageFont

# ── 配置 ──────────────────────────────────────────────
with open("config.json") as _f:
    _cfg = json.load(_f)

API_KEY   = _cfg["api_key"]
DEVICE_ID = _cfg["device_id"]
PAGE_ID   = "4"                           # BTC=3, Claude=4
INTERVAL  = int(_cfg.get("interval_seconds", 60))

CLAUDE_DIR   = os.path.expanduser("~/.claude")
ZECTRIX_BASE = "https://cloud.zectrix.com"
W, H = 400, 300
# ─────────────────────────────────────────────────────


# ── 数据采集 ──────────────────────────────────────────

def get_active_session() -> dict:
    """读取当前活跃 Claude Code 会话信息。"""
    sessions_dir = os.path.join(CLAUDE_DIR, "sessions")
    result = {"status": "idle", "cwd": "", "duration_min": 0, "version": ""}
    now_ms = time.time() * 1000

    for path in glob.glob(os.path.join(sessions_dir, "*.json")):
        try:
            with open(path) as f:
                s = json.load(f)
            # 只看 5 分钟内更新过的 session
            updated = s.get("updatedAt", 0)
            if now_ms - updated > 5 * 60 * 1000:
                continue
            if s.get("status") in ("busy", "idle"):
                cwd = s.get("cwd", "")
                result["cwd"] = os.path.basename(cwd) if cwd else ""
                result["status"] = s.get("status", "idle")
                result["version"] = s.get("version", "")
                started = s.get("startedAt", now_ms)
                result["duration_min"] = int((now_ms - started) / 60000)
        except Exception:
            pass
    return result


def get_today_stats() -> dict:
    """从 history.jsonl 统计今日命令数；从 sessions.csv 取最新工具用量。"""
    today = datetime.date.today().isoformat()
    today_ts = datetime.datetime.combine(datetime.date.today(),
                                         datetime.time.min).timestamp()
    cmd_count = 0
    history_path = os.path.join(CLAUDE_DIR, "history.jsonl")
    try:
        with open(history_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("timestamp", 0) / 1000 >= today_ts:
                        cmd_count += 1
                except Exception:
                    pass
    except Exception:
        pass

    reads = edits = writes = 0
    grade = ""
    sessions_csv = os.path.join(CLAUDE_DIR, "metrics", "sessions.csv")
    try:
        with open(sessions_csv) as f:
            rows = [r for r in csv.DictReader(f)
                    if int(r["timestamp"]) >= today_ts]
        if rows:
            last = rows[-1]
            reads  = int(last["reads"])
            edits  = int(last["edits"])
            writes = int(last["writes"])
            grade  = last["grade"]
    except Exception:
        pass

    return {"commands": cmd_count, "reads": reads,
            "edits": edits, "writes": writes, "grade": grade}


def get_weekly_activity() -> list[dict]:
    """取最近 7 天每日指令数，优先从 history.jsonl 实时计算。"""
    today = datetime.date.today()
    days  = [(today - datetime.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    by_date: dict[str, int] = {}

    # 先用 stats-cache 填历史（7天前更早的背景数据不影响图表，可忽略）
    cache_path = os.path.join(CLAUDE_DIR, "stats-cache.json")
    try:
        with open(cache_path) as f:
            data = json.load(f)
        for entry in data.get("dailyActivity", []):
            by_date[entry["date"]] = entry["messageCount"]
    except Exception:
        pass

    # 用 history.jsonl 覆盖近 7 天（数据更新鲜）
    week_start_ts = (datetime.datetime.combine(
        today - datetime.timedelta(days=6), datetime.time.min)).timestamp()
    history_path  = os.path.join(CLAUDE_DIR, "history.jsonl")
    try:
        with open(history_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    ts = d.get("timestamp", 0) / 1000
                    if ts >= week_start_ts:
                        day = datetime.date.fromtimestamp(ts).isoformat()
                        by_date[day] = by_date.get(day, 0) + 1
                except Exception:
                    pass
    except Exception:
        pass

    return [{"date": d, "count": by_date.get(d, 0)} for d in days]


def get_alltime_stats() -> dict:
    """从 stats-cache 取累计数据。"""
    cache_path = os.path.join(CLAUDE_DIR, "stats-cache.json")
    try:
        with open(cache_path) as f:
            data = json.load(f)
        activity = data.get("dailyActivity", [])
        return {
            "total_msgs":     sum(x["messageCount"] for x in activity),
            "total_tools":    sum(x["toolCallCount"] for x in activity),
            "total_sessions": sum(x["sessionCount"] for x in activity),
            "active_days":    len(activity),
        }
    except Exception:
        return {"total_msgs": 0, "total_tools": 0,
                "total_sessions": 0, "active_days": 0}


# ── 字体 ─────────────────────────────────────────────

def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    mono_candidates = [
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]
    cn_candidates = [
        ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
        ("/System/Library/Fonts/STHeiti Light.ttc", 0),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ]
    # 数字/ASCII 优先 Menlo（等宽），中文标签用黑体
    for path in mono_candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    for path, idx in cn_candidates:
        try:
            return ImageFont.truetype(path, size, index=idx)
        except Exception:
            pass
    return ImageFont.load_default()


def load_cn_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
        ("/System/Library/Fonts/STHeiti Light.ttc", 0),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ]
    for path, idx in candidates:
        try:
            return ImageFont.truetype(path, size, index=idx)
        except Exception:
            pass
    return ImageFont.load_default()


# ── 绘图 ─────────────────────────────────────────────

GRADE_LABEL = {"good": "GOOD", "degraded": "WARN", "severe": "HIGH", "": "—"}
GRADE_ICON  = {"good": "●", "degraded": "◐", "severe": "◉", "": "○"}


def draw_bar_chart(draw: ImageDraw.ImageDraw, weekly: list[dict],
                   x: int, y: int, w: int, h: int) -> None:
    """7 天活动迷你柱状图。"""
    counts = [d["count"] for d in weekly]
    max_c  = max(counts) if any(counts) else 1

    bar_w   = (w - 6 * 3) // 7   # 7 bars + 6 gaps of 3px
    bar_gap = 3
    label_h = 14
    chart_h = h - label_h - 4

    # 今天高亮，其余灰阶（用点阵模拟深浅）
    for i, day in enumerate(weekly):
        bx     = x + i * (bar_w + bar_gap)
        count  = day["count"]
        filled = int(chart_h * count / max_c) if max_c else 0
        by_top = y + chart_h - filled

        is_today = (i == 6)

        if filled > 0:
            if is_today:
                # 今天：实心黑
                draw.rectangle([bx, by_top, bx + bar_w - 1, y + chart_h], fill="black")
            else:
                # 历史：竖条纹（模拟灰色）
                draw.rectangle([bx, by_top, bx + bar_w - 1, y + chart_h], fill="black", outline="black")
                # 用白色细线制造条纹感
                for sx in range(bx + 1, bx + bar_w - 1, 2):
                    draw.line([sx, by_top, sx, y + chart_h], fill="white")

        # 空白底座
        draw.rectangle([bx, y + chart_h + 1, bx + bar_w - 1, y + chart_h + 2], fill="black")

    # x 轴标签：仅标注今天
    font_xs = load_cn_font(10)
    label_x = x + 6 * (bar_w + bar_gap)
    date_short = weekly[-1]["date"][5:]   # MM-DD
    draw.text((label_x, y + chart_h + 4), date_short, font=font_xs, fill="black")


def generate_image(session: dict, today: dict, weekly: list[dict],
                   alltime: dict) -> Image.Image:
    img  = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    # ── fonts ──
    f_title  = load_cn_font(17)
    f_status = load_font(12)
    f_big    = load_font(46)
    f_mid    = load_font(20)
    f_label  = load_cn_font(13)
    f_sm     = load_font(13)
    f_xs     = load_cn_font(11)

    now_str   = datetime.datetime.now().strftime("%m-%d %H:%M")
    status    = session["status"].upper()         # BUSY / IDLE
    proj      = session["cwd"] or "—"
    dur_str   = f"{session['duration_min']}min" if session["duration_min"] else "—"

    # ── TOP BAR ──────────────────────────────────────
    draw.rectangle([0, 0, W, 36], fill="black")
    draw.text((12, 9), "◆ CLAUDE CODE", font=f_title, fill="white")

    # 状态 pill
    status_color = "white" if status == "BUSY" else "#888888"
    pill_x = W - 110
    draw.rectangle([pill_x, 8, pill_x + 50, 28], fill="white")
    draw.text((pill_x + 5, 10), status, font=f_status, fill="black")
    draw.text((W - 54, 11), now_str, font=f_sm, fill="white")

    # ── PROJECT LINE ─────────────────────────────────
    draw.text((12, 42), f"/{proj}", font=load_cn_font(14), fill="black")
    draw.text((W - 60, 43), dur_str, font=f_sm, fill="black")
    draw.line([0, 60, W, 60], fill="black", width=1)

    # ── MAIN AREA ────────────────────────────────────
    # 左侧：今日 commands 大数字
    cmd_str = str(today["commands"])
    bbox    = draw.textbbox((0, 0), cmd_str, font=f_big)
    cw      = bbox[2] - bbox[0]
    draw.text(((190 - cw) // 2, 68), cmd_str, font=f_big, fill="black")
    draw.text((12, 118), "今日指令数", font=f_label, fill="black")

    # 左侧次要数据
    draw.text((12, 136), f"R {today['reads']}  E {today['edits']}  W {today['writes']}",
              font=f_sm, fill="black")

    grade_text = GRADE_LABEL.get(today["grade"], "—")
    draw.text((12, 155), f"Grade: {grade_text}", font=f_xs, fill="black")

    # 中间分割线
    draw.line([195, 62, 195, 178], fill="black", width=1)

    # 右侧：7 天趋势图
    draw.text((205, 64), "7天活跃度", font=f_xs, fill="black")
    draw_bar_chart(draw, weekly, x=203, y=80, w=188, h=88)

    # ── DIVIDER ──────────────────────────────────────
    draw.line([0, 178, W, 178], fill="black", width=1)

    # ── ALL-TIME STATS ───────────────────────────────
    msgs_k   = f"{alltime['total_msgs'] / 1000:.1f}K"
    tools_k  = f"{alltime['total_tools'] / 1000:.1f}K"
    days_n   = str(alltime["active_days"])
    sess_n   = str(alltime["total_sessions"])

    # 四格布局
    col = W // 4
    for i, (val, lbl) in enumerate([
        (msgs_k,  "总消息"),
        (tools_k, "工具调用"),
        (sess_n,  "总会话"),
        (days_n,  "活跃天"),
    ]):
        cx = i * col + col // 2
        vbbox = draw.textbbox((0, 0), val, font=f_mid)
        vw = vbbox[2] - vbbox[0]
        draw.text((cx - vw // 2, 185), val, font=f_mid, fill="black")
        lbbox = draw.textbbox((0, 0), lbl, font=f_xs)
        lw = lbbox[2] - lbbox[0]
        draw.text((cx - lw // 2, 210), lbl, font=f_xs, fill="black")
        if i < 3:
            draw.line([col * (i + 1), 182, col * (i + 1), 225], fill="black", width=1)

    # ── BOTTOM BAR ───────────────────────────────────
    draw.rectangle([0, H - 28, W, H], fill="black")
    ver = session.get("version", "")
    ver_str = f"v{ver}" if ver else "Claude Code"
    draw.text((12, H - 21), ver_str, font=f_xs, fill="white")
    draw.text((W - 155, H - 21),
              "claude.ai/code  极趣云平台看板", font=f_xs, fill="white")

    return img


# ── 推送 ──────────────────────────────────────────────

def push_to_device(img: Image.Image) -> dict:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    resp = requests.post(
        f"{ZECTRIX_BASE}/open/v1/devices/{DEVICE_ID}/display/image",
        headers={"X-API-Key": API_KEY},
        files={"images": ("claude.png", buf, "image/png")},
        data={"pageId": PAGE_ID, "dither": "false"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    preview = "--preview" in sys.argv
    if preview:
        print("预览模式...")

    session = get_active_session()
    today   = get_today_stats()
    weekly  = get_weekly_activity()
    alltime = get_alltime_stats()

    if preview:
        print(f"Session: {session}")
        print(f"Today: {today}")
        print(f"Weekly: {weekly}")
        img = generate_image(session, today, weekly, alltime)
        img.save("claude_preview.png")
        print("预览图已保存：claude_preview.png")
        return

    print(f"Claude Code 看板启动 → 设备 {DEVICE_ID} Page {PAGE_ID}，每 {INTERVAL}s 刷新")
    while True:
        try:
            session = get_active_session()
            today   = get_today_stats()
            weekly  = get_weekly_activity()
            alltime = get_alltime_stats()
            img     = generate_image(session, today, weekly, alltime)
            result  = push_to_device(img)
            ts      = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] cmds={today['commands']} R={today['reads']} "
                  f"E={today['edits']} W={today['writes']} status={session['status']} → {result}")
        except Exception as e:
            print(f"[错误] {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
