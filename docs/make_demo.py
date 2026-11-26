# -*- coding: utf-8 -*-
"""生成一份完全合成的示例数据与报告，用于 README 展示和 CI 演示。

所有进程名、域名、IP 都是虚构的，不含任何真实使用痕迹。

用法:
    python make_demo.py                    # 生成 docs/sample-report.html
    python make_demo.py -o other.html
"""
import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

random.seed(20260101)   # 固定种子，保证每次生成一致

# 虚构的进程与域名组合，括号内是"像什么"，不指向真实产品
FAKE_APPS = [
    ("editor.exe",        ["api.example-llm.com", "cdn.example-llm.com"],      "proxy"),
    ("browser.exe",       ["video.example-stream.net", "news.example-site.org"], "proxy"),
    ("browser.exe",       ["www.example-cn.com", "img.example-cdn.cn"],          "direct"),
    ("code-helper.exe",   ["update.example-helper.io"],                          "proxy"),
    ("sync-agent.exe",    ["sync.example-cloud.com"],                            "proxy"),
    ("sync-agent.exe",    ["mirror.example-npm.cn"],                             "direct"),
    ("chat-app.exe",      ["file.example-messenger.cn"],                         "direct"),
    ("terminal.exe",      ["objects.example-repo.io"],                           "proxy"),
    ("media-player.exe",  ["stream.example-video.net"],                          "proxy"),
    ("editor.exe",        ["mirror.example-cn.cn"],                              "proxy"),
    ("updater.exe",       ["mirror.example-update.io"],                          "proxy"),
]

CHAINS_PROXY = ["JP-Tokyo-01", "JP-Tokyo-03", "HK-HongKong-02"]
CHAINS_DIRECT = ["DIRECT"]


def build_rows(hours=20, interval=300):
    """生成合成采样，interval 为采样间隔（秒）。"""
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    end = now
    start = end - timedelta(hours=hours)
    rows = []
    t = start
    step = timedelta(seconds=interval)

    while t <= end:
        hour = t.hour
        # 白天流量高，深夜低
        busy = 1.0 if 9 <= hour <= 23 else 0.15
        items = []
        total = [0, 0]
        for proc, hosts, mode in FAKE_APPS:
            host = random.choice(hosts)
            if random.random() > 0.35 * busy:
                continue
            base = random.randint(900_000, 9_000_000) * busy
            down = int(base)
            # updater.exe 制造典型故障：每小时整点重下一次约 105MB 的包
            if proc == "updater.exe":
                if t.minute != 0:
                    continue
                down = 105 * 1024 * 1024 + random.randint(-1_500_000, 1_500_000)
            up = int(down * random.uniform(0.02, 0.12))
            chain = random.choice(CHAINS_PROXY if mode == "proxy" else CHAINS_DIRECT)
            items.append({"p": proc, "h": host, "c": chain, "u": up, "d": down})
            total[0] += up
            total[1] += down
        if items:
            rows.append({"ts": t.strftime("%Y-%m-%d %H:%M:%S"),
                         "up": total[0], "down": total[1], "items": items})
        t += step
    return rows


def main():
    ap = argparse.ArgumentParser(description="生成合成示例报告")
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "sample-report.html"))
    a = ap.parse_args()

    import report_html
    rows = build_rows()
    for r in rows:
        r["_dt"] = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")

    t_from = rows[0]["_dt"]
    t_to = rows[-1]["_dt"]
    doc = report_html.build(rows, t_from, t_to)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(doc)

    # 统计一下，便于核对
    import re
    hosts = set(m["h"] for r in rows for m in r["items"])
    procs = set(m["p"] for r in rows for m in r["items"])
    total = sum(r["up"] + r["down"] for r in rows)
    print("已生成 %s" % a.out)
    print("  采样点 %d，时间跨度 %.1f 小时" % (len(rows), (t_to - t_from).total_seconds() / 3600))
    print("  进程 %d 个，域名 %d 个，总流量 %.1f GB"
          % (len(procs), len(hosts), total / 1024 ** 3))
    real = [h for h in hosts if not re.search(
        r"example|^www\.example", h or "")]
    print("  非示例域名（应为空）: %s" % (real or "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
