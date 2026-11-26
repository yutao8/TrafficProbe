# -*- coding: utf-8 -*-
"""生成 README 用的终端截图底稿（HTML，再由浏览器截图）。

数值取自真实录制数据（docs/make_demo.py 生成的报告），保持一致。
用法：python docs/make_terminal_shots.py
"""
import html
import os
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
NL = chr(10)

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
pre{padding:16px 20px;white-space:pre;overflow-x:auto;font-size:12.5px;
line-height:1.6;font-variant-ligatures:none}
.dim{color:#7d8590}
.acc{color:#58a6ff}
.ok{color:#3fb950}
.warn{color:#d29922}
.bad{color:#f85149}
.hl{color:#79c0ff}
.sep{color:#30363d}
"""

W = 132
NAME_W = 32
COL_W = 12


def wlen(s):
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


def row(name, *cols, name_w=NAME_W, col_w=COL_W, name_cls=None):
    """name 左对齐，其余各列右对齐。"""
    parts = [c(ljust(name, name_w), name_cls) if name_cls else esc(ljust(name, name_w))]
    for v in cols:
        parts.append(esc(rjust(v, col_w)))
    return "".join(parts)


MENU = NL.join([
    sep(),
    "  " + c("流量探针 v2.1", "hl") + c("   统计 Clash / mihomo 的分流流量", "dim"),
    sep(),
    "  状态：" + c("已连接 http://127.0.0.1:9090", "ok") + "（v1.19.33）",
    c("（已有数据覆盖 2026-11-25 14:00:20 ~ 2026-11-26 10:22:08）", "dim"),
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

REPEATS = NL.join([
    sep(),
    "异常检测：" + c("2026-11-25 14:00:20 ~ 2026-11-26 10:22:08", "hl")
    + c("（13333 个采样点）", "dim"),
    c("判定标准：同一域名单次下行 >5MB，重复出现；5 分钟内的合并为一次事件", "dim"),
    sep(),
    "",
    c("usemagpie.ai", "acc"),
    "  触发 " + c("11", "hl") + " 次事件，单次约 " + c("98.8MB", "warn")
    + "，累计 " + c("1.1GB", "warn"),
    "  平均间隔 " + c("60 分钟", "hl") + c("（波动 0%）", "dim")
    + c("   <- 高度规律", "bad"),
    "  " + c("判定：循环下载，折算 1.41 GB/天，约 42 GB/月", "bad"),
    "  " + c("建议：关掉该程序的自动更新，或把域名加进直连规则", "warn"),
    "",
    c("release-assets.githubusercontent.com", "acc"),
    "  触发 " + c("5", "hl") + " 次事件，单次约 " + c("107.4MB", "warn")
    + "，累计 " + c("537.2MB", "warn"),
    "  平均间隔 " + c("60 分钟", "hl") + c("（波动 0%）", "dim")
    + c("   <- 高度规律", "bad"),
    "",
    "共 2 个域名存在重复下载行为。",
])

REPORT = NL.join([
    sep(),
    "时间段：2026-11-25 14:00:20  ~  2026-11-26 10:22:08",
    "覆盖：1202 分钟，13333 个采样点",
    "走代理：↑ 320.4MB   ↓ 3.2GB   合计 " + c("3.5GB", "acc"),
    "直连：  ↑ 78.6MB    ↓ 1.9GB   合计 " + c("1.9GB", "dim"),
    c("代理占比：64.9%（只有这部分扣机场配额）", "warn"),
    "代理折算：4.22 GB/天  →  " + c("127 GB/月", "bad"),
    sep(),
    "",
    c("按进程（谁在用）", "hl"),
    sep("-"),
    row("", "代理", "直连", "合计"),
    row("magpie.exe", "1.7GB", "1.7MB", "1.7GB"),
    row("language_server.exe", "580.7MB", "0.0B", "580.7MB"),
    row("WXWork.exe", "0.0B", "486.3MB", "486.3MB"),
    row("chrome.exe", "185.2MB", "110.4MB", "295.6MB"),
    row("Antigravity IDE.exe", "251.4MB", "0.0B", "251.4MB"),
    "",
    c("按出口（走哪条线）", "hl"),
    sep("-"),
    row("", "代理", "直连", "合计"),
    row("JPN 日本 05", "2.6GB", "0.0B", "2.6GB"),
    row("JPN 日本 03", "814.7MB", "0.0B", "814.7MB"),
    row("JPN 日本 01", "144.3MB", "0.0B", "144.3MB"),
    row("DIRECT", "0.0B", "1.9GB", "1.9GB", name_cls="dim"),
])

WASTE = NL.join([
    c("可直连却走了代理（纯浪费配额）", "warn"),
    sep("-"),
    row("域名", ljust("类型", 16), "", "流量", name_w=40, col_w=14),
    row("release-assets.githubusercontent.com", ljust("GitHub Release", 16),
        "", "539.8MB", name_w=40, col_w=14),
    row("analytics.services.jetbrains.com.cn", ljust("国内域名", 16),
        "", "19.2MB", name_w=40, col_w=14),
    row("resources.jetbrains.com.cn", ljust("国内域名", 16),
        "", "17.5MB", name_w=40, col_w=14),
    sep("-"),
    "合计可省：" + c("577.2MB", "warn"),
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
