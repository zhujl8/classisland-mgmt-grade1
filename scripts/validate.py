#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate.py —— 校验仓库中生成的集控档案是否满足 ClassIsland 静态集控规范。

校验范围：
  1. manifest.json 结构（ServerKind / ReVersionString / CoreVersion / {id} 模板 / https 地址）
  2. timelayouts.json（时间点类型与时间格式、上课点数量）
  3. subjects.json（GUID 键、科目字段）
  4. classplans/{id}.json（时间表/科目引用完整性、WeekDay 取值、每张课表节数）
  5. presets/（ManifestUrlTemplate 与 manifest 一致、ClassIdentity 与文件名一致）
  6. policy.json（仅允许已知布尔字段）

零第三方依赖，Python 3.9+。全部通过退出码 0，任何问题退出码 1。
"""

import glob
import json
import os
import re
import sys
import uuid

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
TIME_RE = re.compile(r"^\d{2,3}:\d{2}:\d{2}$")
URL_ID_TEMPLATE_RE = re.compile(r"\{id\}")

errors: list[str] = []
warnings: list[str] = []


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
    base_url = None
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
        tls = manifest.get("TimeLayoutSource") or {}
        if isinstance(tls.get("Value"), str) and URL_ID_TEMPLATE_RE.search(tls["Value"]):
            warning("manifest.json.TimeLayoutSource.Value 含有 {id} 模板：每班独立时间表通常没有必要，除非确有需求")
        tlv = (tls.get("Value") or "")
        base_url = tlv.rsplit("/", 1)[0] if "/" in tlv else None

    # ── timelayouts.json ──
    timelayouts_profile = load("timelayouts.json")
    lesson_count = None
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
                lesson_count = sum(1 for item in items if item.get("TimeType") == 0)
            if lesson_count and lesson_count < 1:
                error("timelayouts.json: 没有任何上课时间点")

    # ── subjects.json ──
    subjects_profile = load("subjects.json")
    subject_ids = set()
    if subjects_profile is not None:
        subjects_map = subjects_profile.get("Subjects")
        if not isinstance(subjects_map, dict) or not subjects_map:
            error("subjects.json: Subjects 为空")
        else:
            for sid, subject in subjects_map.items():
                if not GUID_RE.match(str(sid)):
                    error(f"subjects.json: 科目 ID 不是 GUID：{sid!r}")
                subject_ids.add(str(sid))
                if not str(subject.get("Name", "")).strip():
                    error(f"subjects.json: 科目 {sid} 缺少 Name")
                teacher = subject.get("TeacherName", "")
                if not str(teacher).strip():
                    warning(f"subjects.json: 科目 {subject.get('Name')}（{sid}）没有教师名")

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
        weekdays = []
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
            else:
                weekdays.append(weekday)
            if str(plan.get("TimeLayoutId")) not in tl_ids:
                error(f"{rel}: 课表 {name!r} 引用了不存在的时间表 {plan.get('TimeLayoutId')}")
            classes = plan.get("Classes")
            if not isinstance(classes, list) or not classes:
                error(f"{rel}: 课表 {name!r} 没有任何课程")
                continue
            if lesson_count is not None and len(classes) != lesson_count:
                error(f"{rel}: 课表 {name!r} 课程数 {len(classes)} 与时间表上课点数 {lesson_count} 不一致")
            for index, cls in enumerate(classes):
                sid = str(cls.get("SubjectId"))
                enabled = cls.get("IsEnabled", True)
                if enabled and sid not in subject_ids:
                    error(f"{rel}: 课表 {name!r} 第 {index + 1} 节引用了不存在的科目 {sid}")
        if sorted(set(weekdays)) != sorted(set(weekdays)):
            error(f"{rel}: 存在重复的 WeekDay")
        if len(weekdays) != len(set(weekdays)):
            error(f"{rel}: 存在 WeekDay 重复的课表")

    # ── presets/ ──
    presets = sorted(glob.glob(os.path.join(REPO_ROOT, "presets", "ManagementPreset-*.json")))
    if not presets:
        error("presets/ 目录下没有任何客户端预设文件")
    manifest_url = None
    mf = manifest.get("ClassPlanSource") if manifest else None
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

    # ── 汇总 ──
    print(f"校验完成：{len(files)} 份课表文件，{len(presets)} 份预设。")
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
