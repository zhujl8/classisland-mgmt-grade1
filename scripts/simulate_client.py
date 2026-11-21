#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""simulate_client.py —— 按 ClassIsland 源码逻辑模拟客户端逐日课表命中。

复刻 LessonsService.CheckClassPlan / GetCyclePositionsByDate 的判定公式：
    totalElapsedWeeks = floor((date - Settings.SingleWeekStartTime) / 7 天)
    position(Total)   = (totalElapsedWeeks % Total) + 1        # 1-based
    plan 生效         = WeekDay 匹配 且 (WeekCountDiv == 0 或 WeekCountDiv == position)

学期首日取 build-config.json 推导值（return_week_monday 前一天，周日）。
零依赖，仅用于本地自检，不属于集控运行时文件。
"""

import json
import os
import sys
from datetime import date, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]  # date.weekday()


def load(path):
    with open(os.path.join(REPO_ROOT, path), "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    config = load("build-config.json")
    cycle = config["week_cycle"]
    if not cycle.get("enabled"):
        print("week_cycle 未启用，无需模拟")
        return 0

    # 客户端学期首日（Settings.SingleWeekStartTime）= 返校周周一的前一天（周日）
    semester_start = date.fromisoformat(cycle["return_week_monday"]) - timedelta(days=1)
    school_over = cycle["home_week_friday_school_over"]
    first_class = cycle["home_week_friday_first_class"]

    classplan = load("classplans/1.json")
    plans = list(classplan["ClassPlans"].values())

    timelayouts = load("timelayouts.json")["TimeLayouts"]
    layout_end = {}
    for tl_id, tl in timelayouts.items():
        lessons = [i for i in tl["Layouts"] if i["TimeType"] == 0]
        layout_end[tl_id] = lessons[-1]["EndTime"]

    def matches(plan: dict, day: date) -> bool:
        rule = plan["TimeRule"]
        # DayOfWeek 约定：周日=0，周一=1 …（C# 枚举值；date.weekday() 周一=0，需换算）
        csharp_dow = (day.weekday() + 1) % 7
        if rule["WeekDay"] != csharp_dow:
            return False
        if rule["WeekCountDiv"] == 0:
            return True
        elapsed = (day - semester_start).days // 7
        position = (elapsed % rule["WeekCountDivTotal"]) + 1
        return rule["WeekCountDiv"] == position

    errors = []
    print(f"学期首日（客户端设置）：{semester_start}（{DAY_CN[semester_start.weekday()]}）")
    print(f"{'日期':<12}{'星期':<4}{'轮周':<6}命中课表 / 末节结束")
    # 从返校周周一开始覆盖三个完整两周循环
    start = date.fromisoformat(cycle["return_week_monday"])
    for offset in range(0, 21):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue  # 周六周日无课
        elapsed = (day - semester_start).days // 7
        position = (elapsed % 2) + 1
        hits = [p for p in plans if matches(p, day)]
        if len(hits) != 1:
            errors.append(f"{day} 命中 {len(hits)} 张课表：{[p['Name'] for p in hits]}")
            continue
        plan = hits[0]
        end = layout_end[plan["TimeLayoutId"]]
        label = plan["Name"]
        # 期望校验
        week_type = "返校周" if position == 1 else "回家周"
        if day.weekday() == 4:  # 周五
            if week_type == "返校周" and end != "16:35:00":
                errors.append(f"{day} 返校周周五末节应 16:35，实际 {end}")
            if week_type == "回家周" and (end != f"{school_over}:00" or plan["TimeRule"]["WeekCountDiv"] != 2):
                errors.append(f"{day} 回家周周五末节应 {school_over}，实际 {end}")
        elif plan["TimeRule"]["WeekCountDiv"] != 0:
            errors.append(f"{day} {week_type}{DAY_CN[day.weekday()]}不应命中轮换课表")
        print(f"{day}  {DAY_CN[day.weekday()]}   第{position}周  {label}（末节 {end[:5]}）")

    # 关键日期抽查：用户给出的实例
    check = {
        "2026-11-23": "周一正常课",
        "2026-11-27": "返校周周五（全天正常，16:35 放学）",
        "2026-12-04": "回家周周五（12:20 上课，14:55 放学）",
        "2026-12-07": "下个返校周周一",
    }
    print()
    for text, expect in check.items():
        day = date.fromisoformat(text)
        hits = [p for p in plans if matches(p, day)]
        if len(hits) != 1:
            errors.append(f"{text}（{expect}）命中 {len(hits)} 张课表")
            continue
        end = layout_end[hits[0]["TimeLayoutId"]]
        print(f"抽查 {text} {DAY_CN[day.weekday()]}：{hits[0]['Name']}，末节 {end[:5]} —— 应为：{expect}")

    if errors:
        print("\n发现问题：")
        for item in errors:
            print(f"  ✗ {item}")
        return 1
    print("\n模拟通过：21 天逐日命中唯一课表，返校周/回家周周五切换正确。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
