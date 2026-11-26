# -*- coding: utf-8 -*-
"""生成单文件 HTML 流量报告。零外部依赖，双击即开。

用法:
    python report_html.py --last 24h
    python report_html.py --from "2026-11-25" --to "2026-11-26"
    python report_html.py --last 7d -o report.html
"""
import argparse
import html
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """与 traffic_probe 保持一致：用户级应用目录，跨平台。"""
    env = os.environ.get("TRAFFIC_PROBE_HOME")
    if env:
        d = env
    elif sys.platform == "win32":
        d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                         "TrafficProbe")
    elif sys.platform == "darwin":
        d = os.path.join(os.path.expanduser("~"), "Library",
                         "Application Support", "TrafficProbe")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
        d = os.path.join(base, "TrafficProbe")
    os.makedirs(d, exist_ok=True)
    return d


def human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f%s" % (n, u)
        n /= 1024.0
    return "%.1fTB" % n


def human_num(n):
    """返回 (数值, 单位)，给图表用。"""
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return round(n, 1), u
        n /= 1024.0
    return round(n, 1), "TB"


def parse_dt(s):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    raise SystemExit("时间格式看不懂：%s" % s)


def parse_last(s):
    u = s[-1].lower()
    n = float(s[:-1])
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n),
            "d": timedelta(days=n)}[u]


def load(path, t_from, t_to):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if t_from <= ts <= t_to:
                r["_dt"] = ts
                rows.append(r)
    return rows


def agg(rows, keyfn):
    """返回 {key: {"proxy": [up,down], "direct": [up,down]}}"""
    d = {}
    for r in rows:
        for it in r["items"]:
            k = keyfn(it, r)
            if not k:
                continue
            v = d.setdefault(k, {"proxy": [0, 0], "direct": [0, 0]})
            t = v["direct"] if it["c"] == "DIRECT" else v["proxy"]
            t[0] += it["u"]
            t[1] += it["d"]
    return d


def tot_of(v):
    return sum(v["proxy"]) + sum(v["direct"])


def repeat_scan(rows, threshold_mb=5.0, merge_sec=300, min_times=3):
    """找出循环下载，逻辑与 CLI 的 find-repeats 一致。"""
    thr = threshold_mb * 1024 * 1024
    raw = {}
    for r in rows:
        for it in r["items"]:
            if it["c"] == "DIRECT" or it["d"] < thr:
                continue
            raw.setdefault(it["h"], []).append((r["_dt"], it["d"]))

    def merge(pts):
        pts = sorted(pts)
        ev = []
        for t, sz in pts:
            if ev and (t - ev[-1][0]).total_seconds() <= merge_sec:
                ev[-1] = (ev[-1][0], ev[-1][1] + sz, ev[-1][2] + 1)
            else:
                ev.append((t, sz, 1))
        return ev

    out = []
    for host, pts in raw.items():
        ev = merge(pts)
        if len(ev) < min_times:
            continue
        times = [e[0] for e in ev]
        sizes = [e[1] for e in ev]
        gaps = [(times[i + 1] - times[i]).total_seconds() / 60 for i in range(len(times) - 1)]
        if not gaps:
            continue
        short = [g for g in gaps if g <= 120]
        base = short if len(short) >= 2 else gaps
        mean = sum(base) / len(base) or 1
        var = sum((g - mean) ** 2 for g in base) / len(base)
        cv = (var ** 0.5) / mean if mean else 9
        span = max((times[-1] - times[0]).total_seconds() / 86400, 0.01)
        total = sum(sizes)
        out.append({
            "host": host,
            "events": len(ev),
            "avg_size": sum(sizes) / len(sizes),
            "total": total,
            "gap": mean,
            "cv": cv,
            "periodic": cv < 0.3 and 5 <= mean <= 24 * 60,
            "per_day": total / span,
        })
    out.sort(key=lambda x: -x["total"])
    return out


