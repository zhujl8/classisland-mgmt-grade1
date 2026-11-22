#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate.py —— 校验仓库中生成的集控档案是否满足 ClassIsland 静态集控规范。

校验范围：
  1. manifest.json 结构（ServerKind / ReVersionString / CoreVersion / {id} 模板 / https 地址
     / 镜像前缀一致性与 data/state.json 记录的镜像是否吻合）
  2. timelayouts.json（时间点类型与时间格式、上课点数量；week_cycle 启用时校验回家周周五时间表）
  3. subjects/{id}.json（按班级拆分：GUID 键、科目字段、不串班、只含本班任课教师）
  4. classplans/{id}.json（时间表/科目引用完整性、WeekDay 取值、多周轮换规则组合、每张课表节数）
  5. presets/（ManifestUrlTemplate 与 manifest 一致、ClassIdentity 与文件名一致）
  6. policy.json（仅允许已知布尔字段）
  7. data/state.json 与 policy.json / manifest 的版本一致性
  8. manifest 的 DefaultSettingsSource 与 settings/default-settings.json（学期开始时间）
  9. classplans/、subjects/ 与 presets/ 的班级集合是否一一对应（识别未清理的陈旧文件）

零第三方依赖，Python 3.9+。全部通过退出码 0，任何问题退出码 1。
"""

import glob
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
TIME_RE = re.compile(r"^\d{2,3}:\d{2}:\d{2}$")
URL_ID_TEMPLATE_RE = re.compile(r"\{id\}")
# 源地址根：可选的镜像前缀 + GitHub raw 的 owner/repo/branch
URL_ROOT_RE = re.compile(
    r"^(?P<prefix>https://[^/\s]+/)?(?P<raw>https://raw\.githubusercontent\.com/[^/\s]+/[^/\s]+/[^/\s]+)/"
)

# ClassIsland 内置课表群 GUID（ClassPlanGroup.cs）
BREAK_NAME = "课间休息"
TIMELAYOUT_MAIN_ID = "3113c9e9-9dcb-530b-83f5-f751022b4d20"

SOURCE_KEYS = ("ClassPlanSource", "TimeLayoutSource", "SubjectsSource", "DefaultSettingsSource", "PolicySource")

GLOBAL_GROUP_GUID = "00000000-0000-0000-0000-000000000000"   # 全局课表群（Guid.Empty）
DEFAULT_GROUP_GUID = "acaf4ef0-e261-4262-b941-34ea93cb4369"  # 默认课表群

errors: list[str] = []
warnings: list[str] = []


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def error(msg: str) -> None:
    errors.append(msg)


def warning(msg: str) -> None:
    warnings.append(msg)


def load(path: str):
    full = os.path.join(REPO_ROOT, path)
    if not os.path.exists(full):
        error(f"文件缺失：{path}")
        return None
    try:
        with open(full, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        error(f"文件无法解析为 JSON：{path}（{exc}）")
        return None


def check_reversion_string(container: str, entry) -> None:
    if not isinstance(entry, dict):
        error(f"{container}: ReVersionString 应为对象，实际为 {type(entry).__name__}")
        return
    version = entry.get("Version")
    value = entry.get("Value")
    if not isinstance(version, int) or version < 1:
        error(f"{container}: Version 必须为 >=1 的整数，实际为 {version!r}")
    if not isinstance(value, str) or not value:
        error(f"{container}: Value 必须为非空字符串，实际为 {value!r}")
    elif not value.startswith("https://"):
        error(f"{container}: Value 应为 https 地址，实际为 {value!r}")


def main() -> int:
    # ── manifest.json ──
    manifest = load("manifest.json")
    state_doc = load("data/state.json")
    if manifest is not None:
        if manifest.get("ServerKind") != 0:
            error(f"manifest.json: ServerKind 应为 0（静态托管），实际为 {manifest.get('ServerKind')!r}")
        org = manifest.get("OrganizationName")
        if not isinstance(org, str) or not org.strip():
            error("manifest.json: OrganizationName 为空")
        core = manifest.get("CoreVersion")
        if not isinstance(core, str) or not re.match(r"^\d+(\.\d+)*$", core):
            error(f"manifest.json: CoreVersion 应为版本号字符串（如 2.0.0.0），实际为 {core!r}")
        elif core.split(".")[0] != "2":
            warning(f"manifest.json: CoreVersion 主版本为 {core.split('.')[0]}，当前 ClassIsland 2.x 客户端要求主版本为 2")
        for key in ("ClassPlanSource", "TimeLayoutSource", "SubjectsSource", "PolicySource"):
            check_reversion_string(f"manifest.json.{key}", manifest.get(key))
        cp = manifest.get("ClassPlanSource") or {}
        if isinstance(cp.get("Value"), str) and not URL_ID_TEMPLATE_RE.search(cp["Value"]):
            error("manifest.json.ClassPlanSource.Value 应包含 {id} 模板以按班级分发课表")
        subjects_source = manifest.get("SubjectsSource") or {}
        if isinstance(subjects_source.get("Value"), str) and not URL_ID_TEMPLATE_RE.search(subjects_source["Value"]):
            error("manifest.json.SubjectsSource.Value 应包含 {id} 模板，按班级分发科目（否则每台设备都会拿到全年级教师名单）")
        tls = manifest.get("TimeLayoutSource") or {}
        if isinstance(tls.get("Value"), str) and URL_ID_TEMPLATE_RE.search(tls["Value"]):
            warning("manifest.json.TimeLayoutSource.Value 含有 {id} 模板：每班独立时间表通常没有必要，除非确有需求")

        # ── 镜像一致性 ──
        roots = set()
        prefixes = set()
        for key in SOURCE_KEYS:
            value = (manifest.get(key) or {}).get("Value")
            if not isinstance(value, str):
                continue
            match = URL_ROOT_RE.match(value)
            if not match:
                error(
                    f"manifest.json.{key}.Value 不是「GitHub raw 地址」或「镜像前缀 + GitHub raw 地址」：{value!r}"
                )
                continue
            prefix = match.group("prefix") or ""
            roots.add(prefix + match.group("raw"))
            prefixes.add(prefix)
        if len(roots) > 1:
            error(f"manifest.json: 各源地址根不一致：{sorted(roots)}")
        if len(prefixes) > 1:
            error(f"manifest.json: 各源使用了不同的镜像前缀：{sorted(p for p in prefixes if p)}")
        if roots:
            root = next(iter(roots))
            print(f"  源地址根：{root}")
            recorded = ((state_doc or {}).get("mirror") or {}) if isinstance(state_doc, dict) else {}
            recorded_base = str(recorded.get("base") or "")
            if recorded_base and not root.startswith(recorded_base):
                error(f"manifest.json: 源地址根 {root!r} 与 data/state.json 记录的镜像 {recorded_base!r} 不一致")
            if recorded and recorded.get("style") not in ("raw", "github"):
                error(f"data/state.json.mirror.style 非法：{recorded.get('style')!r}")
            if prefix and not recorded_base:
                warning("manifest.json: 使用了镜像前缀，但 data/state.json 未记录镜像，可能是手工改过清单")

    # ── timelayouts.json ──
    timelayouts_profile = load("timelayouts.json")
    lesson_counts = {}
    if timelayouts_profile is not None:
        layouts_map = timelayouts_profile.get("TimeLayouts")
        if not isinstance(layouts_map, dict) or not layouts_map:
            error("timelayouts.json: TimeLayouts 为空")
        else:
            for tl_id, tl in layouts_map.items():
                if not GUID_RE.match(str(tl_id)):
                    error(f"timelayouts.json: 时间表 ID 不是 GUID：{tl_id!r}")
                items = tl.get("Layouts")
                if not isinstance(items, list) or not items:
                    error(f"timelayouts.json: 时间表 {tl_id} 的 Layouts 为空")
                    continue
                for index, item in enumerate(items):
                    for field in ("StartTime", "EndTime"):
                        if not TIME_RE.match(str(item.get(field, ""))):
                            error(f"timelayouts.json: 时间表 {tl_id} 第 {index + 1} 项 {field} 格式非法：{item.get(field)!r}（应为 HH:mm:ss）")
                    ttype = item.get("TimeType")
                    if ttype not in (0, 1, 2, 3):
                        error(f"timelayouts.json: 时间表 {tl_id} 第 {index + 1} 项 TimeType 非法：{ttype!r}")
                    if ttype == 2 and item.get("StartTime") != item.get("EndTime"):
                        warning(f"timelayouts.json: 分割线第 {index + 1} 项起止时间不同（ClassIsland 会将分割线视为瞬时点）")
                lesson_counts[str(tl_id)] = sum(1 for item in items if item.get("TimeType") == 0)
                if lesson_counts[str(tl_id)] < 1:
                    error(f"timelayouts.json: 时间表 {tl_id} 没有任何上课时间点")

    # ── week_cycle（两周轮换）时间表 ──
    config = load("build-config.json") or {}
    cycle_cfg = config.get("week_cycle") if isinstance(config, dict) else None
    home_layout_ids = set()
    if isinstance(cycle_cfg, dict) and cycle_cfg.get("enabled"):
        school_over = str(cycle_cfg.get("home_week_friday_school_over") or "").strip()
        if school_over:
            if len(school_over.split(":")) == 2:
                school_over += ":00"
            for tl_id, tl in (timelayouts_profile.get("TimeLayouts") or {}).items() if timelayouts_profile else []:
                lessons = [item for item in (tl.get("Layouts") or []) if item.get("TimeType") == 0]
                if lessons and str(lessons[-1].get("EndTime")) == school_over:
                    home_layout_ids.add(str(tl_id))
            if not home_layout_ids:
                error(
                    f"timelayouts.json: week_cycle 已启用，但没有任何时间表的末节课在 {school_over} 结束"
                    "（缺少「回家周周五」提前放学时间表，请重新运行 build.py）"
                )
            elif len(home_layout_ids) > 1:
                warning(f"timelayouts.json: 有多个时间表的末节课都在 {school_over} 结束：{sorted(home_layout_ids)}")
        else:
            error("build-config.json: week_cycle.home_week_friday_school_over 未设置")

        # 回家周周五上午缩短的课间必须真的落在时间表里（只改配置不重建是最常见的坑）
        raw_break = str(cycle_cfg.get("home_week_friday_morning_break") or "").strip()
        if raw_break and home_layout_ids:
            parts = [item.strip() for item in raw_break.split("-")]
            if len(parts) != 2:
                error(f"build-config.json: week_cycle.home_week_friday_morning_break 应形如 \"08:15-08:25\"，实际 {raw_break!r}")
            else:
                wanted_start = parts[0] if len(parts[0].split(":")) == 3 else parts[0] + ":00"
                wanted_end = parts[1] if len(parts[1].split(":")) == 3 else parts[1] + ":00"
                for tl_id in sorted(home_layout_ids):
                    layouts = ((timelayouts_profile or {}).get("TimeLayouts") or {}).get(tl_id, {}).get("Layouts") or []
                    matched = any(
                        item.get("TimeType") == 1
                        and str(item.get("StartTime")) == wanted_start
                        and str(item.get("EndTime")) == wanted_end
                        for item in layouts
                    )
                    if not matched:
                        error(
                            f"timelayouts.json: 回家周周五时间表 {tl_id} 里没有 {parts[0]}–{parts[1]} 的课间"
                            "（build-config.json 已配置 home_week_friday_morning_break，但档案未重建）"
                        )

    # ── 平常日子的大课间缩放 & 课间名称 ──
    shrink_raw = config.get("morning_big_break_shrink_minutes", 0) if isinstance(config, dict) else 0
    try:
        shrink = int(shrink_raw or 0)
    except (TypeError, ValueError):
        error(f"build-config.json: morning_big_break_shrink_minutes 应为整数，实际 {shrink_raw!r}")
        shrink = 0
    if shrink < 0 or shrink > 30:
        error(f"build-config.json: morning_big_break_shrink_minutes 超出合理范围（0–30）：{shrink}")

    # 课间（TimeType=1）名称必须统一
    for tl_id, tl in ((timelayouts_profile or {}).get("TimeLayouts") or {}).items():
        for index, item in enumerate(tl.get("Layouts") or []):
            if item.get("TimeType") == 1 and str(item.get("BreakName") or "") != BREAK_NAME:
                error(
                    f"timelayouts.json: 时间表 {tl_id} 第 {index + 1} 个课间名称应为「{BREAK_NAME}」，"
                    f"实际 {item.get('BreakName')!r}"
                )

    # 大课间应当等于「源站课间长度 - shrink」（shrink=0 时即源站原样），避免改了配置却没重建
    snapshot = load("data/snapshot/result_actual.json")
    patterns = [str(p) for p in (config.get("exclude_slot_patterns") or []) if p]
    if isinstance(snapshot, dict) and isinstance(snapshot.get("times"), list):
        src_slots = snapshot.get("slots") or []
        src_times = snapshot["times"]
        keep = [
            i for i, name in enumerate(src_slots)
            if not any(re.search(p, str(name)) for p in patterns)
        ]
        if len(keep) >= 2:
            gap_start = str(src_times[keep[0]]).split("-")[1].strip()
            if len(gap_start.split(":")) == 2:
                gap_start += ":00"
            gap_end = str(src_times[keep[1]]).split("-")[0]
            end_minutes = int(gap_end.split(":")[0]) * 60 + int(gap_end.split(":")[1])
            expected_end = end_minutes - shrink
            wanted = f"{expected_end // 60:02d}:{expected_end % 60:02d}:00"
            main_tl = ((timelayouts_profile or {}).get("TimeLayouts") or {}).get(TIMELAYOUT_MAIN_ID)
            if isinstance(main_tl, dict):
                matched = [
                    item for item in (main_tl.get("Layouts") or [])
                    if item.get("TimeType") == 1 and str(item.get("StartTime")) == gap_start
                ]
                if not matched:
                    error(f"timelayouts.json: 常规时间表里找不到 {gap_start[:5]} 开始的大课间")
                elif str(matched[0].get("EndTime")) != wanted:
                    error(
                        f"timelayouts.json: 大课间应为 {gap_start[:5]}–{wanted[:5]}"
                        f"（源站 {gap_start[:5]}–{gap_end[:5]} 缩短 {shrink} 分钟），实际 {matched[0].get('EndTime')}"
                    )

    # ── subjects/{id}.json（按班级拆分的科目表）──
    subject_files = sorted(
        glob.glob(os.path.join(REPO_ROOT, "subjects", "*.json")),
        key=lambda p: (len(os.path.basename(p)[:-5]), os.path.basename(p)),
    )
    if not subject_files:
        error("subjects/ 目录下没有任何按班级拆分的科目文件")
    if os.path.exists(os.path.join(REPO_ROOT, "subjects.json")):
        error("仓库中仍存在旧的全局科目总表 subjects.json（会把全年级教师姓名分发给每台设备，请删除）")
    subjects_by_class = {}
    subject_name_sets = {}
    for path in subject_files:
        rel = os.path.relpath(path, REPO_ROOT)
        cid = os.path.basename(path)[:-5]
        if not cid.isdigit():
            error(f"{rel}: 文件名应为纯数字班级 ID，实际为 {cid!r}")
        profile = load(os.path.join("subjects", os.path.basename(path)))
        if profile is None:
            continue
        subjects_map = profile.get("Subjects")
        if not isinstance(subjects_map, dict) or not subjects_map:
            error(f"{rel}: Subjects 为空")
            continue
        ids = set()
        names = set()
        for sid, subject in subjects_map.items():
            if not GUID_RE.match(str(sid)):
                error(f"{rel}: 科目 ID 不是 GUID：{sid!r}")
            ids.add(str(sid))
            if not str(subject.get("Name", "")).strip():
                error(f"{rel}: 科目 {sid} 缺少 Name")
            teacher = subject.get("TeacherName", "")
            if not str(teacher).strip():
                warning(f"{rel}: 科目 {subject.get('Name')}（{sid}）没有教师名")
            names.add((str(subject.get("Name", "")), str(teacher)))
        subjects_by_class[cid] = ids
        subject_name_sets[cid] = names

    # ── classplans/{id}.json ──
    tl_ids = set()
    if timelayouts_profile and isinstance(timelayouts_profile.get("TimeLayouts"), dict):
        tl_ids = set(timelayouts_profile["TimeLayouts"].keys())

    files = sorted(
        glob.glob(os.path.join(REPO_ROOT, "classplans", "*.json")),
        key=lambda p: (len(os.path.basename(p)[:-5]), os.path.basename(p)),
    )
    if not files:
        error("classplans/ 目录下没有任何课表文件")
    seen_plan_names = set()
    stub_count = 0
    rot_summary = set()  # (weekday, div) 样本，用于汇总输出
    for path in files:
        rel = os.path.relpath(path, REPO_ROOT)
        cid = os.path.basename(path)[:-5]
        if not cid.isdigit():
            error(f"{rel}: 文件名应为纯数字班级 ID，实际为 {cid!r}")
        profile = load(os.path.join("classplans", os.path.basename(path)))
        if profile is None:
            continue
        plans = profile.get("ClassPlans")
        if not isinstance(plans, dict) or not plans:
            error(f"{rel}: ClassPlans 为空")
            continue
        weekdays = {}
        used_subject_ids = set()
        for plan_id, plan in plans.items():
            if not GUID_RE.match(str(plan_id)):
                error(f"{rel}: 课表 ID 不是 GUID：{plan_id!r}")
            name = plan.get("Name", "")
            if not str(name).strip():
                error(f"{rel}: 存在未命名的课表")
            if name in seen_plan_names:
                error(f"{rel}: 课表名称重复：{name!r}")
            seen_plan_names.add(name)
            rule = plan.get("TimeRule", {})
            weekday = rule.get("WeekDay")
            if not isinstance(weekday, int) or not 0 <= weekday <= 6:
                error(f"{rel}: 课表 {name!r} 的 WeekDay 非法：{weekday!r}（应为 0=周日 … 6=周六）")
                continue
            week_div = rule.get("WeekCountDiv")
            week_total = rule.get("WeekCountDivTotal")
            if not isinstance(week_div, int) or not 0 <= week_div <= 99:
                error(f"{rel}: 课表 {name!r} 的 WeekCountDiv 应为非负整数：{week_div!r}（0=每周生效，n=第 n 周）")
                continue
            if not isinstance(week_total, int) or week_total < 0:
                error(f"{rel}: 课表 {name!r} 的 WeekCountDivTotal 应为非负整数：{week_total!r}")
                continue
            if week_div > 0 and (week_div > week_total or week_total < 2):
                error(
                    f"{rel}: 课表 {name!r} 轮换参数非法：WeekCountDiv={week_div} 应不超过 "
                    f"WeekCountDivTotal={week_total}，且双周轮换要求 WeekCountDivTotal ≥ 2"
                )
                continue
            plan_enabled = plan.get("IsEnabled", True) is not False
            if plan_enabled:
                weekdays.setdefault(weekday, []).append((week_div, week_total, name))

            group = plan.get("AssociatedGroup")
            if group is not None and str(group).lower() not in (GLOBAL_GROUP_GUID, DEFAULT_GROUP_GUID):
                error(
                    f"{rel}: 课表 {name!r} 的 AssociatedGroup 应为全局课表群（全零 GUID）或默认课表群，"
                    f"实际为 {group!r}（其他课表群会随客户端选中状态变化，导致课表不生效）"
                )

            if str(plan.get("TimeLayoutId")) not in tl_ids:
                error(f"{rel}: 课表 {name!r} 引用了不存在的时间表 {plan.get('TimeLayoutId')}")
            elif home_layout_ids and plan_enabled:
                layout_key = str(plan.get("TimeLayoutId"))
                if week_div == 2 and layout_key not in home_layout_ids:
                    error(f"{rel}: 课表 {name!r} 为第 2 周（回家周）课表，应引用提前放学时间表")
                if week_div == 1 and layout_key in home_layout_ids:
                    error(f"{rel}: 课表 {name!r} 为第 1 周（返校周）课表，不应引用提前放学时间表")
            classes = plan.get("Classes")
            if not isinstance(classes, list):
                error(f"{rel}: 课表 {name!r} 的 Classes 应为数组")
                continue
            if not classes and plan_enabled:
                error(f"{rel}: 课表 {name!r} 没有任何课程")
                continue
            if not plan_enabled:
                stub_count += 1
                continue
            expected_lessons = lesson_counts.get(str(plan.get("TimeLayoutId")))
            if expected_lessons is not None and len(classes) != expected_lessons:
                error(
                    f"{rel}: 课表 {name!r} 课程数 {len(classes)} 与所引用时间表上课点数 {expected_lessons} 不一致"
                )
            class_subject_ids = subjects_by_class.get(cid) or set()
            for index, cls in enumerate(classes):
                sid = str(cls.get("SubjectId"))
                enabled = cls.get("IsEnabled", True)
                if enabled:
                    used_subject_ids.add(sid)
                if enabled and sid not in class_subject_ids:
                    error(
                        f"{rel}: 课表 {name!r} 第 {index + 1} 节引用了本班科目表里没有的科目 {sid}"
                        f"（subjects/{cid}.json 应包含本班全部任课教师）"
                    )

        # 本班科目表不应包含本班用不到的「科目+教师」（否则等于把别人的老师也发给了这台设备）
        extra_subjects = sorted((subjects_by_class.get(cid) or set()) - used_subject_ids)
        if extra_subjects:
            error(
                f"{rel}: subjects/{cid}.json 含本班课表未使用的科目 {extra_subjects}"
                "（应只包含本班任课教师）"
            )

        # 多周轮换组合校验：同一周同一天只能命中一张课表
        for weekday, entries in sorted(weekdays.items()):
            non_rotation = [(d, t, n) for d, t, n in entries if d == 0]
            rotation = [(d, t, n) for d, t, n in entries if d > 0]
            if len(non_rotation) > 1:
                names = "、".join(n for _, _, n in non_rotation)
                error(f"{rel}: 星期 {weekday} 存在多张「每周生效」课表（{names}），同一天将命中多张课表")
            if non_rotation and rotation:
                error(
                    f"{rel}: 星期 {weekday} 同时存在「每周生效」与「多周轮换」课表，"
                    "轮换周内会出现两张课表同时匹配（客户端只取其一，另一张永不生效）"
                )
            if rotation:
                divs = sorted(d for d, _, _ in rotation)
                if divs != [1, 2]:
                    error(f"{rel}: 星期 {weekday} 的轮换课表应成对覆盖第 1、2 周，实际为第 {divs} 周")
                for d, t, n in rotation:
                    if t != 2:
                        error(f"{rel}: 课表 {n!r} 的 WeekCountDivTotal 应为 2（双周轮换），实际为 {t}")
                rot_summary.add((weekday, tuple(divs)))

    # ── presets/ ──
    presets = sorted(glob.glob(os.path.join(REPO_ROOT, "presets", "ManagementPreset-*.json")))
    if not presets:
        error("presets/ 目录下没有任何客户端预设文件")
    manifest_url = None
    if manifest and isinstance(manifest.get("ClassPlanSource"), dict):
        # manifest 地址与预设地址同根，从任一源地址推导
        root = None
        for key in ("ClassPlanSource", "TimeLayoutSource", "SubjectsSource", "PolicySource"):
            value = (manifest.get(key) or {}).get("Value")
            if isinstance(value, str):
                root = value.split("/classplans/")[0] if "/classplans/" in value else value.rsplit("/", 1)[0]
                break
        if root:
            manifest_url = f"{root}/manifest.json"
    for path in presets:
        rel = os.path.relpath(path, REPO_ROOT)
        expected_id = os.path.basename(path)[len("ManagementPreset-"):-5]
        preset = load(os.path.join("presets", os.path.basename(path)))
        if preset is None:
            continue
        if preset.get("ManagementServerKind") != 0:
            error(f"{rel}: ManagementServerKind 应为 0")
        template = preset.get("ManifestUrlTemplate")
        if not isinstance(template, str) or not template.startswith("https://"):
            error(f"{rel}: ManifestUrlTemplate 非法：{template!r}")
        elif manifest_url and template != manifest_url:
            error(f"{rel}: ManifestUrlTemplate 与 manifest.json 地址不一致：{template!r} ≠ {manifest_url!r}")
        if str(preset.get("ClassIdentity")) != expected_id:
            error(f"{rel}: ClassIdentity 与文件名不一致：{preset.get('ClassIdentity')!r} ≠ {expected_id!r}")

    # ── policy.json ──
    policy = load("policy.json")
    if policy is not None:
        allowed = {
            "DisableProfileEditing", "DisableProfileClassPlanEditing", "DisableProfileTimeLayoutEditing",
            "DisableProfileSubjectsEditing", "DisableSettingsEditing", "DisableSplashCustomize",
            "DisableDebugMenu", "DisableEasterEggs", "AllowExitManagement",
        }
        for key, value in policy.items():
            if key not in allowed:
                warning(f"policy.json: 未识别的字段 {key!r}（已忽略，请检查拼写）")
            elif not isinstance(value, bool):
                error(f"policy.json: {key} 应为布尔值，实际为 {value!r}")

    # ── data/state.json：策略版本是否跟上内容变化 ──
    state = load("data/state.json")
    if isinstance(state, dict) and isinstance(policy, dict):
        recorded_policy = state.get("policy") if isinstance(state.get("policy"), dict) else {}
        actual_hash = hashlib.sha256(canonical_json(policy).encode("utf-8")).hexdigest()
        if recorded_policy.get("hash") and recorded_policy["hash"] != actual_hash:
            error(
                "data/state.json: policy 哈希与 policy.json 不一致"
                "（策略已改动但未重新运行 build.py，客户端可能拿不到新策略）"
            )
        manifest_policy = (manifest or {}).get("PolicySource") or {}
        if isinstance(manifest_policy.get("Version"), int) and isinstance(recorded_policy.get("version"), int):
            if manifest_policy["Version"] < recorded_policy["version"]:
                error("manifest.json: PolicySource.Version 低于 data/state.json 记录的策略版本")

    # ── 默认设置源（下发学期开始时间）──
    settings_rel = os.path.join("settings", "default-settings.json")
    settings_doc = load(settings_rel) if os.path.exists(os.path.join(REPO_ROOT, settings_rel)) else None
    default_settings_source = (manifest or {}).get("DefaultSettingsSource")
    if default_settings_source is not None:
        check_reversion_string("manifest.json.DefaultSettingsSource", default_settings_source)
        if settings_doc is None:
            error("manifest.json: 挂了 DefaultSettingsSource，但 settings/default-settings.json 不存在")
        else:
            raw = settings_doc.get("SingleWeekStartTime")
            try:
                parsed = datetime.fromisoformat(str(raw)).date()
            except ValueError:
                parsed = None
            if parsed is None:
                error(f"settings/default-settings.json: SingleWeekStartTime 无法解析：{raw!r}")
            elif isinstance(cycle_cfg, dict) and cycle_cfg.get("enabled"):
                monday_raw = str(cycle_cfg.get("return_week_monday") or "").strip()
                try:
                    expected = date.fromisoformat(monday_raw) - timedelta(days=1)
                except ValueError:
                    expected = None
                if expected and parsed != expected:
                    error(
                        f"settings/default-settings.json: SingleWeekStartTime 为 {parsed}，"
                        f"应为返校周周一前一天 {expected}（否则全新机器单双周会错一周）"
                    )
    elif settings_doc is not None:
        warning("settings/default-settings.json 存在，但 manifest.json 未挂 DefaultSettingsSource（客户端不会应用）")

    # ── 班级文件与预设文件是否一一对应（识别未清理的陈旧文件）──
    plan_ids = {os.path.basename(path)[:-5] for path in files}
    preset_ids = {os.path.basename(path)[len("ManagementPreset-"):-5] for path in presets}
    subject_ids_by_class = set(subjects_by_class.keys())
    if subject_ids_by_class != plan_ids:
        error(
            f"subjects/: 班级集合与 classplans/ 不一致"
            f"（缺少 {sorted(plan_ids - subject_ids_by_class, key=int)}，多余 {sorted(subject_ids_by_class - plan_ids, key=int)}）"
        )
    missing_presets = plan_ids - preset_ids
    orphan_presets = preset_ids - plan_ids
    if missing_presets:
        error(f"presets/: 缺少班级 {sorted(missing_presets, key=int)} 的预设文件")
    if orphan_presets:
        error(f"presets/: 存在无对应课表文件的预设 {sorted(orphan_presets, key=int)}（源数据已无该班级，请重新构建以清理）")

    # ── 汇总 ──
    print(f"校验完成：{len(files)} 份课表文件，{len(subject_files)} 份班级科目表，{len(presets)} 份预设。")
    if rot_summary:
        summary_text = "、".join(
            f"星期{day}→第{','.join(str(d) for d in divs)}周" for day, divs in sorted(rot_summary)
        )
        print(f"多周轮换：{summary_text}（客户端按「学期开始时间」自动切换）。")
    if isinstance(cycle_cfg, dict) and str(cycle_cfg.get("home_week_friday_morning_break") or "").strip():
        print(f"回家周周五上午课间：{cycle_cfg['home_week_friday_morning_break']}（其余日子保持常规课间）。")
    if stub_count:
        print(f"停用课表（含退场中和桩）：{stub_count} 张（IsEnabled=false，不参与当日命中）。")
    for item in warnings:
        print(f"警告：{item}")
    if errors:
        print()
        for item in errors:
            print(f"错误：{item}")
        print(f"\n共 {len(errors)} 处错误。")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
