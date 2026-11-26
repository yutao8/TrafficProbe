# -*- coding: utf-8 -*-
"""生成 README 用的终端截图底稿（HTML，再由浏览器截图）。

所有进程名、域名均为虚构，不含真实使用痕迹。
用法：python docs/make_terminal_shots.py

注意：中文字符在等宽字体里占 2 个字宽，补位必须按显示宽度算，
否则表头（上行/下行/合计）会和数字列错位。
"""
import html
import os
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;padding:26px;font:13px/1.7 "Cascadia Code","SF Mono",
Consolas,"Courier New",monospace;color:#c9d1d9}
.win{background:#161b22;border:1px solid #30363d;border-radius:10px;
overflow:hidden;max-width:1020px;margin:0 auto 22px}
.bar{background:#21262d;padding:9px 14px;display:flex;align-items:center;gap:7px;
border-bottom:1px solid #30363d}
.dot{width:11px;height:11px;border-radius:50%}
.r{background:#ff5f57}.y{background:#febc2e}.g{background:#28c840}
.title{margin-left:10px;color:#8b949e;font-size:12px}
pre{padding:18px 20px;white-space:pre;overflow-x:auto;font-size:12.5px;
line-height:1.75;font-variant-ligatures:none}
.dim{color:#7d8590}
.acc{color:#58a6ff}
.ok{color:#3fb950}
.warn{color:#d29922}
.bad{color:#f85149}
.hl{color:#79c0ff}
.sep{color:#30363d}
"""

W = 130          # 分隔线宽度
NAME_W = 34      # 名称列宽
COL_W = 13       # 数值列宽


def wlen(s):
    """按终端显示宽度计算（CJK 算 2）。"""
    n = 0
    for ch in str(s):
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def ljust(s, w):
    return str(s) + " " * max(0, w - wlen(s))


def rjust(s, w):
    return " " * max(0, w - wlen(s)) + str(s)


def esc(s):
    return html.escape(str(s))


def sep(ch="="):
    return '<span class="sep">%s</span>' % (ch * W)


def c(text, cls):
    return '<span class="%s">%s</span>' % (cls, esc(text))


def cells(*pairs):
    """pairs: (文本, 宽度, 对齐, 颜色类或 None)。返回对齐好的行。"""
    out = []
    for text, width, align, cls in pairs:
        pad = ljust(text, width) if align == "l" else rjust(text, width)
        out.append(c(pad, cls) if cls else esc(pad))
    return "".join(out)


def trow(name, up, down, total, name_cls=None):
    return cells((name, NAME_W, "l", name_cls),
                 (up, COL_W, "r", None),
                 (down, COL_W, "r", None),
                 (total, COL_W, "r", None))


MENU = "\n".join([
    sep(),
    "  " + c("流量探针 v2.1", "hl") + c("   统计 Clash / mihomo 的分流流量", "dim"),
    sep(),
    "  状态：" + c("已连接 http://127.0.0.1:9090", "ok") + "（v1.19.33）",
    c("（已有数据覆盖 2026-11-20 00:12:05 ~ 2026-11-20 20:04:35）", "dim"),
    "  数据目录：" + c("%APPDATA%\\TrafficProbe", "dim"),
    sep("-"),
    "",
    "  1) 开始录制（占住窗口，Ctrl+C 停）",
    "  t) 托盘后台录制（关窗口也继续跑）",
    "  2) 查今天",
    "  3) 查最近 24 小时",
    "  4) 查自定义时间段",
    "  5) " + c("找异常循环下载，定位流量失控", "warn"),
    "  6) 看哪些流量本该直连",
    "  7) 实时看当前连接",
    "  8) 按天对账",
    "  h) 生成 HTML 报告",
    "  9) 清理旧日志",
    "  c) 设置 API 端口和密码",
    "  0) 退出",
    "",
    "  选择：",
])

REPEATS = "\n".join([
    sep(),
    "异常检测：" + c("2026-11-20 00:00:12 ~ 2026-11-20 20:04:22", "hl")
    + c("（240 个采样点）", "dim"),
    c("判定标准：同一域名单次下行 >5MB，重复出现；5 分钟内的合并为一次事件", "dim"),
    sep(),
    "",
    c("mirror.example-update.io", "acc"),
    "  触发 " + c("19", "hl") + " 次事件（原始 78 个采样），单次约 "
    + c("104.2MB", "warn") + "，累计 " + c("1.94GB", "warn"),
    "  平均间隔 " + c("60 分钟", "hl") + c("（波动 2%，按 18 个有效间隔算）", "dim")
    + c("   <- 高度规律", "bad"),
    "  " + c("判定：循环下载，折算 2.33 GB/天，约 70 GB/月", "bad"),
    "  " + c("建议：关掉该程序的自动更新，或把域名加进直连规则", "warn"),
    "",
    c("mirror.example-cnpm.cn", "acc"),
    "  触发 " + c("6", "hl") + " 次事件（原始 24 个采样），单次约 "
    + c("28.4MB", "warn") + "，累计 " + c("170.4MB", "warn"),
    "  平均间隔 " + c("120 分钟", "hl") + c("（波动 11%，按 5 个有效间隔算）", "dim"),
    "",
    "共 2 个域名存在重复下载行为。",
])

REPORT = "\n".join([
    sep(),
    "时间段：2026-11-20 00:00:12  ~  2026-11-20 20:04:22",
    "覆盖：1204 分钟，240 个采样点",
    "走代理：↑ 312.5MB   ↓ 3.71GB   合计 " + c("4.01GB", "acc"),
    "直连：  ↑ 88.2MB    ↓ 1.24GB   合计 " + c("1.32GB", "dim"),
    c("代理占比：75.2%（只有这部分扣机场配额）", "warn"),
    "代理折算：4.79 GB/天  →  " + c("144 GB/月", "bad"),
    sep(),
    "",
    c("按进程（谁在用）", "hl"),
    sep("-"),
    trow("", "上行", "下行", "合计"),
    trow("editor.exe", "284.1MB", "2.94GB", "3.21GB"),
    trow("browser.exe", "19.8MB", "612.4MB", "632.2MB"),
    trow("sync-agent.exe", "7.2MB", "128.6MB", "135.8MB"),
    trow("media-player.exe", "1.1MB", "24.3MB", "25.4MB"),
    "",
    c("按出口（走哪条线）", "hl"),
    sep("-"),
    trow("", "上行", "下行", "合计"),
    trow("JP-Tokyo-03", "271.4MB", "2.68GB", "2.94GB"),
    trow("JP-Tokyo-01", "41.1MB", "1.03GB", "1.07GB"),
    trow("DIRECT", "88.2MB", "1.24GB", "1.32GB", name_cls="dim"),
])

WASTE = "\n".join([
    c("可直连却走了代理（纯浪费配额）", "warn"),
    sep("-"),
    cells(("域名", 44, "l", None), ("类型", 14, "l", None),
          ("", COL_W * 2 - 14, "l", None), ("合计", COL_W, "r", None)),
    cells(("mirror.example-cnpm.cn", 44, "l", None), ("国内域名", 14, "l", None),
          ("", COL_W * 2 - 14, "l", None), ("170.4MB", COL_W, "r", None)),
    cells(("img.example-cdn.cn", 44, "l", None), ("国内域名", 14, "l", None),
          ("", COL_W * 2 - 14, "l", None), ("42.1MB", COL_W, "r", None)),
    sep("-"),
    "合计可省：" + c("212.5MB", "warn"),
])


def page(blocks):
    body = []
    for title, content in blocks:
        body.append(
            '<div class="win"><div class="bar">'
            '<span class="dot r"></span><span class="dot y"></span>'
            '<span class="dot g"></span><span class="title">%s</span></div>'
            '<pre>%s</pre></div>' % (esc(title), content))
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"><style>%s</style>'
            '</head><body>%s</body></html>' % (CSS, "".join(body)))


def main():
    # 分开输出，README 里可以各自排版
    menu_doc = page([("TrafficProbe  --  交互菜单", MENU)])
    cli_doc = page([
        ("TrafficProbe find-repeats --hours 24h", REPEATS),
        ("TrafficProbe report --last 24h --by all", REPORT),
        ("TrafficProbe report --last 24h --waste", WASTE),
    ])
    for name, doc in (("terminal-menu.html", menu_doc),
                      ("terminal-cli.html", cli_doc)):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            f.write(doc)
        print("已生成", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
