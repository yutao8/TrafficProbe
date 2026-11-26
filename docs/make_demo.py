# -*- coding: utf-8 -*-
"""从真实录制数据生成示例报告。

只做最小必要脱敏：替换公司内网域名，其余（软件名、公共域名）保留原样。

用法:
    python docs/make_demo.py
    python docs/make_demo.py --src <jsonl> --out x.html
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 仅替换公司内网域名，其余一律保留
HOST_MAP = {
    "erp.ltepcb.com": "erp.example-corp.com",
    "office.ltepcb.com": "office.example-corp.com",
}


def load(src):
    rows = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            r["_dt"] = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
            rows.append(r)
    return rows


def sanitize(rows):
    for r in rows:
        for it in r["items"]:
            it["h"] = HOST_MAP.get(it["h"], it["h"])
    return rows


def main():
    ap = argparse.ArgumentParser(description="生成脱敏示例报告")
    ap.add_argument("--src", default=os.path.join(
        os.environ.get("APPDATA", ""), "TrafficProbe", "traffic.jsonl"))
    ap.add_argument("--out", default=os.path.join(HERE, "sample-report.html"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.src):
        print("找不到源数据：%s" % a.src)
        return 1

    rows = sanitize(load(a.src))

    import report_html
    doc = report_html.build(rows, rows[0]["_dt"], rows[-1]["_dt"])

    # 只检查公司内网标识是否残留
    hits = [k for k in ("ltepcb",) if k in doc.lower()]
    if hits:
        print("!! 内网标识残留：%s" % hits)
        return 1

    if a.check:
        print("检查通过，未写入")
        return 0

    with open(a.out, "w", encoding="utf-8") as f:
        f.write(doc)

    total = sum(r["up"] + r["down"] for r in rows)
    hosts = set(m["h"] for r in rows for m in r["items"])
    procs = set(m["p"] for r in rows for m in r["items"])
    span = (rows[-1]["_dt"] - rows[0]["_dt"]).total_seconds() / 3600
    print("已生成 %s" % a.out)
    print("  采样点 %d，跨度 %.1f 小时" % (len(rows), span))
    print("  进程 %d 个，域名 %d 个，总流量 %.2f GB"
          % (len(procs), len(hosts), total / 1024 ** 3))
    return 0


if __name__ == "__main__":
    sys.exit(main())