DIRECT_HINTS = [
    (r"(^|\.)qq\.com$", "腾讯"), (r"(^|\.)myqcloud\.com$", "腾讯云"),
    (r"(^|\.)baidu\.com$", "百度"), (r"(^|\.)bdstatic\.com$", "百度"),
    (r"(^|\.)taobao\.com$|(^|\.)tmall\.com$|(^|\.)alicdn\.com$", "阿里"),
    (r"(^|\.)aliyun\.com$|(^|\.)aliyuncs\.com$", "阿里云"),
    (r"(^|\.)bilibili\.com$|(^|\.)hdslb\.com$", "B站"),
    (r"(^|\.)jd\.com$|(^|\.)360buyimg\.com$", "京东"),
    (r"(^|\.)npmmirror\.com$", "npm 镜像"),
    (r"^release-assets\.githubusercontent\.com$", "GitHub Release"),
    (r"(^|\.)cn$", "国内域名"),
]


def waste_scan(rows):
    import re as _re
    d = agg(rows, lambda it, r: it["h"] if it["c"] != "DIRECT" else None)
    out = []
    for h, v in d.items():
        if not h:
            continue
        total = sum(v["proxy"])
        if not total:
            continue
        for pat, label in DIRECT_HINTS:
            if _re.search(pat, h, _re.I):
                out.append({"host": h, "label": label, "total": total})
                break
    out.sort(key=lambda x: -x["total"])
    return out


# ---------------- HTML ----------------

def bar_chart(items):
    """横向堆叠条：左段蓝=代理（扣配额），右段灰=直连（不扣）。

    items = [(名称, {"proxy":[u,d], "direct":[u,d]}), ...]
    """
    if not items:
        return '<p class="empty">没有数据</p>'
    mx = max(tot_of(v) for _, v in items) or 1
    out = ['<div class="bars">']
    for name, v in items:
        pv, dv = sum(v["proxy"]), sum(v["direct"])
        total = pv + dv
        ppct = pv / mx * 100
        dpct = dv / mx * 100
        tip = "%s / 代理 %s / 直连 %s" % (name, human(pv), human(dv))
        out.append(
            '<div class="bar-row"><div class="bar-name" title="%s">%s</div>'
            '<div class="bar-track">'
            '<div class="bar-fill" style="width:%.2f%%"></div>'
            '<div class="bar-fill d" style="width:%.2f%%"></div>'
            '</div>'
            '<div class="bar-val"><span class="v-p">%s</span>'
            '<span class="v-sep">/</span><span class="v-d">%s</span></div></div>'
            % (html.escape(tip), html.escape(str(name)[:38]),
               ppct, dpct, human(pv), human(dv)))
    out.append("</div>")
    return "".join(out)


