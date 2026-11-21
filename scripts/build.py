#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py —— 拉取课程表源数据并生成 ClassIsland 静态集控档案。

数据流：
  源站 API (duanjr.site /tools/class_schedule/api/schedule)
      → 本脚本抓取、校验、转换
      → subjects/{id}.json / timelayouts.json / classplans/{id}.json / presets/
      → settings/default-settings.json（可选，下发学期开始时间）
      → manifest.json（内容变化时自动递增 Version；地址默认按镜像加速改写）
      → git 提交推送 → ClassIsland 客户端按集控清单拉取

两周轮换（单双周）：
  build-config.json 的 week_cycle.enabled 打开后：
    - 周一~周四课表每周生效（TimeRule.WeekCountDiv = 0）；
    - 周五拆成两张课表：返校周（WeekCountDiv = 1，正常时间表）
      与回家周（WeekCountDiv = 2，下午提前：12:20 上课、14:55 放学）；
    - 客户端以「设置 → 常规 → 学期开始时间」（即 SingleWeekStartTime）为起点，
      计算当周处于第 1/2 周，自动切换两张周五课表。该日期 = return_week_monday
      的前一天（周日）；emit_default_settings 打开时，脚本还会生成
      settings/default-settings.json 并经 DefaultSettingsSource 下发，
      使全新安装的机器免手工设置（注意该设置源只在欢迎向导完成前生效）。

零第三方依赖，仅需 Python 3.9+ 标准库。

常用命令：
  python scripts/build.py                       # 在 GitHub Actions 中运行（自动识别仓库与分支）
  python scripts/build.py --repo 用户名/仓库名   # 本地运行（写入 raw.githubusercontent 地址）
  python scripts/build.py --dry-run             # 只计算不写盘
  python scripts/build.py --force-bump          # 内容未变也递增版本号
  python scripts/build.py --no-mirror           # 不走镜像，清单直连 raw.githubusercontent.com
  python scripts/build.py --mirror-host ghfast.top  # 固定某家镜像
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

# ─────────────────────────── 常量 ───────────────────────────

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "build-config.json")
STATE_PATH = os.path.join(REPO_ROOT, "data", "state.json")
SNAPSHOT_DIR = os.path.join(REPO_ROOT, "data", "snapshot")
LAST_UPDATE_JSON = os.path.join(REPO_ROOT, "data", "last-update.json")
LAST_UPDATE_MD = os.path.join(REPO_ROOT, "data", "last-update.md")
ROSTER_MD = os.path.join(REPO_ROOT, "docs", "班级对照表.md")

# ClassIsland 档案中所有 GUID 的派生命名空间（固定值，保证每次构建生成的 ID 稳定）
GUID_NAMESPACE = uuid.UUID("6f1c1b53-2b17-4a5e-9d3a-7f0e2c8d4b21")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"

# 统一 TimeLayout 的 GUID（内容变化不会改变 GUID，仅依赖 manifest 版本号刷新）
TIME_LAYOUT_ID = str(uuid.uuid5(GUID_NAMESPACE, "grade1/timelayout"))
# 「回家周周五」专用 TimeLayout（下午提前放学）
TIME_LAYOUT_HOME_FRIDAY_ID = str(uuid.uuid5(GUID_NAMESPACE, "grade1/timelayout/home-friday"))

# 课表归属：全局课表群（ClassIsland ClassPlanGroup.GlobalGroupGuid = Guid.Empty）。
# 归属全局群后不依赖客户端当前选中的课表群，任何状态下都能参与当日课表匹配。
GLOBAL_GROUP_GUID = ZERO_GUID

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]  # date.weekday(): 0=周一

BEIJING_TZ = timezone(timedelta(hours=8))

# 课间间隔超过该分钟数时用「分割线」而不是「课间休息」
LONG_GAP_MINUTES = 60

# HTTP 抓取参数
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3
USER_AGENT = "ClassIsland-Mgmt-Updater/1.0 (schedule sync)"

# 镜像加速：镜像清单来自 https://git.hiwiki.org/zh-CN/mirrors 背后的测速数据（每 12 小时更新）
MIRROR_LIST_URL = "https://git.hiwiki.org/data/mirror-speeds.json"
RAW_HOST_PREFIX = "https://raw.githubusercontent.com/"
DEFAULT_MIRROR = {
    "enabled": True,          # 默认开启：国内直连 raw.githubusercontent.com 基本不可用
    "host": None,             # 固定某个镜像主机名；None = 自动选最快
    "list_url": MIRROR_LIST_URL,
    "timeout": 10,            # 单个镜像探测超时（秒）
    "probe_candidates": 10,   # 按清单延迟排序后实际探测的镜像数量
    "hysteresis": 1.5,        # 延迟不回落到上次选择的 1.5 倍以内就不切换，避免每天来回改
}

# 默认配置（build-config.json 缺省字段时使用）
CONFIG_DEFAULTS = {
    "source_url": "https://duanjr.site/tools/class_schedule/api/schedule?file=result_actual.json",
    "organization_name": "2026级高一年级",
    "core_version": "2.0.0.0",
    "generate_roster": False,  # 是否生成 docs/班级对照表.md（源 teachers 元数据与课表不一致，默认关闭）
    "exclude_slot_patterns": ["看班"],
    "weekday_names": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
    "week_cycle": None,  # 两周轮换；结构见 build-config.json
    # 是否通过集控清单的 DefaultSettingsSource 下发「学期首日」。
    # 注意：ClassIsland 只在欢迎向导完成前应用该设置源（全新机器），且是整体替换 Settings.json。
    "emit_default_settings": True,
    "mirror": dict(DEFAULT_MIRROR),
}

# 「退场课表」的中和桩：不再生成、但仍需留在客户端档案里的课表会被改写为停用桩，
# 避免"上一次有、这一次没有"的条目在客户端上无限期残留并继续参与当日匹配。
RETIRED_PREFIX = "（已停用）"


# ─────────────────────────── 工具函数 ───────────────────────────


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def guid_of(*parts: str) -> str:
    """由若干字符串稳定地派生一个 GUID。"""
    return str(uuid.uuid5(GUID_NAMESPACE, "|".join(parts)))


