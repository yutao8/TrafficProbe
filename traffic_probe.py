# -*- coding: utf-8 -*-
"""流量探针 v2.0 —— 统计 Clash / mihomo 的分流流量，并自动找出异常。

单文件、零依赖（只用标准库）。可双击运行，也可命令行调用。

v2.0 相比 v1.0 的改进：
  1. 进程归因补全：metadata 里没有进程名时，用系统 TCP 表按本地端口反查 PID，
     解决大量流量被记成 "?" 的问题。
  2. find-repeats：自动检测"每隔 N 分钟下载一次同样大小的包"这类循环浪费。
     本次实测就是靠这个发现某个工具每小时重复下载 110MB。
  3. --waste：列出走代理但其实可以直连的域名（国内站、GitHub release 等）。
  4. --by day：按天汇总，便于对账月度配额。
  5. --csv：导出给 Excel 看。
  6. prune：按保留天数清理日志，避免无限增长。
  7. live：实时看当前哪条连接在吃流量。

命令行:
    TrafficProbe.exe                      # 菜单
    TrafficProbe.exe record
    TrafficProbe.exe report --last 24h --by all
    TrafficProbe.exe find-repeats --hours 24
    TrafficProbe.exe report --last 7d --waste
    TrafficProbe.exe report --last 7d --by day --csv out.csv
    TrafficProbe.exe live
    TrafficProbe.exe prune --keep 30
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    os.system("chcp 65001 > nul")

APP = "流量探针"
VER = "2.1"
DEFAULT_PORTS = [9090, 9097, 9091, 7890]
CONF_NAME = "traffic_probe.conf.json"
LOG_NAME = "traffic.jsonl"

# 一眼就该直连的域名特征（走代理就是浪费配额）
DIRECT_HINTS = [
    (r"(^|\.)qq\.com$", "腾讯"),
    (r"(^|\.)qq\.com\.cn$", "腾讯"),
    (r"(^|\.)myqcloud\.com$", "腾讯云"),
    (r"(^|\.)baidu\.com$", "百度"),
    (r"(^|\.)bdstatic\.com$", "百度静态"),
    (r"(^|\.)taobao\.com$|(^|\.)tmall\.com$|(^|\.)alicdn\.com$", "阿里"),
    (r"(^|\.)aliyun\.com$|(^|\.)aliyuncs\.com$", "阿里云"),
    (r"(^|\.)bilibili\.com$|(^|\.)hdslb\.com$", "B站"),
    (r"(^|\.)jd\.com$|(^|\.)360buyimg\.com$", "京东"),
    (r"(^|\.)npmmirror\.com$", "npm 国内镜像"),
    (r"(^|\.)pypi\.tuna\.tsinghua\.edu\.cn$", "清华源"),
    (r"^release-assets\.githubusercontent\.com$", "GitHub Release 大文件"),
    (r"(^|\.)cn$", "国内域名"),
]


# ---------------- 基础 ----------------

def app_dir():
    """程序所在目录（可写位置：放日志、配置）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def res_dir():
    """只读资源目录。PyInstaller onefile 会把 --add-data 解到 sys._MEIPASS。

    打包后图标、report_html.py、tray.py 都在这里，
    不能拿 app_dir() 去找，否则找不到。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and os.path.isdir(meipass):
        return meipass
    return os.path.dirname(os.path.abspath(__file__))


def asset(*parts):
    """按优先级找资源：打包目录 → 程序目录 → 源码目录。"""
    for base in (getattr(sys, "_MEIPASS", None), app_dir(),
                 os.path.dirname(os.path.abspath(__file__))):
        if not base:
            continue
        p = os.path.join(base, *parts)
        if os.path.exists(p):
            return p
    return os.path.join(res_dir(), *parts)


def conf_path():
    return os.path.join(data_dir(), CONF_NAME)


def load_conf():
    try:
        with open(conf_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_conf(c):
    try:
        with open(conf_path(), "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def data_dir():
    r"""数据目录：放在用户级应用目录，跨平台且不受程序安装位置影响。

    Windows : %APPDATA%\TrafficProbe
    macOS   : ~/Library/Application Support/TrafficProbe
    Linux   : $XDG_DATA_HOME/TrafficProbe 或 ~/.local/share/TrafficProbe

    可用环境变量 TRAFFIC_PROBE_HOME 覆盖（便携模式/测试用）。
    旧版数据在程序旁的 data/ 目录，首次运行会自动迁移过来。
    """
    env = os.environ.get("TRAFFIC_PROBE_HOME")
    if env:
        d = env
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "TrafficProbe")
    elif sys.platform == "darwin":
        d = os.path.join(os.path.expanduser("~"), "Library",
                         "Application Support", "TrafficProbe")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
        d = os.path.join(base, "TrafficProbe")
    try:
        os.makedirs(d, exist_ok=True)
        _migrate_legacy(d)
    except Exception:
        return app_dir()
    return d


def _migrate_legacy(target):
    """把旧版放在程序旁的 data/*.jsonl 搬到用户目录，只做一次。"""
    src = os.path.join(app_dir(), "data")
    if os.path.abspath(src) == os.path.abspath(target) or not os.path.isdir(src):
        return
    marker = os.path.join(target, ".migrated_from_portable")
    if os.path.exists(marker):
        return
    moved = 0
    for name in os.listdir(src):
        if not (name.endswith(".jsonl") or name.endswith(CONF_NAME)):
            continue
        sp, tp = os.path.join(src, name), os.path.join(target, name)
        if os.path.exists(tp):
            continue
        try:
            os.replace(sp, tp) if not os.path.exists(tp) else None
            moved += 1
        except Exception:
            try:
                import shutil
                shutil.copy2(sp, tp)
                moved += 1
            except Exception:
                pass
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("migrated %d files at %s\n" % (moved, datetime.now()))
    except Exception:
        pass


def resolve_log(arg=None):
    if arg:
        return arg
    c = load_conf()
    if c.get("log"):
        return c["log"]
    return os.path.join(data_dir(), LOG_NAME)


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f%s" % (n, unit)
        n /= 1024.0
    return "%.1fTB" % n


def api_call(base, path, secret=None):
    req = urllib.request.Request(base + path)
    if secret:
        req.add_header("Authorization", "Bearer " + secret)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def detect(secret=None, port=None):
    for p in ([port] if port else DEFAULT_PORTS):
        base = "http://127.0.0.1:%d" % p
        try:
            return base, api_call(base, "/version", secret)
        except Exception:
            continue
    return None, None


# ---------------- 进程归因 ----------------

class PidResolver:
    """metadata 没有进程名时，用本地端口反查进程。

    Windows: netstat -ano 拿 本地端口 -> PID，tasklist 拿 PID -> 进程名。
    两者都做缓存，且限制刷新频率，避免每秒起子进程。
    """

    def __init__(self, ttl=30):
        self.ttl = ttl
        self._port2pid = {}
        self._pid2name = {}
        self._t_port = 0
        self._t_name = 0
        self.enabled = sys.platform == "win32"

    def _refresh_ports(self):
        if time.time() - self._t_port < self.ttl:
            return
        self._t_port = time.time()
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True,
                                 encoding="gbk", errors="ignore", timeout=8).stdout
        except Exception:
            return
        port2pid = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            local = parts[1]
            pid = parts[-1]
            if ":" not in local or not pid.isdigit():
                continue
            port = local.rsplit(":", 1)[1]
            if port.isdigit():
                port2pid.setdefault(int(port), int(pid))
        if port2pid:
            self._port2pid = port2pid

    def _refresh_names(self):
        if time.time() - self._t_name < self.ttl:
            return
        self._t_name = time.time()
        try:
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True,
                                 encoding="gbk", errors="ignore", timeout=10).stdout
        except Exception:
            return
        m = {}
        for row in csv.reader(out.splitlines()):
            if len(row) >= 2 and row[1].strip().isdigit():
                m[int(row[1])] = row[0].strip()
        if m:
            self._pid2name = m

    def name_by_port(self, port):
        if not self.enabled or not port:
            return None
        try:
            port = int(port)
        except (TypeError, ValueError):
            return None
        self._refresh_ports()
        pid = self._port2pid.get(port)
        if not pid:
            return None
        self._refresh_names()
        nm = self._pid2name.get(pid)
        return nm or ("pid:%d" % pid)


# ---------------- 录制 ----------------

def pick_proc(md, resolver):
    """从 metadata 拿进程名，拿不到就按本地端口反查。"""
    raw = md.get("processPath") or md.get("process") or ""
    if raw:
        nm = os.path.basename(raw.replace("\\", "/"))
        if nm:
            return nm
    nm = resolver.name_by_port(md.get("sourcePort"))
    return nm or "?"


class Recorder(threading.Thread):
    """后台录制线程。可被 stop() 优雅停止。"""

    def __init__(self, base, secret, log_path, interval, quiet=False, on_tick=None):
        super().__init__(daemon=True)
        self.base = base
        self.secret = secret
        self.log_path = log_path
        self.interval = interval
        self.quiet = quiet
        self.on_tick = on_tick          # 回调 (本次上行, 本次下行, 总行数)
        self._halt_event = threading.Event()
        self.rows_written = 0
        self.unknown = 0
        self.fails = 0
        self.last_total = [0, 0]        # 最近一次采样的上下行
        self.spawn = None               # 当前连接的机场入口（用于提示）

    def stop(self):
        self._halt_event.set()

    def run(self):
        resolver = PidResolver()
        last = {}
        t0 = time.time()
        while not self._halt_event.is_set():
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                snap = api_call(self.base, "/connections", self.secret)
            except Exception as e:
                self.fails += 1
                if not self.quiet and self.fails % 10 == 1:
                    print("  [%s] 连接失败，稍后重试：%s" % (ts, e))
                self._halt_event.wait(self.interval)
                continue
            conns = snap.get("connections") or []
            rows, tot = {}, [0, 0]
            for c in conns:
                cid = c.get("id")
                up, down = c.get("upload", 0), c.get("download", 0)
                prev = last.get(cid)
                du, dd = (up - prev[0], down - prev[1]) if prev else (up, down)
                if du < 0 or dd < 0:
                    du, dd = up, down
                last[cid] = (up, down)
                if not (du or dd):
                    continue
                md = c.get("metadata") or {}
                proc = pick_proc(md, resolver)
                host = md.get("host") or md.get("sniffHost") or md.get("destinationIP") or "?"
                chain = (c.get("chains") or ["DIRECT"])[0]
                v = rows.setdefault((proc, host, chain), [0, 0])
                v[0] += du
                v[1] += dd
                tot[0] += du
                tot[1] += dd
            alive = set(c.get("id") for c in conns)
            last = {k: v for k, v in last.items() if k in alive}
            if rows:
                items = [{"p": p, "h": h, "c": ch, "u": u, "d": d}
                         for (p, h, ch), (u, d) in rows.items()]
                self.unknown += sum(1 for it in items if it["p"] == "?")
                rec = {"ts": ts, "up": tot[0], "down": tot[1], "items": items}
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self.rows_written += 1
            self.last_total = tot
            if self.on_tick:
                try:
                    self.on_tick(tot[0], tot[1], self.rows_written)
                except Exception:
                    pass
            if self.rows_written and self.rows_written % 12 == 0 and not self.quiet:
                mins = (time.time() - t0) / 60.0
                print("  [%s] 已记录 %d 次 | 本次 ↑%s ↓%s | 运行 %.0f 分钟"
                      % (ts, self.rows_written, human(tot[0]), human(tot[1]), mins))
            self._halt_event.wait(self.interval)


def do_record(args, quiet=False, tray=False):
    conf = load_conf()
    secret = args.secret or conf.get("secret")
    port = args.port or conf.get("port")
    base, ver = detect(secret, port)
    if not base:
        print("× 没找到 Clash/mihomo 的 API。请检查：")
        print("  1. Clash Verge / mihomo 正在运行")
        print("  2. 设置里打开了「外部控制器」(external-controller)")
        print("  3. 端口一般是 9090，不对就用菜单 c 改")
        return 1
    lp = resolve_log(args.log)

    if tray:
        return run_tray(base, ver, secret, lp, args.interval)

    print("√ 已连接 %s   版本 %s" % (base, ver.get("version", "?")))
    print("  录制到 %s" % lp)
    print("  每 %g 秒采样一次，按 Ctrl+C 停止" % args.interval)
    print("  进程归因：%s" % ("已启用（端口反查）" if PidResolver().enabled else "仅靠 metadata"))
    print()
    rec = Recorder(base, secret, lp, args.interval, quiet=quiet)
    rec.start()
    try:
        while rec.is_alive():
            rec.join(0.5)
    except KeyboardInterrupt:
        rec.stop()
        rec.join(3)
        print("")
        print("已停止。%d 条记录写入 %s" % (rec.rows_written, lp))
        if rec.unknown:
            print("提示：有 %d 条记录没识别到进程名（已尝试端口反查）" % rec.unknown)
        if rec.fails:
            print("采样失败 %d 次" % rec.fails)
    return 0


def run_tray(base, ver, secret, lp, interval):
    """托盘模式：关掉控制台也能继续录制。"""
    try:
        for _p in (res_dir(), app_dir()):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from tray import Tray, hide_console, acquire_single_instance, msgbox
    except Exception as e:
        print("× 托盘组件不可用（%s），退回普通录制模式" % e)
        return do_record(argparse.Namespace(secret=secret, port=None, log=lp,
                                           interval=interval), quiet=True)

    if not acquire_single_instance():
        msgbox("已经在托盘中运行了，不重复启动。")
        return 0

    rec = Recorder(base, secret, lp, interval, quiet=True)
    rec.start()
    hide_console()

    icon = asset("assets", "trafficprobe.ico")
    tray_ref = {}

    def show_stats():
        rows = rec.rows_written
        up, down = rec.last_total
        msg = "运行中：已记录 %d 次采样" % rows
        msg += "\n最近一次：↑ %s   ↓ %s" % (human(up), human(down))
        msg += "\n\n数据文件：\n%s" % lp
        msgbox(msg, "流量探针")

    def open_folder():
        try:
            os.startfile(os.path.dirname(lp))
        except Exception:
            pass

    def quit_all():
        rec.stop()
        rec.join(2)
        tray_ref["t"].quit()

    handlers = {1: show_stats, 2: open_folder, 3: quit_all}
    menu = [(1, "查看状态"), (2, "打开数据文件夹"), None, (3, "退出")]
    t = Tray("流量探针 · 录制中", icon, menu, handlers, on_double_click=show_stats)
    tray_ref["t"] = t

    def tick(du, dd, rows):
        if rows and rows % 6 == 0:
            t.set_tip("流量探针 · %d 次 | 上 %s 下 %s"
                      % (rows, human(du), human(dd)))

    rec.on_tick = tick
    return t.run()


# ---------------- 读取 ----------------

def parse_dt(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit("× 时间格式看不懂：%s（用 2026-11-25 09:00 或 2026-11-25）" % s)


def parse_last(s):
    u = s[-1].lower()
    try:
        n = float(s[:-1])
    except ValueError:
        raise SystemExit("× --last 写成 24h / 7d / 30m")
    if u == "m":
        return timedelta(minutes=n)
    if u == "h":
        return timedelta(hours=n)
    if u == "d":
        return timedelta(days=n)
    raise SystemExit("× 单位只支持 m / h / d")


def load_rows(path, t_from=None, t_to=None):
    out = []
    if not os.path.exists(path):
        return out
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
            if t_from and ts < t_from:
                continue
            if t_to and ts > t_to:
                continue
            r["_dt"] = ts
            out.append(r)
    return out


def window(args):
    if getattr(args, "last", None):
        t_to = datetime.now()
        return t_to - parse_last(args.last), t_to
    tf = getattr(args, "time_from", None)
    if tf:
        t_from = parse_dt(tf)
        tt = getattr(args, "time_to", None)
        t_to = parse_dt(tt) if tt else datetime.now()
        if tt and len(tt.strip()) == 10:
            t_to = t_to + timedelta(days=1) - timedelta(seconds=1)
        return t_from, t_to
    raise SystemExit("× 需要 --last 或 --from")


# ---------------- 报表 ----------------

DIMS = {"process": "按进程（谁在用）", "host": "按域名（连到哪）",
        "chain": "按出口（走哪条线）", "hour": "按小时（什么时候用）",
        "day": "按天（对账用）"}


def key_of(it, r, dim):
    if dim == "process":
        return it["p"]
    if dim == "host":
        return it["h"]
    if dim == "chain":
        return it["c"]
    if dim == "hour":
        return r["_dt"].strftime("%m-%d %H时")
    return r["_dt"].strftime("%Y-%m-%d")


def do_report(args):
    lp = resolve_log(args.log)
    if not os.path.exists(lp):
        print("还没有录制数据：%s" % lp)
        print("先选「1) 开始录制」，让它跑一段时间再来查。")
        return 1
    t_from, t_to = window(args)
    rows = load_rows(lp, t_from, t_to)
    if not rows:
        print("这个时间段没有记录：%s ~ %s" % (t_from, t_to))
        do_span(argparse.Namespace(log=lp), quiet=True)
        return 1

    span_min = max((rows[-1]["_dt"] - rows[0]["_dt"]).total_seconds() / 60.0, 1.0 / 60)
    prox = [0, 0]
    drct = [0, 0]
    for r in rows:
        for it in r["items"]:
            t = drct if it["c"] == "DIRECT" else prox
            t[0] += it["u"]
            t[1] += it["d"]
    tot = prox[0] + prox[1] + drct[0] + drct[1]

    print("=" * 70)
    print("时间段：%s  ~  %s" % (rows[0]["_dt"], rows[-1]["_dt"]))
    print("覆盖：%.0f 分钟，%d 个采样点" % (span_min, len(rows)))
    print("走代理：↑ %s   ↓ %s   合计 %s"
          % (human(prox[0]), human(prox[1]), human(prox[0] + prox[1])))
    print("直连：  ↑ %s   ↓ %s   合计 %s"
          % (human(drct[0]), human(drct[1]), human(drct[0] + drct[1])))
    if tot:
        print("代理占比：%.1f%%（只有这部分扣机场配额）"
              % ((prox[0] + prox[1]) / tot * 100))
    if span_min >= 30:
        per_day = (prox[0] + prox[1]) / span_min * 60 * 24 / 1024 ** 3
        print("代理折算：%.2f GB/天  →  %.0f GB/月" % (per_day, per_day * 30))
    print("=" * 70)

    dims = [(args.by, DIMS[args.by])] if args.by != "all" else list(DIMS.items())
    table = []
    for dim, title in dims:
        agg = {}
        for r in rows:
            for it in r["items"]:
                k = key_of(it, r, dim)
                v = agg.setdefault(k, [0, 0])
                v[0] += it["u"]
                v[1] += it["d"]
        print("\n%s" % title)
        print("-" * 70)
        print("%-44s %9s %9s %9s" % ("", "上行", "下行", "合计"))
        for k, (u, d) in sorted(agg.items(), key=lambda kv: kv[1][0] + kv[1][1],
                                reverse=True)[:args.top]:
            print("%-44s %9s %9s %9s" % (str(k)[:44], str(human(u)),
                                          human(d), human(u + d)))
            table.append({"维度": dim, "名称": k, "上行": u, "下行": d, "合计": u + d})

    if args.waste:
        show_waste(rows)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["维度", "名称", "上行", "下行", "合计"])
            w.writeheader()
            w.writerows(table)
        print("\n已导出 %s（%d 行）" % (args.csv, len(table)))
    print()
    return 0


def show_waste(rows):
    """列出走代理但本该直连的域名。"""
    agg = {}
    for r in rows:
        for it in r["items"]:
            if it["c"] == "DIRECT":
                continue
            agg.setdefault(it["h"], [0, 0, set()])
            agg[it["h"]][0] += it["u"]
            agg[it["h"]][1] += it["d"]
            agg[it["h"]][2].add(it["p"])
    hits = []
    for h, (u, d, ps) in agg.items():
        for pat, label in DIRECT_HINTS:
            if re.search(pat, h, re.I):
                hits.append((h, label, u, d, ps))
                break
    print("\n可直连却走了代理（纯浪费配额）")
    print("-" * 70)
    if not hits:
        print("  没有发现。分流规则是干净的。")
        return
    print("%-42s %-12s %9s" % ("域名", "类型", "合计"))
    total = 0
    for h, label, u, d, ps in sorted(hits, key=lambda x: -(x[2] + x[3]))[:20]:
        print("%-42s %-12s %9s" % (h[:42], label, human(u + d)))
        total += u + d
    print("-" * 70)
    print("合计可省：%s" % human(total))


# ---------------- 异常检测 ----------------

def do_find_repeats(args):
    """找出"每隔固定时间下载同样大小"的循环浪费。"""
    lp = resolve_log(args.log)
    if not os.path.exists(lp):
        print("还没有录制数据：%s" % lp)
        return 1
    t_to = datetime.now()
    t_from = t_to - parse_last(args.hours)
    rows = load_rows(lp, t_from, t_to)
    if len(rows) < 10:
        print("数据太少（%d 个采样点），至少录 30 分钟再看" % len(rows))
        return 1

    # 按域名收集"爆发"，并把 5 分钟内的多次爆发合并成一次"事件"
    # （同一个更新包会分成 4~5 个分片，间隔几秒；不合并会被算成多次独立下载）
    thr = args.threshold * 1024 * 1024
    raw = {}
    for r in rows:
        for it in r["items"]:
            if it["c"] == "DIRECT" or it["d"] < thr:
                continue
            raw.setdefault(it["h"], []).append((r["_dt"], it["d"]))

    MERGE_SEC = 300

    def merge(pts):
        pts = sorted(pts)
        ev = []
        for t, sz in pts:
            if ev and (t - ev[-1][0]).total_seconds() <= MERGE_SEC:
                ev[-1] = (ev[-1][0], ev[-1][1] + sz, ev[-1][2] + 1)
            else:
                ev.append((t, sz, 1))
        return ev

    print("=" * 70)
    print("异常检测：%s ~ %s（%d 个采样点）"
          % (rows[0]["_dt"], rows[-1]["_dt"], len(rows)))
    print("判定标准：同一域名单次下行 >%.0fMB，重复出现；%.0f 分钟内的合并为一次事件"
          % (args.threshold, MERGE_SEC / 60))
    print("=" * 70)

    found = 0
    for host, pts in sorted(raw.items(), key=lambda kv: -sum(x[1] for x in kv[1])):
        events = merge(pts)
        if len(events) < args.min_times:
            continue
        times = [e[0] for e in events]
        sizes = [e[1] for e in events]
        gaps = [(times[i + 1] - times[i]).total_seconds() / 60
                for i in range(len(times) - 1)]
        if not gaps:
            continue
        # 只看"连续两小时内"的间隔：夜间休眠造成的长空档会把波动率拉爆，误杀规律判断
        short = [g for g in gaps if g <= 120]
        base_gaps = short if len(short) >= 2 else gaps
        avg_gap = sum(base_gaps) / len(base_gaps)
        mean = avg_gap or 1
        var = sum((g - mean) ** 2 for g in base_gaps) / len(base_gaps)
        cv = (var ** 0.5) / mean if mean else 9
        avg_size = sum(sizes) / len(sizes)
        total = sum(sizes)
        periodic = cv < 0.3 and 5 <= avg_gap <= 24 * 60

        found += 1
        print("")
        print("%s" % host)
        print("  触发 %d 次事件（原始 %d 个采样），单次约 %s，累计 %s"
              % (len(events), len(pts), human(avg_size), human(total)))
        print("  平均间隔 %.0f 分钟（波动 %.0f%%，按 %d 个有效间隔算）%s"
              % (avg_gap, cv * 100, len(base_gaps),
                 "  ← 高度规律" if periodic else ""))
        if periodic:
            span_days = max((times[-1] - times[0]).total_seconds() / 86400, 0.01)
            per_day = total / span_days
            print("  ★ 判定：循环下载，折算 %.2f GB/天，约 %.0f GB/月"
                  % (per_day / 1024 ** 3, per_day * 30 / 1024 ** 3))
            print("    建议：关掉该程序的自动更新，或把域名加进直连规则")

    if not found:
        print("\n没有发现重复下载。")
    else:
        print("\n共 %d 个域名存在重复下载行为。" % found)
    return 0


# ---------------- 实时 ----------------

def do_live(args):
    conf = load_conf()
    secret = args.secret or conf.get("secret")
    port = args.port or conf.get("port")
    base, ver = detect(secret, port)
    if not base:
        print("× 没连上 Clash/mihomo API")
        return 1
    print("实时监控（Ctrl+C 退出），每 %g 秒刷新\n" % args.interval)
    last = {}
    try:
        while True:
            snap = api_call(base, "/connections", secret)
            conns = snap.get("connections") or []
            now = [0, 0]
            rows = []
            for c in conns:
                cid = c.get("id")
                up, down = c.get("upload", 0), c.get("download", 0)
                prev = last.get(cid)
                du, dd = (up - prev[0], down - prev[1]) if prev else (0, 0)
                last[cid] = (up, down)
                if du <= 0 and dd <= 0:
                    continue
                md = c.get("metadata") or {}
                raw = md.get("processPath") or md.get("process") or "?"
                proc = os.path.basename(raw.replace("\\", "/")) or "?"
                host = md.get("host") or md.get("destinationIP") or "?"
                chain = (c.get("chains") or ["DIRECT"])[0]
                rows.append((du + dd, proc, host, chain))
                now[0] += du
                now[1] += dd
            alive = set(c.get("id") for c in conns)
            last = {k: v for k, v in last.items() if k in alive}
            print("\033[2J\033[H", end="")
            print("活跃连接 %d 条   本次 ↑%s ↓%s"
                  % (len(conns), human(now[0]), human(now[1])))
            print("-" * 76)
            print("%-22s %-34s %-10s %9s" % ("进程", "域名", "出口", "本次"))
            for t, p, h, ch in sorted(rows, reverse=True)[:args.top]:
                print("%-22s %-34s %-10s %9s" % (p[:22], h[:34], ch[:10], human(t)))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已退出")
    return 0


# ---------------- 维护 ----------------

def do_prune(args):
    lp = resolve_log(args.log)
    if not os.path.exists(lp):
        print("没有日志文件：%s" % lp)
        return 1
    cutoff = datetime.now() - timedelta(days=args.keep)
    kept, dropped = [], 0
    with open(lp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                dropped += 1
                continue
            if ts >= cutoff:
                kept.append(line)
    before = os.path.getsize(lp) / 1024 ** 2
    with open(lp, "w", encoding="utf-8") as f:
        for line in kept:
            f.write(line + "\n")
    after = os.path.getsize(lp) / 1024 ** 2
    print("保留最近 %d 天：%d 条，丢弃 %d 条" % (args.keep, len(kept), dropped))
    print("文件：%.1f MB → %.1f MB" % (before, after))
    return 0


def do_html(args):
    """调用 report_html 生成单文件 HTML 报告，并尝试用默认浏览器打开。"""
    try:
        for _p in (res_dir(), app_dir()):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        import report_html
    except Exception as e:
        print("× 生成 HTML 需要同目录的 report_html.py（%s）" % e)
        return 1
    lp = resolve_log(getattr(args, "log", None))
    if not os.path.exists(lp):
        print("还没有录制数据：%s" % lp)
        return 1
    t_from, t_to = window(args)
    rows = report_html.load(lp, t_from, t_to)
    if not rows:
        print("这个时间段没有数据：%s ~ %s" % (t_from, t_to))
        return 1
    doc = report_html.build(rows, t_from, t_to)
    out = getattr(args, "out", None) or os.path.join(
        data_dir(), "report_%s.html" % datetime.now().strftime("%Y%m%d_%H%M"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print("已生成报告：%s" % out)
    print("  %.1f KB，%d 条采样" % (os.path.getsize(out) / 1024, len(rows)))
    if not getattr(args, "no_open", False):
        try:
            if sys.platform == "win32":
                os.startfile(out)
            elif sys.platform == "darwin":
                import subprocess as sp
                sp.Popen(["open", out])
            else:
                import subprocess as sp
                sp.Popen(["xdg-open", out])
            print("  已用默认浏览器打开")
        except Exception:
            print("  打开浏览器失败，请手动双击上面的文件")
    return 0


def do_span(args, quiet=False):
    lp = resolve_log(getattr(args, "log", None))
    if not os.path.exists(lp):
        if not quiet:
            print("还没有录制数据。先选「1) 开始录制」跑一段时间。")
            print("日志会写到：%s" % lp)
        return 1
    first = last = None
    n = 0
    with open(lp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ts = datetime.strptime(json.loads(line)["ts"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            first = first or ts
            last = ts
            n += 1
    if not first:
        if not quiet:
            print("日志是空的")
        return 1
    if quiet:
        print("（已有数据覆盖 %s ~ %s）" % (first, last))
        return 0
    print("=" * 70)
    print("录制覆盖：%s  ~  %s" % (first, last))
    print("%d 个采样点，%.1f 小时" % (n, (last - first).total_seconds() / 3600))
    print("文件：%s（%.1f MB）" % (lp, os.path.getsize(lp) / 1024 ** 2))
    print("=" * 70)
    return 0


# ---------------- 菜单 ----------------

def ask(prompt, default=""):
    try:
        v = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return v or default


def menu():
    conf = load_conf()
    while True:
        print()
        print("=" * 70)
        print("  %s v%s   统计 Clash / mihomo 的分流流量" % (APP, VER))
        print("=" * 70)
        base, ver = detect(conf.get("secret"), conf.get("port"))
        if base:
            print("  状态：已连接 %s（%s）" % (base, ver.get("version", "?")))
        else:
            print("  状态：× 未连接。先启动 Clash Verge / mihomo，并打开外部控制器")
        do_span(argparse.Namespace(log=resolve_log(None)), quiet=True)
        print("  数据目录：%s" % data_dir())
        print("-" * 70)
        print("  1) 开始录制（占住窗口，Ctrl+C 停）")
        print("  t) 托盘后台录制（关窗口也继续跑）")
        print("  2) 查今天")
        print("  3) 查最近 24 小时")
        print("  4) 查自定义时间段")
        print("  5) 找异常循环下载  ← 定位流量失控")
        print("  6) 看哪些流量本该直连（浪费检查）")
        print("  7) 实时看当前连接")
        print("  8) 按天对账")
        print("  h) 生成 HTML 报告（浏览器打开）")
        print("  9) 清理旧日志")
        print("  c) 设置 API 端口 / 密码")
        print("  0) 退出")
        c = ask("\n  选择：").lower()

        if c == "1":
            iv = ask("  采样间隔秒数（回车=5）：", "5")
            try:
                iv = float(iv)
            except ValueError:
                iv = 5.0
            print()
            do_record(argparse.Namespace(secret=conf.get("secret"), port=conf.get("port"),
                                         log=None, interval=iv), tray=False)
        elif c == "t":
            iv = ask("  采样间隔秒数（回车=5）：", "5")
            try:
                iv = float(iv)
            except ValueError:
                iv = 5.0
            print("")
            print("  正在转入托盘，之后可以安全关闭这个窗口。")
            print("  托盘图标：右键出菜单，双击看状态。")
            time.sleep(1.2)
            do_record(argparse.Namespace(secret=conf.get("secret"), port=conf.get("port"),
                                         log=None, interval=iv), tray=True)
        elif c == "2":
            now = datetime.now()
            do_report(argparse.Namespace(log=None, last=None,
                                         time_from=now.strftime("%Y-%m-%d 00:00"),
                                         time_to=now.strftime("%Y-%m-%d 23:59"),
                                         by="all", top=20, waste=True, csv=None))
        elif c == "3":
            do_report(argparse.Namespace(log=None, last="24h", time_from=None,
                                         time_to=None, by="all", top=15,
                                         waste=True, csv=None))
        elif c == "4":
            f = ask("  开始时间（如 2026-11-25 09:00）：")
            t = ask("  结束时间（回车=现在）：", "")
            dim = ask("  维度 1进程/2域名/3出口/4小时/回车=全部：", "all")
            dim = {"1": "process", "2": "host", "3": "chain", "4": "hour"}.get(dim, "all")
            try:
                do_report(argparse.Namespace(log=None, last=None, time_from=f,
                                             time_to=t or None, by=dim, top=20,
                                             waste=True, csv=None))
            except SystemExit as e:
                print(e)
        elif c == "5":
            h = ask("  回看多少小时（回车=24）：", "24")
            try:
                do_find_repeats(argparse.Namespace(log=None, hours=h + "h",
                                                   threshold=5.0, min_times=3))
            except SystemExit as e:
                print(e)
        elif c == "6":
            do_report(argparse.Namespace(log=None, last="24h", time_from=None,
                                         time_to=None, by="process", top=1,
                                         waste=True, csv=None))
        elif c == "7":
            do_live(argparse.Namespace(secret=conf.get("secret"), port=conf.get("port"),
                                       interval=2.0, top=20))
        elif c == "8":
            do_report(argparse.Namespace(log=None, last="30d", time_from=None,
                                         time_to=None, by="day", top=40,
                                         waste=False, csv=None))
        elif c == "h":
            w = ask("  统计范围 1今天/2最近24小时/3最近7天/回车=24小时：", "2")
            m = {"1": None, "2": "24h", "3": "7d"}.get(w, "24h")
            now = datetime.now()
            try:
                if m:
                    do_html(argparse.Namespace(log=None, last=m, time_from=None,
                                               time_to=None, out=None, no_open=False))
                else:
                    do_html(argparse.Namespace(
                        log=None, last=None,
                        time_from=now.strftime("%Y-%m-%d 00:00"),
                        time_to=now.strftime("%Y-%m-%d 23:59"),
                        out=None, no_open=False))
            except SystemExit as e:
                print(e)
        elif c == "9":
            k = ask("  保留最近多少天（回车=30）：", "30")
            try:
                do_prune(argparse.Namespace(log=None, keep=int(k)))
            except ValueError:
                print("  天数要是整数")
        elif c == "c":
            p = ask("  API 端口（回车=自动探测）：", "")
            s = ask("  面板密码（没设就回车）：", "")
            if p:
                try:
                    conf["port"] = int(p)
                except ValueError:
                    print("  端口要是数字")
                    continue
            else:
                conf.pop("port", None)
            if s:
                conf["secret"] = s
            else:
                conf.pop("secret", None)
            save_conf(conf)
            print("  已保存：%s" % conf_path())
            b2, _ = detect(conf.get("secret"), conf.get("port"))
            print("  测试连接：%s" % ("成功 " + b2 if b2 else "失败"))
        elif c in ("0", "q", ""):
            print("  再见")
            return 0
        else:
            print("  没有这个选项")


def main():
    if len(sys.argv) == 1:
        try:
            return menu()
        except KeyboardInterrupt:
            print()
            return 0

    ap = argparse.ArgumentParser(prog="TrafficProbe", description="统计 Clash/mihomo 分流流量")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="持续录制")
    r.add_argument("--log", default=None)
    r.add_argument("--interval", type=float, default=5.0)
    r.add_argument("--secret", default=None)
    r.add_argument("--port", type=int, default=None)
    r.add_argument("--tray", action="store_true", help="在系统托盘后台运行，关闭窗口也继续录")

    def add_query(p):
        p.add_argument("--log", default=None)
        p.add_argument("--from", dest="time_from", default=None)
        p.add_argument("--to", dest="time_to", default=None)
        p.add_argument("--last", default=None, help="24h / 7d / 30m")
        p.add_argument("--top", type=int, default=15)

    q = sub.add_parser("report", help="查时间段")
    add_query(q)
    q.add_argument("--by", default="all",
                   choices=["process", "host", "chain", "hour", "day", "all"])
    q.add_argument("--waste", action="store_true", help="额外列出本该直连的流量")
    q.add_argument("--csv", default=None, help="导出 CSV")

    f = sub.add_parser("find-repeats", help="找循环下载")
    f.add_argument("--log", default=None)
    f.add_argument("--hours", default="24h")
    f.add_argument("--threshold", type=float, default=5.0, help="单次 MB 阈值")
    f.add_argument("--min-times", type=int, default=3, help="至少重复次数")

    lv = sub.add_parser("live", help="实时看连接")
    lv.add_argument("--interval", type=float, default=2.0)
    lv.add_argument("--top", type=int, default=20)
    lv.add_argument("--secret", default=None)
    lv.add_argument("--port", type=int, default=None)

    pr = sub.add_parser("prune", help="清理旧日志")
    pr.add_argument("--log", default=None)
    pr.add_argument("--keep", type=int, default=30)

    h = sub.add_parser("html", help="生成 HTML 报告")
    h.add_argument("--log", default=None)
    h.add_argument("--from", dest="time_from", default=None)
    h.add_argument("--to", dest="time_to", default=None)
    h.add_argument("--last", default=None, help="24h / 7d")
    h.add_argument("-o", "--out", default=None)
    h.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    s = sub.add_parser("span", help="看录制覆盖")
    s.add_argument("--log", default=None)

    a = ap.parse_args()
    if a.cmd == "record":
        return do_record(a, tray=getattr(a, "tray", False))
    if a.cmd == "report":
        return do_report(a)
    if a.cmd == "find-repeats":
        return do_find_repeats(a)
    if a.cmd == "live":
        return do_live(a)
    if a.cmd == "prune":
        return do_prune(a)
    if a.cmd == "html":
        return do_html(a)
    return do_span(a)


if __name__ == "__main__":
    sys.exit(main())