def line_chart(points):
    """双线折线：代理（蓝）与直连（灰）。points=[(label, proxy_val, direct_val)]"""
    if not points:
        return '<p class="empty">没有数据</p>'
    W, H = 860, 220
    PADL, PADR, PADT, PADB = 56, 14, 14, 36
    mx = max(max(p, d) for _, p, d in points) or 1
    n = len(points)
    step = (W - PADL - PADR) / max(n - 1, 1)

    def path_of(idx):
        cs = []
        for i, pt in enumerate(points):
            val = pt[idx]
            x = PADL + i * step
            y = PADT + (H - PADT - PADB) * (1 - val / mx)
            cs.append((x, y))
        pth = "M " + " L ".join("%.1f %.1f" % c for c in cs)
        return pth, cs

    p_path, p_cs = path_of(1)
    d_path, d_cs = path_of(2)
    p_area = p_path + " L %.1f %d L %.1f %d Z" % (
        p_cs[-1][0], H - PADB, p_cs[0][0], H - PADB)

    ticks = []
    for k in range(5):
        v = mx * k / 4
        y = PADT + (H - PADT - PADB) * (1 - k / 4)
        ticks.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/>'
                     % (PADL, y, W - PADR, y))
        ticks.append('<text x="%d" y="%.1f" class="axis" text-anchor="end">%s</text>'
                     % (PADL - 6, y + 4, human(v)))
    labels = []
    step_lbl = max(1, n // 7)
    for i, pt in enumerate(points):
        if i % step_lbl == 0 or i == n - 1:
            x = PADL + i * step
            labels.append('<text x="%.1f" y="%d" class="axis" text-anchor="middle">%s</text>'
                          % (x, H - PADB + 16, html.escape(pt[0][:5])))
    return ('<svg viewBox="0 0 %d %d" class="chart" preserveAspectRatio="none">'
            '<defs><linearGradient id="gp" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0%%" stop-color="#4aa8ff" stop-opacity="0.5"/>'
            '<stop offset="100%%" stop-color="#4aa8ff" stop-opacity="0.02"/>'
            '</linearGradient></defs>%s'
            '<path d="%s" fill="url(#gp)"/>'
            '<path d="%s" fill="none" stroke="#8fa3c0" stroke-width="1.6" '
            'stroke-dasharray="4 3"/>'
            '<path d="%s" fill="none" stroke="#5cb4ff" stroke-width="2"/>'
            '%s</svg>' % (W, H, "".join(ticks), p_area, d_path, p_path, "".join(labels)))


LEGEND = ('<div class="legend">'
          '<span><i class="sw p"></i>代理流量（扣机场配额）</span>'
          '<span><i class="sw d"></i>直连流量（不扣配额）</span></div>')

CSS = """
:root{--bg:#0f1626;--card:#182234;--card2:#1e2a40;--line:#27354e;
--txt:#e8eef8;--dim:#8fa3c0;--acc:#5cb4ff;--warn:#ffb84d;--bad:#ff6b6b;--ok:#4ade80}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.6 -apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif}
.wrap{max-width:1160px;margin:0 auto;padding:28px 20px 64px}
h1{font-size:24px;margin:0 0 4px;letter-spacing:.5px}
h2{font-size:16px;margin:0 0 14px;color:var(--acc);font-weight:600}
.sub{color:var(--dim);font-size:13px;margin-bottom:22px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.card .k{color:var(--dim);font-size:12px;margin-bottom:6px}
.card .v{font-size:22px;font-weight:600;letter-spacing:.3px}
.card .n{color:var(--dim);font-size:12px;margin-top:4px}
.card.acc .v{color:var(--acc)}
.card.warn .v{color:var(--warn)}
.card.bad .v{color:var(--bad)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:18px 20px;margin-bottom:20px}
.bars{margin-top:4px}
.bar-row{display:grid;grid-template-columns:minmax(120px,240px) 1fr 92px;
gap:12px;align-items:center;padding:5px 0}
.bar-name{color:var(--dim);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.bar-track{background:#101a2b;border-radius:5px;height:16px;overflow:hidden}
.bar-fill{height:100%;background:linear-gradient(90deg,#2f6fd0,#5cb4ff);border-radius:5px}
.bar-val{text-align:right;font-variant-numeric:tabular-nums;font-size:12.5px}
.chart{width:100%;height:200px;display:block}
.grid{stroke:#233149;stroke-width:1}
.axis{fill:var(--dim);font-size:10px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;font-size:12px}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11.5px}
.tag.bad{background:rgba(255,107,107,.15);color:var(--bad)}
.tag.warn{background:rgba(255,184,77,.15);color:var(--warn)}
.tag.ok{background:rgba(74,222,128,.15);color:var(--ok)}
.empty{color:var(--dim);padding:10px 0}
.alert{border-left:3px solid var(--warn);background:rgba(255,184,77,.07);
padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:13px}
.alert b{color:var(--warn)}
.foot{color:var(--dim);font-size:12px;text-align:center;margin-top:34px;
padding-top:18px;border-top:1px solid var(--line)}
.legend{display:flex;gap:22px;margin:0 0 12px;font-size:12px;color:var(--dim);
flex-wrap:wrap}
.legend span{display:flex;align-items:center;gap:7px}
.sw{display:inline-block;width:11px;height:11px;border-radius:3px}
.sw.p{background:linear-gradient(90deg,#2f6fd0,#5cb4ff)}
.sw.d{background:#465875}
.bar-track{display:flex}
.bar-fill{flex:none}
.bar-fill.d{background:linear-gradient(90deg,#3a4a63,#5a6f8d)}
.bar-val{display:flex;justify-content:flex-end;gap:4px;font-size:12px}
.v-p{color:#7cc4ff}
.v-sep{color:#3a4a63}
.v-d{color:#9db0c9}
"""


def build(rows, t_from, t_to):
    prox = [0, 0]
    drct = [0, 0]
    for r in rows:
        for it in r["items"]:
            t = drct if it["c"] == "DIRECT" else prox
            t[0] += it["u"]
            t[1] += it["d"]
    ptot = sum(prox)
    dtot = sum(drct)
    tot = ptot + dtot

    span_min = max((rows[-1]["_dt"] - rows[0]["_dt"]).total_seconds() / 60, 0.02) if rows else 0
    per_day = ptot / span_min * 60 * 24 / 1024 ** 3 if span_min else 0

    by_proc = agg(rows, lambda it, r: it["p"])
    by_host = agg(rows, lambda it, r: it["h"])
    by_chain = agg(rows, lambda it, r: it["c"])
    by_hour = agg(rows, lambda it, r: r["_dt"].strftime("%m-%d %H时"))
    by_day = agg(rows, lambda it, r: r["_dt"].strftime("%Y-%m-%d"))

    reps = repeat_scan(rows)
    waste = waste_scan(rows)
    periodic = [x for x in reps if x["periodic"]]

    def top(d, n=12):
        items = [(k, v) for k, v in d.items() if k]
        items.sort(key=lambda x: -tot_of(x[1]))
        return items[:n]

    # 按小时拆分代理/直连
    by_hour_split = {}
    for r in rows:
        k = r["_dt"].strftime("%m-%d %H时")
        v = by_hour_split.setdefault(k, [0, 0, 0])   # proxy, direct
        for it in r["items"]:
            if it["c"] == "DIRECT":
                v[1] += it["u"] + it["d"]
            else:
                v[0] += it["u"] + it["d"]
    dh_sorted = [(k.split(" ")[-1].replace("时", ""), v[0], v[1])
                 for k, v in sorted(by_hour_split.items())[-48:]]

    parts = []
    parts.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    parts.append('<title>流量报告 %s</title><style>%s</style></head><body><div class="wrap">'
                 % (datetime.now().strftime("%Y-%m-%d %H:%M"), CSS))
    parts.append('<h1>流量报告</h1>')
    parts.append('<div class="sub">%s ~ %s ｜ 覆盖 %.1f 小时 ｜ %d 个采样点 ｜ 生成于 %s</div>'
                 % (rows[0]["_dt"], rows[-1]["_dt"], span_min / 60, len(rows),
                    datetime.now().strftime("%Y-%m-%d %H:%M")))

    if periodic:
        names = "、".join(html.escape(x["host"]) for x in periodic[:3])
        sv = sum(x["per_day"] for x in periodic) * 30 / 1024 ** 3
        parts.append('<div class="alert"><b>发现 %d 个循环下载域名</b>：%s<br>'
                     '折算每月浪费约 <b>%.0f GB</b>，建议关掉对应程序的自动更新，'
                     '或为这些域名加直连规则。</div>' % (len(periodic), names, sv))

    # 概览卡片
    parts.append('<div class="cards">')
    parts.append('<div class="card acc"><div class="k">走代理（扣配额）</div>'
                 '<div class="v">%s</div><div class="n">↑ %s / ↓ %s</div></div>'
                 % (human(ptot), human(prox[0]), human(prox[1])))
    parts.append('<div class="card"><div class="k">直连（不扣配额）</div>'
                 '<div class="v">%s</div><div class="n">↑ %s / ↓ %s</div></div>'
                 % (human(dtot), human(drct[0]), human(drct[1])))
    parts.append('<div class="card"><div class="k">代理占比</div><div class="v">%.1f%%</div>'
                 '<div class="n">总流量 %s</div></div>'
                 % ((ptot / tot * 100) if tot else 0, human(tot)))
    cls = "bad" if per_day * 30 > 300 else ("warn" if per_day * 30 > 120 else "")
    parts.append('<div class="card %s"><div class="k">折算月用量</div><div class="v">%.0f GB</div>'
                 '<div class="n">%.2f GB/天</div></div>' % (cls, per_day * 30, per_day))
    parts.append('</div>')

    # 按天
    if len(by_day) > 1:
        parts.append('<div class="panel"><h2>按天（对账用）</h2>')
        parts.append(LEGEND)
        parts.append(bar_chart([(k, v) for k, v in sorted(by_day.items())]))
        parts.append('</div>')

    # 折线
    parts.append('<div class="panel"><h2>时间分布（按小时）</h2>')
    parts.append(LEGEND)
    parts.append(line_chart(dh_sorted[-48:]))
    parts.append('</div>')

    # 三段条形
    parts.append('<div class="panel"><h2>谁在用（按进程）</h2>%s%s</div>'
                 % (LEGEND, bar_chart(top(by_proc, 12))))
    parts.append('<div class="panel"><h2>连到哪（按域名）</h2>%s%s</div>'
                 % (LEGEND, bar_chart(top(by_host, 12))))
    parts.append('<div class="panel"><h2>走哪条线（按出口节点）</h2>%s%s</div>'
                 % (LEGEND, bar_chart(top(by_chain, 10))))

    # 循环下载表
    parts.append('<div class="panel"><h2>循环下载检测</h2>')
    if reps:
        parts.append('<table><thead><tr><th>域名</th><th class="n">事件数</th>'
                     '<th class="n">单次</th><th class="n">累计</th>'
                     '<th class="n">间隔</th><th>判定</th></tr></thead><tbody>')
        for x in reps[:15]:
            tag = ('<span class="tag bad">循环下载</span>' if x["periodic"]
                   else '<span class="tag warn">偶发</span>')
            parts.append('<tr><td>%s</td><td class="n">%d</td><td class="n">%s</td>'
                         '<td class="n">%s</td><td class="n">%.0f分</td><td>%s</td></tr>'
                         % (html.escape(x["host"]), x["events"], human(x["avg_size"]),
                            human(x["total"]), x["gap"], tag))
        parts.append('</tbody></table>')
    else:
        parts.append('<p class="empty">没有发现重复下载行为</p>')
    parts.append('</div>')

    # 浪费
    parts.append('<div class="panel"><h2>本该直连却走了代理</h2>')
    if waste:
        s = sum(x["total"] for x in waste)
        parts.append('<table><thead><tr><th>域名</th><th>类型</th>'
                     '<th class="n">流量</th></tr></thead><tbody>')
        for x in waste[:15]:
            parts.append('<tr><td>%s</td><td><span class="tag warn">%s</span></td>'
                         '<td class="n">%s</td></tr>'
                         % (html.escape(x["host"]), x["label"], human(x["total"])))
        parts.append('</tbody></table>')
        parts.append('<p style="color:var(--warn);margin:12px 0 0">'
                     '合计可省 %s</p>' % human(s))
    else:
        parts.append('<p class="empty">分流规则干净，没有发现浪费</p>')
    parts.append('</div>')

    parts.append('<div class="foot">TrafficProbe · 数据来自 Clash/mihomo API 采样<br>'
                 '统计口径：只有走代理的流量扣机场配额，直连仅消耗本地带宽</div>')
    parts.append("</div></body></html>")
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser(description="生成流量 HTML 报告")
    ap.add_argument("--log", default=None)
    ap.add_argument("--from", dest="time_from", default=None)
    ap.add_argument("--to", dest="time_to", default=None)
    ap.add_argument("--last", default=None)
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    log = a.log or os.path.join(data_dir(), "traffic.jsonl")
    if not os.path.exists(log):
        print("找不到日志：%s" % log)
        return 1
    if a.last:
        t_to = datetime.now()
        t_from = t_to - parse_last(a.last)
    elif a.time_from:
        t_from = parse_dt(a.time_from)
        t_to = parse_dt(a.time_to) if a.time_to else datetime.now()
        if a.time_to and len(a.time_to.strip()) == 10:
            t_to = t_to + timedelta(days=1) - timedelta(seconds=1)
    else:
        t_to = datetime.now()
        t_from = t_to - timedelta(hours=24)

    rows = load(log, t_from, t_to)
    if not rows:
        print("这个时间段没有数据：%s ~ %s" % (t_from, t_to))
        return 1

    doc = build(rows, t_from, t_to)
    out = a.out or os.path.join(os.path.dirname(log),
                                "report_%s.html" % datetime.now().strftime("%Y%m%d_%H%M"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print("已生成：%s（%.1f KB，%d 条采样）"
          % (out, os.path.getsize(out) / 1024, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