def dump_json(obj, path: str) -> None:
    """确定性 JSON 输出：2 空格缩进、保留中文，保证重复构建无 diff 噪音。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_json_file(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    file_cfg = load_json_file(CONFIG_PATH)
    if file_cfg:
        for key, value in file_cfg.items():
            if key.startswith("//"):
                continue
            if key == "mirror" and isinstance(value, dict):
                merged = dict(DEFAULT_MIRROR)
                for mk, mv in value.items():
                    if not str(mk).startswith("//"):
                        merged[mk] = mv
                cfg[key] = merged
            else:
                cfg[key] = value
    return cfg


# ─────────────────────────── 源数据抓取 ───────────────────────────


def _attempt(url: str, verify_level: int) -> bytes:
    """verify_level: 2=严格校验；1=校验证书链但不校验主机名（源站证书域名不匹配时使用）；0=完全跳过校验。"""
    context = None
    if verify_level == 1:
        context = ssl.create_default_context()
        context.check_hostname = False
    elif verify_level == 0:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT, context=context) as response:
        return response.read()


def http_get(url: str) -> bytes:
    """带重试的 HTTP GET。源站证书签发给 www.duanjr.site，与访问域名不一致时自动降级校验级别。"""
    last_error = None
    for attempt in range(1, HTTP_RETRIES + 1):
        for level, hint in ((2, "严格校验"), (1, "校验证书链（不校验主机名）"), (0, "跳过证书校验")):
            try:
                return _attempt(url, level)
            except urllib.error.HTTPError as error:
                if error.code in (403, 404):
                    raise RuntimeError(
                        f"源站返回 HTTP {error.code}：{url}\n"
                        "请检查 build-config.json 中 source_url 的 file 参数对应的文件是否存在。"
                    ) from error
                last_error = error
                break  # HTTP 状态码错误与证书无关，直接进入下一次重试
            except Exception as error:  # noqa: BLE001（SSL/网络类错误，降级后再试）
                last_error = error
                if level > 0:
                    print(f"HTTPS {hint}失败（{error}），降级重试……")
        if attempt < HTTP_RETRIES:
            print(f"抓取失败（第 {attempt}/{HTTP_RETRIES} 次），3 秒后重试……")
            time.sleep(3)
    raise RuntimeError(f"无法抓取源数据：{last_error}\n"
                       "提示：已自动尝试跳过证书校验仍失败，请检查网络能否访问源站（可用浏览器打开 source_url 验证）。")


# ─────────────────────────── 镜像加速 ───────────────────────────


def fetch_bytes(url: str, timeout: float = 10.0):
    """短超时 GET，返回 (body, 耗时秒)。gzip 响应自动解压；证书校验仍按「严格→跳过」逐级降级。"""
    last_error = None
    for level in (2, 0):
        context = None
        if level == 0:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                body = response.read()
                # 有的 CDN 会把 Content-Encoding 标成 br，实际给的却是 gzip；按魔数判断最稳
                if body[:2] == b"\x1f\x8b":
                    try:
                        body = gzip.decompress(body)
                    except OSError:
                        pass
                return body, time.monotonic() - started
        except Exception as error:  # noqa: BLE001
            last_error = error
    raise RuntimeError(f"无法获取 {url}：{last_error}")


def mirror_wrap(url: str, base: str, style: str):
    """把 GitHub 原始地址套到镜像上。style: raw=保持 raw.githubusercontent 路径；github=改成 github.com/raw 路径。"""
    if not base.endswith("/"):
        base += "/"
    if style == "raw":
        return f"{base}{url}"
    if style == "github":
        match = re.match(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$", url)
        if not match:
            return None
        owner, repo, branch, path = match.groups()
        return f"{base}https://github.com/{owner}/{repo}/raw/{branch}/{path}"
    return None


def fetch_mirror_candidates(list_url: str, timeout: float):
    """读取镜像清单与测速结果，只保留状态正常的镜像，按清单延迟升序返回。"""
    # 该接口会被 CDN 缓存成奇怪的编码（Content-Encoding: br 但内容其实是 gzip），
    # 加个时间戳参数绕开缓存，配合按魔数解压才能稳定拿到 JSON。
    bust = f"{list_url}{'&' if '?' in list_url else '?'}_={int(time.time())}"
    body, _ = fetch_bytes(bust, timeout)
    document = json.loads(body.decode("utf-8"))
    items = []
    for item in document.get("mirrors") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status", "")).lower() != "ok":
            continue
        base = item.get("base")
        if not isinstance(base, str) or not base.startswith("http"):
            continue
        host = urllib.parse.urlsplit(base).hostname or base
        try:
            latency = float(item.get("latency") or 10 ** 9)
        except (TypeError, ValueError):
            latency = 10 ** 9
        items.append({"host": host, "base": base, "latency": latency})
    items.sort(key=lambda entry: entry["latency"])
    return items, document.get("updatedAt")


def probe_mirror(base: str, sample_url: str, timeout: float):
    """探测某个镜像能否正确返回我们的档案。返回 (style, 耗时秒) 或 None。"""
    for style in ("raw", "github"):
        wrapped = mirror_wrap(sample_url, base, style)
        if not wrapped:
            continue
        try:
            body, elapsed = fetch_bytes(wrapped, timeout)
        except Exception:  # noqa: BLE001（镜像不可用就换下一个）
            continue
        if b'"ServerKind"' in body[:4000]:
            return style, elapsed
    return None


def choose_mirror(mirror_cfg: dict, sample_url: str, previous: dict):
    """自动选择镜像：按官方测速延迟排序 → 并发探测前 N 个 → 取延迟最低且可用的那个。

    previous 为上一轮选中的镜像（state.json 里的记录），命中时不随便换（延迟在
    hysteresis 倍数内保持不变），避免每天改 URL 导致所有客户端反复刷新。
    """
    timeout = float(mirror_cfg.get("timeout") or 10)
    pinned = mirror_cfg.get("host")
    previous = previous if isinstance(previous, dict) else {}
    prev_base = previous.get("base")
    prev_style = previous.get("style")

    candidates = []
    list_error = None
    try:
        candidates, updated_at = fetch_mirror_candidates(
            mirror_cfg.get("list_url") or MIRROR_LIST_URL, timeout
        )
    except Exception as error:  # noqa: BLE001（清单挂了就用上次的或放弃）
        updated_at = None
        list_error = error

    if pinned:
        pinned_host = str(pinned).strip().rstrip("/")
        base = pinned_host if pinned_host.startswith("http") else f"https://{pinned_host}/"
        candidates = [entry for entry in candidates if entry["host"] == urllib.parse.urlsplit(base).hostname]
        if not candidates:
            candidates = [{"host": urllib.parse.urlsplit(base).hostname or base, "base": base, "latency": 0.0}]

    if not candidates:
        # 清单拿不到时，仍可继续用上一轮选中的镜像
        if prev_base:
            probed = probe_mirror(prev_base, sample_url, timeout)
            if probed:
                style, elapsed = probed
                return {
                    "host": previous.get("host") or urllib.parse.urlsplit(prev_base).hostname,
                    "base": prev_base,
                    "style": style,
                    "latency": previous.get("latency"),
                    "elapsed": elapsed,
                    "reason": "镜像清单不可用，沿用上次选择",
                    "updated_at": None,
                }
        if list_error is not None:
            print(f"提示：镜像清单获取失败（{list_error}），本次不启用镜像")
        return None

    limit = max(int(mirror_cfg.get("probe_candidates") or 10), 1)
    pool = candidates[:limit]
    if prev_base and all(entry["base"] != prev_base for entry in pool):
        if prev_style is not None or prev_base:
            pool = pool[:-1] + [{"host": previous.get("host"), "base": prev_base,
                                 "latency": previous.get("latency") or 10 ** 9}]

    results = []
    with ThreadPoolExecutor(max_workers=min(5, len(pool))) as executor:
        futures = {
            executor.submit(probe_mirror, entry["base"], sample_url, timeout): entry
            for entry in pool
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                probed = future.result()
            except Exception:  # noqa: BLE001
                probed = None
            if probed:
                style, elapsed = probed
                results.append({**entry, "style": style, "elapsed": elapsed})

    if not results:
        if prev_base:
            probed = probe_mirror(prev_base, sample_url, timeout)
            if probed:
                style, elapsed = probed
                return {
                    "host": previous.get("host"),
                    "base": prev_base,
                    "style": style,
                    "latency": previous.get("latency"),
                    "elapsed": elapsed,
                    "reason": "探测全部失败，沿用上次选择",
                    "updated_at": updated_at,
                }
        print("提示：所有镜像探测均失败，本次回退到 GitHub 直连地址")
        return None

    results.sort(key=lambda entry: (entry["latency"], entry["elapsed"]))
    best = results[0]

    chosen = best
    for entry in results:
        if prev_base and entry["base"] == prev_base:
            factor = float(mirror_cfg.get("hysteresis") or 1.5)
            if entry["latency"] <= best["latency"] * factor:
                chosen = entry
                chosen["reason"] = f"保持上次选择（延迟未超过最优的 {factor:g} 倍）"
            break
    chosen.setdefault("reason", "按测速延迟选择最优镜像")

    other_probed = [
        f"{entry['host']} {entry['latency']:.0f}ms/{entry['elapsed'] * 1000:.0f}ms"
        for entry in results
        if entry is not chosen
    ]
    return {
        "host": chosen["host"],
        "base": chosen["base"],
        "style": chosen["style"],
        "latency": chosen["latency"],
        "elapsed": chosen["elapsed"],
        "reason": chosen.get("reason"),
        "updated_at": updated_at,
        "probed": other_probed,
    }


def resolve_source_url(base_url: str, source_file: str) -> str:
    """把 --source-file 参数替换进 source_url 的 file 查询参数。"""
    parsed = urllib.parse.urlsplit(base_url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "file"]
    query.append(("file", source_file))
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def fetch_source(source_url: str, source_file: str) -> dict:
    url = resolve_source_url(source_url, source_file) if source_file else source_url
    print(f"抓取源数据：{url}")
    raw = http_get(url)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"源数据不是合法的 UTF-8 JSON：{error}") from error

    # 结构校验
    for key in ("title", "days", "slots", "times", "schedule"):
        if key not in data:
            raise RuntimeError(f"源数据缺少字段 {key!r}，请确认数据源格式未变化。")
    schedule = data["schedule"]
    if not isinstance(schedule, dict) or not schedule:
        raise RuntimeError("源数据 schedule 为空。")
    slots, times, days = data["slots"], data["times"], data["days"]
    if len(times) != len(slots):
        raise RuntimeError(f"源数据 times({len(times)}) 与 slots({len(slots)}) 数量不一致。")
    for cid, grid in schedule.items():
        if len(grid) != len(days):
            raise RuntimeError(f"班级 {cid} 的天数 {len(grid)} 与 days({len(days)}) 不一致。")
        for day_index, row in enumerate(grid):
            if len(row) != len(slots):
                raise RuntimeError(f"班级 {cid} 第 {day_index + 1} 天的节次数与 slots 不一致。")
    print(f"源数据校验通过：{data.get('title', '')}，{len(schedule)} 个班，{len(days)} 天 × {len(slots)} 节。")
    return data


# ─────────────────────────── 时间解析 ───────────────────────────


def parse_hhmm(text: str):
    """'07:30-08:15' → ('07:30:00', '08:15:00')；非法格式抛错。"""
    parts = str(text).strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"无法解析时间段：{text!r}")
    start, end = parts[0].strip(), parts[1].strip()
    for token in (start, end):
        pieces = token.split(":")
        if len(pieces) < 2 or len(pieces) > 3:
            raise ValueError(f"无法解析时间：{token!r}")
        if not all(piece.isdigit() for piece in pieces):
            raise ValueError(f"无法解析时间：{token!r}")
    if len(start.split(":")) == 2:
        start += ":00"
    if len(end.split(":")) == 2:
        end += ":00"
    return start, end


def minutes_of(hhmmss: str) -> int:
    hour, minute, _ = hhmmss.split(":")
    return int(hour) * 60 + int(minute)


def crosses_noon(start: str, end: str) -> bool:
    return minutes_of(start) < 12 * 60 < minutes_of(end)


def format_hhmm(total_minutes: int) -> str:
    """分钟数 → 'HH:MM'。"""
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def parse_time_point(value, name: str) -> str:
    """'12:20' / '12:20:00' → '12:20:00'。"""
    text = str(value or "").strip()
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).strftime("%H:%M:%S")
        except ValueError:
            continue
    raise RuntimeError(f"{name} 应为 HH:MM 或 HH:MM:SS 时间，实际为 {text!r}")


# ─────────────────────── 两周轮换（单双周） ───────────────────────


def parse_week_cycle(cfg: dict):
    """解析 build-config.json 的 week_cycle 配置；未启用时返回 None。"""
    cycle = cfg.get("week_cycle")
    if not cycle:
        return None
    if not isinstance(cycle, dict) or not cycle.get("enabled"):
        return None

    monday_raw = str(cycle.get("return_week_monday", "")).strip()
    try:
        anchor = datetime.strptime(monday_raw, "%Y-%m-%d").date()
    except ValueError as error:
        raise RuntimeError(f"week_cycle.return_week_monday 应为 YYYY-MM-DD 日期：{error}") from error
    if anchor.weekday() != 0:
        raise RuntimeError(
            f"week_cycle.return_week_monday 应为「返校周」的周一，{monday_raw} 实际是{WEEKDAY_CN[anchor.weekday()]}"
        )

    first_class = parse_time_point(cycle.get("home_week_friday_first_class"), "week_cycle.home_week_friday_first_class")
    school_over = parse_time_point(cycle.get("home_week_friday_school_over"), "week_cycle.home_week_friday_school_over")
    if minutes_of(school_over) <= minutes_of(first_class):
        raise RuntimeError("week_cycle：home_week_friday_school_over 应晚于 home_week_friday_first_class")

    # 客户端轮换基准（ClassIsland Settings.SingleWeekStartTime，界面称「学期首日」）：
    # 学期首日当天为第 1 周。取返校周周一的前一天（周日），可保证周日入学/返校当晚就属于新一轮的返校周。
    semester_start = anchor - timedelta(days=1)
    return {
        "return_week_monday": anchor,
        "first_class": first_class,
        "school_over": school_over,
        "semester_start": semester_start,
    }


def build_home_friday_times(times, included_indexes, cycle) -> list:
    """构造「回家周周五」使用的时间数组。

    午休级长间隔之后的节次整体提前：下午第一节从 first_class 开始，末节在 school_over 结束，
    节次时长与课间时长沿用常规下午作息。无法精确填满时会直接报错，避免静默生成错课表。
    """
    first_class = cycle["first_class"]
    school_over = cycle["school_over"]

    # 与 build_time_layout 相同的分割规则，定位长间隔（≥ LONG_GAP_MINUTES）之后的第一个节次
    split_pos = None
    previous_end = None
    for pos, index in enumerate(included_indexes):
        start, end = parse_hhmm(times[index])
        if previous_end is not None and minutes_of(start) - minutes_of(previous_end) >= LONG_GAP_MINUTES:
            split_pos = pos
        previous_end = end
    if split_pos is None:
        raise RuntimeError(
            "week_cycle 需要作息中存在午休级别的长间隔（≥60 分钟）来区分上午/下午，"
            "当前源数据 times 未检测到长间隔；请检查数据源或把 build-config.json 的 week_cycle.enabled 改为 false。"
        )

    afternoon_indexes = included_indexes[split_pos:]
    count = len(afternoon_indexes)
    first_start, first_end = parse_hhmm(times[afternoon_indexes[0]])
    lesson_minutes = minutes_of(first_end) - minutes_of(first_start)
    if count >= 2:
        next_start, _ = parse_hhmm(times[afternoon_indexes[1]])
        break_minutes = minutes_of(next_start) - minutes_of(first_end)
    else:
        break_minutes = 0

    total_minutes = minutes_of(school_over) - minutes_of(first_class)
    need = count * lesson_minutes + (count - 1) * break_minutes
    if need != total_minutes:
        raise RuntimeError(
            f"回家周周五时间排不下：{count} 节 × {lesson_minutes} 分钟 + {count - 1} 个 {break_minutes} 分钟课间 = {need} 分钟，"
            f"但 {first_class[:5]}–{school_over[:5]} 共 {total_minutes} 分钟。"
            "请核对源站下午节次时长，或调整 build-config.json 中 week_cycle 的 first_class / school_over。"
        )

    new_times = list(times)
    cursor = minutes_of(first_class)
    for order, index in enumerate(afternoon_indexes):
        start = cursor
        end = start + lesson_minutes
        new_times[index] = f"{format_hhmm(start)}-{format_hhmm(end)}"
        cursor = end + (break_minutes if order < count - 1 else 0)

    _, last_end = parse_hhmm(new_times[afternoon_indexes[-1]])
    if last_end != school_over:
        raise RuntimeError(f"内部错误：回家周周五末节结束时间 {last_end} != 配置放学时间 {school_over}")
    return new_times


# ─────────────────────────── 档案构建 ───────────────────────────


def build_time_layout(slots, times, included_indexes) -> dict:
    """生成统一时间表：上课点 + 课间 + 长间隔分割线（午休）。"""
    layouts = []
    previous_end = None
    for index in included_indexes:
        start, end = parse_hhmm(times[index])
        if previous_end is not None and minutes_of(start) > minutes_of(previous_end):
            gap = minutes_of(start) - minutes_of(previous_end)
            if gap >= LONG_GAP_MINUTES:
                name = "午休" if crosses_noon(previous_end, start) else "休息"
                layouts.append(
                    {
                        "StartTime": previous_end,
                        "EndTime": previous_end,  # 分割线为瞬时点，与 ClassIsland 自身行为一致
                        "TimeType": 2,
                        "IsHideDefault": False,
                        "DefaultClassId": ZERO_GUID,
                        "BreakName": name,
                    }
                )
            else:
                layouts.append(
                    {
                        "StartTime": previous_end,
                        "EndTime": start,
                        "TimeType": 1,
                        "IsHideDefault": False,
                        "DefaultClassId": ZERO_GUID,
                        "BreakName": "课间休息",
                    }
                )
        layouts.append(
            {
                "StartTime": start,
                "EndTime": end,
                "TimeType": 0,
                "IsHideDefault": False,
                "DefaultClassId": ZERO_GUID,
                "BreakName": "",
            }
        )
        previous_end = end
    return layouts


def build_subjects_and_plans(data, included_indexes, weekday_names, exclude_report,
                             week_cycle=None, friday_home_layout_id=None) -> tuple:
    """遍历全部班级，生成科目总表与每班每日课表。返回 (subjects, classplans_by_class)。"""
    schedule = data["schedule"]
    slot_teachers = data.get("slot_teachers", {})
    subjects = {}
    classplans_by_class = {}
    friday_weekday = weekday_names.index("周五") + 1 if "周五" in weekday_names else -1

    for cid in sorted(schedule.keys(), key=int):
        grid = schedule[cid]
        teachers_grid = slot_teachers.get(cid, [[None] * len(row) for row in grid])
        class_number = int(cid)
        plans = {}

        for day_index, row in enumerate(grid):
            day_name = data["days"][day_index]
            if day_name not in weekday_names:
                raise RuntimeError(f"无法识别的星期名：{day_name!r}")
            weekday = weekday_names.index(day_name) + 1  # ClassIsland WeekDay：周一=1 … 周日=7
            classes = []
            for slot_index in included_indexes:
                name = str(row[slot_index]).strip()
                teacher = str(teachers_grid[day_index][slot_index] or "").strip() if day_index < len(teachers_grid) else ""
                if not name:
                    exclude_report.append(f"班级 {cid} 第 {day_index + 1} 天第 {slot_index + 1} 节为空，已按空课处理。")
                    classes.append({"SubjectId": ZERO_GUID, "IsEnabled": False})
                    continue
                subject_id = guid_of("subject", name, teacher)
                subjects.setdefault(
                    subject_id,
                    {
                        "Name": name,
                        "Initial": name[0],
                        "TeacherName": teacher,
                        "IsOutDoor": name == "体育",
                    },
                )
                classes.append({"SubjectId": subject_id, "IsEnabled": True})

            if weekday == friday_weekday:
                # 周五恒定输出两张课表，GUID 不随配置变化 —— 无论 week_cycle 开关如何，
                # 生成结果都是"覆盖"同一批条目，客户端上不会出现新旧两张周五课表并存、
                # 同一天多重命中的情况（集控合并不删除旧条目，只能靠同名键覆盖）。
                #   week_cycle 开启：返校周（第 1 周）+ 回家周（第 2 周）两张都启用；
                #   week_cycle 关闭：第一张改为每周生效，第二张写为 IsEnabled=false 的停用桩。
                if week_cycle and friday_home_layout_id:
                    variants = (
                        ("return", 1, TIME_LAYOUT_ID, f"{day_name}（返校周）", True),
                        ("home", 2, friday_home_layout_id, f"{day_name}（回家周 · 提前放学）", True),
                    )
                else:
                    variants = (
                        ("return", 0, TIME_LAYOUT_ID, day_name, True),
                        ("home", 0, TIME_LAYOUT_ID, f"{day_name}（回家周 · 已停用）", False),
                    )
                for suffix, week_div, layout_id, label, enabled in variants:
                    plan_id = guid_of("classplan", cid, str(weekday), suffix)
                    plans[plan_id] = {
                        "Name": f"高一{class_number}班 · {label}",
                        "TimeLayoutId": layout_id,
                        "TimeRule": {"WeekDay": weekday, "WeekCountDiv": week_div, "WeekCountDivTotal": 2},
                        "Classes": [dict(item) for item in classes],
                        "IsEnabled": enabled,
                        "AssociatedGroup": GLOBAL_GROUP_GUID,
                    }
            else:
                plan_id = guid_of("classplan", cid, str(weekday))
                plans[plan_id] = {
                    "Name": f"高一{class_number}班 · {day_name}",
                    "TimeLayoutId": TIME_LAYOUT_ID,
                    "TimeRule": {"WeekDay": weekday, "WeekCountDiv": 0, "WeekCountDivTotal": 2},
                    "Classes": classes,
                    "IsEnabled": True,
                    "AssociatedGroup": GLOBAL_GROUP_GUID,
                }
        classplans_by_class[cid] = plans

    return subjects, classplans_by_class


def load_previous_plans(cid: str) -> dict:
    """读取仓库中上一次生成的班级档案，用于识别退场（不再生成）的课表。"""
    path = os.path.join(REPO_ROOT, "classplans", f"{cid}.json")
    doc = load_json_file(path)
    if not isinstance(doc, dict):
        return {}
    plans = doc.get("ClassPlans")
    return plans if isinstance(plans, dict) else {}


def neutralize_plan(plan: dict) -> dict:
    """把退场课表改写为停用桩：不可命中、时间表引用保持有效、名称带退场标记。

    幂等：已是停用桩的条目不重复加前缀，可反复构建。
    """
    stub = json.loads(json.dumps(plan, ensure_ascii=False))
    stub["IsEnabled"] = False
    stub["TimeLayoutId"] = TIME_LAYOUT_ID  # 避免引用可能已被删除的时间表
    name = str(stub.get("Name") or "")
    if not name.startswith(RETIRED_PREFIX):
        stub["Name"] = RETIRED_PREFIX + name
    return stub


def merge_retired_plans(plans: dict, previous: dict) -> dict:
    """把上一次有、这一次没有的课表以停用桩的形式合并回来，确保客户端上不会残留可命中的旧课表。"""
    merged = {plan_id: json.loads(json.dumps(plan, ensure_ascii=False)) for plan_id, plan in plans.items()}
    for plan_id, plan in previous.items():
        if plan_id in merged:
            continue
        merged[plan_id] = neutralize_plan(plan)
    return merged


# ─────────────────────────── 版本与状态管理 ───────────────────────────


def bump(entry: dict, new_hash: str, new_url: str, force: bool) -> tuple:
    """根据内容哈希与 URL 判断是否递增版本号。返回 (版本号, 是否更新)。"""
    old_version = int(entry.get("version", 0) or 0)
    changed = entry.get("hash") != new_hash or (new_url is not None and entry.get("url") != new_url)
    if changed or force:
        return old_version + 1, True
    return max(old_version, 1), False


def canonical_text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ─────────────────────────── 主流程 ───────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 ClassIsland 高一课程表静态集控档案")
    parser.add_argument("--source-file", default="result_actual.json", help="源站 API 的 file 参数（默认 result_actual.json）")
    parser.add_argument("--repo", default=None, help="GitHub 仓库（owner/repo），未提供时读取 GITHUB_REPOSITORY")
    parser.add_argument("--branch", default=None, help="分支名，未提供时读取 GITHUB_REF_NAME，再退回 main")
    parser.add_argument("--base-url", default=None, help="直接指定档案托管根地址（覆盖 --repo/--branch）")
    parser.add_argument("--force-bump", action="store_true", help="内容未变化时也递增版本号")
    parser.add_argument("--dry-run", action="store_true", help="只计算并打印摘要，不写任何文件")
    parser.add_argument("--no-mirror", action="store_true", help="不启用镜像加速，清单直接指向 raw.githubusercontent.com")
    parser.add_argument("--mirror-host", default=None, help="固定使用某个镜像主机名（如 ghfast.top），跳过自动选择")
    args = parser.parse_args()

    cfg = load_config()
    weekday_names = cfg["weekday_names"]

    # ── 托管地址 ──
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY") or "OWNER/REPO"
    branch = args.branch or os.environ.get("GITHUB_REF_NAME") or "main"
    base_url = args.base_url or f"https://raw.githubusercontent.com/{repo}/{branch}"
    print(f"托管根地址：{base_url}")

    # ── 镜像加速（默认开启）──
    # 国内多数教室网络直连 raw.githubusercontent.com 不通，清单里改成「镜像+原始地址」的拼接形式；
    # 镜像清单取自 git.hiwiki.org 的测速数据，每 12 小时更新，这里挑延迟最低且实测可用的一家。
    mirror_cfg = dict(cfg.get("mirror") or {})
    if args.no_mirror:
        mirror_cfg["enabled"] = False
    if args.mirror_host:
        mirror_cfg["enabled"] = True
        mirror_cfg["host"] = args.mirror_host
    state_before = load_json_file(STATE_PATH) or {}
    mirror_info = None
    if base_url.startswith(RAW_HOST_PREFIX) and mirror_cfg.get("enabled", True):
        mirror_info = choose_mirror(mirror_cfg, f"{base_url}/manifest.json", state_before.get("mirror"))
    elif not base_url.startswith(RAW_HOST_PREFIX):
        print("提示：托管根地址不是 GitHub raw，跳过镜像加速")

    def publish(url: str) -> str:
        """把 GitHub 原始地址按当前镜像拼接成客户端可访问的地址。"""
        if mirror_info and url.startswith(RAW_HOST_PREFIX):
            wrapped = mirror_wrap(url, mirror_info["base"], mirror_info["style"])
            if wrapped:
                return wrapped
        return url

    if mirror_info:
        print(
            f"镜像加速：{mirror_info['host']}（清单延迟 {float(mirror_info.get('latency') or 0):.0f} ms，"
            f"实测 {mirror_info['elapsed'] * 1000:.0f} ms，路径形式 {mirror_info['style']}）"
        )
        if mirror_info.get("reason"):
            print(f"  选择依据：{mirror_info['reason']}")
        for extra in mirror_info.get("probed") or []:
            print(f"  其他候选：{extra}")

    # ── 抓取与解析 ──
    data = fetch_source(cfg["source_url"], args.source_file)
    slots, times = data["slots"], data["times"]

    patterns = [p for p in cfg["exclude_slot_patterns"] if p]
    included_indexes = [
        i for i, name in enumerate(slots)
        if not any(re.search(pattern, str(name)) for pattern in patterns)
    ]
    excluded = [slots[i] for i in range(len(slots)) if i not in included_indexes]
    if not included_indexes:
        raise RuntimeError("排除规则把全部节次都过滤掉了，请检查 build-config.json 的 exclude_slot_patterns。")
    print(f"包含节次：{[slots[i] for i in included_indexes]}")
    if excluded:
        print(f"排除节次：{excluded}")

    # ── 两周轮换 ──
    cycle = parse_week_cycle(cfg)
    times_home_friday = None
    layouts_home_friday = None
    if cycle:
        times_home_friday = build_home_friday_times(times, included_indexes, cycle)
        layouts_home_friday = build_time_layout(slots, times_home_friday, included_indexes)
        print(
            "两周轮换：返校周锚点 "
            f"{cycle['return_week_monday'].strftime('%Y-%m-%d')}（周一），回家周周五 "
            f"{cycle['first_class'][:5]} 上课、{cycle['school_over'][:5]} 放学。"
        )
        print(
            "客户端「设置 → 常规 → 学期开始时间」需为 "
            f"{cycle['semester_start'].strftime('%Y-%m-%d')}（周日），单双周才会正确轮换"
            + ("（全新机器将由 DefaultSettingsSource 自动写入）。" if bool(cfg.get("emit_default_settings", True)) else "。")
        )

    # ── 生成档案 ──
    layouts = build_time_layout(slots, times, included_indexes)
    lesson_count = sum(1 for item in layouts if item["TimeType"] == 0)
    exclude_report = []
    subjects, classplans_by_class = build_subjects_and_plans(
        data,
        included_indexes,
        weekday_names,
        exclude_report,
        week_cycle=cycle,
        friday_home_layout_id=TIME_LAYOUT_HOME_FRIDAY_ID if cycle else None,
    )
    for note in exclude_report:
        print(f"提示：{note}")

    subject_count = len(subjects)
    class_ids = sorted(classplans_by_class.keys(), key=int)

    # 退场课表中和：上一次有、这一次没有的条目改写为停用桩，避免客户端上残留可命中的旧课表
    retired_count = 0
    for cid in class_ids:
        previous = load_previous_plans(cid)
        merged = merge_retired_plans(classplans_by_class[cid], previous)
        retired_count += sum(1 for pid in merged if pid not in classplans_by_class[cid])
        classplans_by_class[cid] = merged

    # 科表按班级拆分：集控的 SubjectsSource 支持 {id} 模板，每台设备只下载本班用到的
    # 「科目+教师」条目，避免每块班级屏上都塞进全年级 78 个教师条目（也缩小了姓名暴露面）。
    subject_files = {}
    per_class_subject_counts = {}
    for cid in class_ids:
        used = set()
        for plan in classplans_by_class[cid].values():
            for item in plan.get("Classes") or []:
                subject_id = item.get("SubjectId")
                if item.get("IsEnabled") and subject_id and subject_id != ZERO_GUID:
                    used.add(subject_id)
        missing = [sid for sid in used if sid not in subjects]
        if missing:
            raise RuntimeError(f"班级 {cid} 的课表引用了未生成的科目：{missing}")
        subject_files[cid] = {
            "Name": f"高一{int(cid)}班 科目（自动生成，仅含本班任课教师）",
            "Subjects": {sid: subjects[sid] for sid in sorted(used)},
        }
        per_class_subject_counts[cid] = len(used)

    plans_per_class = len(next(iter(classplans_by_class.values()))) if classplans_by_class else 0
    subject_count_min = min(per_class_subject_counts.values()) if per_class_subject_counts else 0
    subject_count_max = max(per_class_subject_counts.values()) if per_class_subject_counts else 0
    print(
        f"生成完成：{len(class_ids)} 个班 × 每班 {plans_per_class} 张日课表，{lesson_count} 节/天；"
        f"科目按班拆分（全年级 {subject_count} 条，每班 {subject_count_min}–{subject_count_max} 条）。"
    )
    if retired_count:
        print(f"其中 {retired_count} 张为已退场课表的停用桩（IsEnabled=false，不影响当日命中）。")

    timelayouts_map = {TIME_LAYOUT_ID: {"Name": "高一统一时间表", "Layouts": layouts}}
    if cycle:
        timelayouts_map[TIME_LAYOUT_HOME_FRIDAY_ID] = {
            "Name": f"高一统一时间表 · 回家周周五（{cycle['first_class'][:5]} 上课，{cycle['school_over'][:5]} 放学）",
            "Layouts": layouts_home_friday,
        }
    timelayouts_profile = {
        "Name": "高一统一时间表（自动生成）",
        "TimeLayouts": timelayouts_map,
    }
    classplan_files = {}
    for cid in class_ids:
        classplan_files[cid] = {
            "Name": f"高一{int(cid)}班 课表（自动生成）",
            "ClassPlans": classplans_by_class[cid],
        }

    classplans_bundle_hash = sha256_text(canonical_text(classplan_files))
    timelayouts_hash = sha256_text(canonical_text(timelayouts_profile))
    subjects_hash = sha256_text(canonical_text(subject_files))
    # 课表依赖时间表（Classes 数量必须与时间表上课点一致），时间表变化时同步刷新课表版本
    classplans_hash = sha256_text(classplans_bundle_hash + timelayouts_hash)

    urls = {
        "classplans": publish(f"{base_url}/classplans/{{id}}.json"),
        "timelayouts": publish(f"{base_url}/timelayouts.json"),
        "subjects": publish(f"{base_url}/subjects/{{id}}.json"),
        "policy": publish(f"{base_url}/policy.json"),
        "settings": publish(f"{base_url}/settings/default-settings.json"),
    }
    manifest_url = publish(f"{base_url}/manifest.json")

    # ── 默认设置源（下发「学期首日」）──
    # 只有相对全新（未完成欢迎向导）的实例才会应用该设置源，且是整体替换 Settings.json；
    # 因此这里只放重启关键项的取值，其余字段由客户端按内置默认值填充。
    emit_settings = bool(cfg.get("emit_default_settings", True)) and cycle is not None
    settings_profile = None
    settings_hash = None
    if emit_settings:
        settings_profile = {"SingleWeekStartTime": cycle["semester_start"].strftime("%Y-%m-%dT00:00:00")}
        settings_hash = sha256_text(canonical_text(settings_profile))

    # ── 版本管理 ──
    existing_manifest = load_json_file(os.path.join(REPO_ROOT, "manifest.json"))
    manifest_policy_version = 1
    if existing_manifest and isinstance(existing_manifest.get("PolicySource"), dict):
        raw_version = existing_manifest["PolicySource"].get("Version", existing_manifest["PolicySource"].get("version", 1))
        try:
            manifest_policy_version = max(int(raw_version or 1), 1)
        except (TypeError, ValueError):
            manifest_policy_version = 1

    state = load_json_file(STATE_PATH) or {}
    versions = {}
    changes = {}
    version_jobs = [
        ("timelayouts", timelayouts_hash),
        ("subjects", subjects_hash),
        ("classplans", classplans_hash),
    ]
    if emit_settings:
        version_jobs.append(("settings", settings_hash))
    for key, new_hash in version_jobs:
        entry = state.get(key, {})
        version, changed = bump(entry, new_hash, urls[key], args.force_bump)
        versions[key] = version
        changes[key] = changed

    # policy.json 由管理员维护、内容不由脚本生成，但版本号改为按内容哈希自动维护，
    # 避免"改了策略却忘了给 PolicySource.Version +1，客户端永远拿不到"。
    policy_hash = None
    policy_changed = False
    policy_version = manifest_policy_version
    policy_doc = load_json_file(os.path.join(REPO_ROOT, "policy.json"))
    if isinstance(policy_doc, dict):
        policy_hash = sha256_text(canonical_text(policy_doc))
        state_policy = state.get("policy") if isinstance(state.get("policy"), dict) else {}
        try:
            stored_version = max(int(state_policy.get("version") or 1), 1)
        except (TypeError, ValueError):
            stored_version = 1
        stored_hash = state_policy.get("hash")
        if stored_hash is None:
            policy_version = max(manifest_policy_version, 1)
        elif stored_hash != policy_hash:
            policy_version = max(manifest_policy_version, stored_version + 1)
            policy_changed = True
        else:
            policy_version = max(manifest_policy_version, stored_version)
        if args.force_bump and not policy_changed:
            policy_version = max(manifest_policy_version, stored_version + 1)
            policy_changed = True

    # 课表逐班变更明细（用于摘要展示）
    old_file_hashes = state.get("classplans", {}).get("files", {})
    new_file_hashes = {cid: sha256_text(canonical_text(classplan_files[cid])) for cid in class_ids}
    changed_classes = [cid for cid in class_ids if new_file_hashes[cid] != old_file_hashes.get(cid)]
    if changes["classplans"] and not changed_classes:
        changed_classes = list(class_ids)  # 时间表联动刷新

    # ── 写盘 ──
    if args.dry_run:
        print("（dry-run 模式：不写任何文件）")
    else:
        # 清掉源数据里已不存在的班级遗留文件（否则会一直留在仓库里）
        classplans_dir = os.path.join(REPO_ROOT, "classplans")
        presets_dir = os.path.join(REPO_ROOT, "presets")
        for directory, pattern, keep in (
            (classplans_dir, re.compile(r"^(\d+)\.json$"), set(class_ids)),
            (presets_dir, re.compile(r"^ManagementPreset-(\d+)\.json$"), set(class_ids)),
        ):
            if not os.path.isdir(directory):
                continue
            for filename in sorted(os.listdir(directory)):
                match = pattern.match(filename)
                if match and match.group(1) not in keep:
                    os.remove(os.path.join(directory, filename))
                    print(f"已清理不再存在的班级文件：{os.path.relpath(os.path.join(directory, filename), REPO_ROOT)}")

        for cid, profile in classplan_files.items():
            dump_json(profile, os.path.join(REPO_ROOT, "classplans", f"{cid}.json"))
        dump_json(timelayouts_profile, os.path.join(REPO_ROOT, "timelayouts.json"))
        subjects_dir = os.path.join(REPO_ROOT, "subjects")
        os.makedirs(subjects_dir, exist_ok=True)
        for cid, profile in subject_files.items():
            dump_json(profile, os.path.join(subjects_dir, f"{cid}.json"))
        for filename in sorted(os.listdir(subjects_dir)):
            match = re.match(r"^(\d+)\.json$", filename)
            if match and match.group(1) not in set(class_ids):
                os.remove(os.path.join(subjects_dir, filename))
                print(f"已清理不再存在的科目文件：subjects/{filename}")
        # 旧的全局科目总表（会把全年级教师姓名塞给每台设备）已弃用，构建时顺手删除
        legacy_subjects = os.path.join(REPO_ROOT, "subjects.json")
        if os.path.exists(legacy_subjects):
            os.remove(legacy_subjects)
            print("已移除旧的全局科目总表 subjects.json（改为按班级拆分 subjects/{id}.json）")

        # 客户端预设文件（放到数据目录并重命名为 ManagementPreset.json，加入管理时会自动加载）
        for cid in class_ids:
            preset = {
                "ManagementServerKind": 0,
                "ManagementServer": "",
                "ManagementServerGrpc": "",
                "ManifestUrlTemplate": manifest_url,
                "ClassIdentity": cid,
            }
            dump_json(preset, os.path.join(REPO_ROOT, "presets", f"ManagementPreset-{cid}.json"))

        # 默认设置源（仅用于下发学期首日；不含则删除旧文件，避免清单与文件不一致）
        settings_path = os.path.join(REPO_ROOT, "settings", "default-settings.json")
        if emit_settings:
            dump_json(settings_profile, settings_path)
        elif os.path.exists(settings_path):
            os.remove(settings_path)
            print("已移除 settings/default-settings.json（week_cycle 未启用或已关闭下发）")

        # manifest.json：版本号由本脚本维护
        manifest = {
            "ServerKind": 0,
            "OrganizationName": cfg["organization_name"],
            "CoreVersion": cfg["core_version"],
            "ClassPlanSource": {"Value": urls["classplans"], "Version": versions["classplans"]},
            "TimeLayoutSource": {"Value": urls["timelayouts"], "Version": versions["timelayouts"]},
            "SubjectsSource": {"Value": urls["subjects"], "Version": versions["subjects"]},
            "PolicySource": {"Value": urls["policy"], "Version": policy_version},
        }
        if emit_settings:
            manifest["DefaultSettingsSource"] = {"Value": urls["settings"], "Version": versions["settings"]}
        dump_json(manifest, os.path.join(REPO_ROOT, "manifest.json"))

        # 状态文件（内容派生，无时间戳，保证 git 历史干净）
        new_state = {
            "base_url": base_url,
            "timelayouts": {"hash": timelayouts_hash, "version": versions["timelayouts"], "url": urls["timelayouts"]},
            "subjects": {
                "hash": subjects_hash,
                "version": versions["subjects"],
                "url": urls["subjects"],
                "files": {cid: sha256_text(canonical_text(subject_files[cid])) for cid in class_ids},
            },
            "classplans": {
                "hash": classplans_hash,
                "version": versions["classplans"],
                "url": urls["classplans"],
                "files": new_file_hashes,
            },
            "policy": {"hash": policy_hash, "version": policy_version},
        }
        if emit_settings:
            new_state["settings"] = {"hash": settings_hash, "version": versions["settings"], "url": urls["settings"]}
        if mirror_info:
            # 只记录「选了谁」，不含测速时间戳，避免每次构建都产生无意义的 diff
            new_state["mirror"] = {
                "host": mirror_info["host"],
                "base": mirror_info["base"],
                "style": mirror_info["style"],
            }
        dump_json(new_state, STATE_PATH)

        # 源数据快照（供人工 diff 审查）
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        with open(os.path.join(SNAPSHOT_DIR, "result_actual.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

        # 班级对照表（可选，默认关闭）：
        # 源数据 teachers 元数据（班主任/选科组合）与当前课表不一致，源站页面也不展示，
        # 直接生成会误导部署；确需生成时把 build-config.json 的 generate_roster 设为 true。
        if cfg.get("generate_roster"):
            meta = data.get("teachers", {})
            roster_lines = [
                "# 高一班级对照表",
                "",
                "> 由 scripts/build.py 自动生成（源数据中的班级元数据快照），请勿手动编辑。",
                "> 注意：班主任/选科组合来自源数据 `teachers` 元数据，源站页面并未展示，且可能与当前课表不一致，仅作参考。",
                "",
                "| 集控 ID | 班级 | 班主任 | 选科组合 |",
                "| --- | --- | --- | --- |",
            ]
            for cid in class_ids:
                info = meta.get(cid, {})
                roster_lines.append(
                    f"| {cid} | 高一{int(cid)}班 | {info.get('homeroom', '—')} | {info.get('combo', '—')} |"
                )
            os.makedirs(os.path.dirname(ROSTER_MD), exist_ok=True)
            with open(ROSTER_MD, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(roster_lines) + "\n")
        elif os.path.exists(ROSTER_MD):
            print("提示：generate_roster 已关闭，保留现有的 docs/班级对照表.md（不再更新）。")

        now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        dump_json(
            {
                "fetched_at": now,
                "source_title": data.get("title", ""),
                "source_file": args.source_file,
                "class_count": len(class_ids),
                "versions": versions,
                "policy_version": policy_version,
                "changed": changes,
                "changed_classes": changed_classes,
            },
            LAST_UPDATE_JSON,
        )

        def ver_line(key, label):
            mark = "已更新" if changes[key] else "未变化"
            return f"| {label} | v{versions[key]} | {mark} |"

        changed_class_text = "、".join(f"高一{int(c)}班" for c in changed_classes) if changed_classes else "无"
        md = [
            "## 高一课程表集控更新摘要",
            "",
            f"- 源数据：{data.get('title', args.source_file)}（{args.source_file}）",
            f"- 抓取时间：{now}（北京时间）",
            f"- 覆盖班级：{len(class_ids)} 个 ｜ 每日 {lesson_count} 节 ｜ 科目每班 {subject_count_min}–{subject_count_max} 条（全年级 {subject_count} 条，按班分发）",
        ]
        if cycle:
            md += [
                f"- 两周轮换：返校周锚点 {cycle['return_week_monday'].strftime('%Y-%m-%d')}（周一，周五正常）"
                f"；回家周周五 {cycle['first_class'][:5]} 上课、{cycle['school_over'][:5]} 放学",
                f"- **学期首日**（界面：「设置 → 常规 → 学期开始时间」）= {cycle['semester_start'].strftime('%Y-%m-%d')}（周日）",
                "- 全新安装的机器会在欢迎向导前由 DefaultSettingsSource 自动写入该日期；已初始化过的机器仍需按上面的日期手工核对",
            ]
        md += [
            "",
            "| 档案 | 版本 | 状态 |",
            "| --- | --- | --- |",
            ver_line("timelayouts", "时间表 timelayouts.json"),
            ver_line("subjects", "科目 subjects/{id}.json（按班级拆分）"),
            ver_line("classplans", "课表 classplans/{id}.json"),
        ]
        if emit_settings:
            md.append(ver_line("settings", "默认设置 settings/default-settings.json"))
        md += [
            f"| 策略 policy.json | v{policy_version} | {'已更新' if policy_changed else '未变化'}（按内容哈希自动维护） |",
            "",
            f"变更班级：{changed_class_text}",
            "",
            f"托管地址：{manifest_url}",
        ]
        with open(LAST_UPDATE_MD, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(md) + "\n")

    # ── 摘要输出（stdout 也会被 Action 写入 $GITHUB_STEP_SUMMARY 的场景使用）──
    print()
    print("版本号：")
    for key, label in (("timelayouts", "时间表"), ("subjects", "科目"), ("classplans", "课表")):
        print(f"  {label}: v{versions[key]}{'（更新）' if changes[key] else '（未变化）'}")
    if emit_settings:
        print(f"  默认设置: v{versions['settings']}（{'更新' if changes['settings'] else '未变化'}，仅对全新机器生效）")
    print(f"  策略: v{policy_version}（{'更新' if policy_changed else '未变化'}，按内容哈希自动维护）")
    if mirror_info:
        print(f"  镜像：{mirror_info['host']}（{mirror_info['style']} 形式）")
    else:
        print("  镜像：未启用（客户端直连 raw.githubusercontent.com）")
    if changed_classes:
        print(f"变更班级：{'、'.join(f'高一{int(c)}班' for c in changed_classes)}")
    else:
        print("变更班级：无")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"构建失败：{error}", file=sys.stderr)
        sys.exit(1)
