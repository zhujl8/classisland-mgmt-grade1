# ClassIsland 高一课程表静态集控

面向全高一年级（1–20 班）的 [ClassIsland](https://github.com/ClassIsland/ClassIsland) **静态集控仓库**：课表数据自动从源站抓取、转换为 ClassIsland 2.x 档案格式，托管在 GitHub 上，班级多媒体屏幕上的 ClassIsland 通过集控功能按班级自动拉取对应课表。

内置 GitHub Actions：**每天 06:30（北京时间）自动运行一次**（也可在 Actions 页手动触发），完成「抓取源数据 → 生成档案 → 递增版本号 → 提交推送」全流程，无需手工编辑任何 JSON。

```
源站课表页面 (duanjr.site/tools/class_schedule)
        │  API: /tools/class_schedule/api/schedule?file=result_actual.json
        ▼
GitHub Action（本仓库 scripts/build.py）
        │  转换为 ClassIsland 档案 + 版本管理
        ▼
manifest.json + subjects/{id}.json + timelayouts.json + classplans/{id}.json
        + settings/default-settings.json（可选，下发「学期开始时间」）
        │  镜像加速（默认开）：自动挑最快的公益镜像，把地址改写成「镜像 + GitHub 原始地址」
        │  GitHub 静态托管（raw.githubusercontent.com）
        ▼
班级机 ClassIsland（加入管理，ID = 班级号 1–20）
```

> 集控规范参考官方文档：[docs.classisland.tech/management](https://docs.classisland.tech/management/)（本仓库的清单、档案、客户端预设均按其 2.x 格式生成）。

## 仓库结构

```
├── manifest.json                  # 集控清单（Action 自动维护版本号；地址默认经镜像加速改写）
├── policy.json                    # 集控策略（管理员手动维护内容，版本号按内容哈希自动维护）
├── subjects/
│   ├── 1.json … 20.json           # 每班科目表（自动生成，只含本班任课教师 7–11 条）
├── timelayouts.json               # 时间表（自动生成：常规表 + 回家周周五提前放学表）
├── settings/
│   └── default-settings.json      # 默认设置源（仅含「学期开始时间」，只对全新机器生效）
├── classplans/
│   ├── 1.json … 20.json           # 每班课表档案（自动生成，每班 6 张：周一~周四 + 返校周周五 + 回家周周五）
├── presets/
│   └── ManagementPreset-1..20.json # 每班客户端预设文件（自动生成）
├── scripts/
│   ├── build.py                   # 抓取源数据 → 生成全部档案（零第三方依赖）
│   ├── validate.py                # 结构校验（CI 中自动执行）
│   └── simulate_client.py         # 本地自检：按客户端判定逻辑逐日验证课表命中（可选）
├── docs/班级对照表.md              # 可选：集控 ID ↔ 班级元数据（默认不生成，见 generate_roster）
├── build-config.json              # 构建参数（组织名、源地址、排除节次、轮换锚点等）
├── data/
│   ├── state.json                 # 版本状态（内容哈希，自动维护）
│   └── snapshot/                  # 源数据快照（便于人工审查每次变更）
├── LICENSE                        # MIT
└── .github/workflows/update.yml   # 更新 Action（手动触发，可开启定时）
```

## 快速开始

### 第 1 步：推送本仓库到 GitHub

方式 A（命令行，推荐）：

```bash
# 本压缩包已包含初始 git 提交；若是解压后没有 .git 的目录，先执行：
# git init -b main && git add -A && git commit -m "init: 高一课程表静态集控"
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

方式 B（网页）：在 GitHub 新建空仓库后，解压本压缩包，把全部文件（含 `.github` 隐藏目录）拖拽上传到仓库主页。

### 第 2 步：运行一次 Action（关键）

打开仓库 **Actions → 更新高一课程表 → Run workflow → Run**。

这一次运行会：
- 自动把 manifest.json 与 presets/ 里的占位地址 `OWNER/REPO` 替换为你的真实仓库地址（`https://raw.githubusercontent.com/<用户名>/<仓库>/<分支>/…`）；
- 重新抓取源数据、生成全部档案、递增版本号并提交推送。

完成后即可在仓库中看到可用的集控清单地址：

```
https://raw.githubusercontent.com/<你的用户名>/<仓库名>/<分支>/manifest.json
```

### 第 3 步：班级机加入管理

**方式 A：预设文件（免手输，推荐）**

1. 从仓库下载对应班级的 `presets/ManagementPreset-<班级号>.json`；
2. 在「加入管理」对话框里用**浏览**选中这个文件（点「连接」即可，ID 会自动填为班级号）；  
   若想让它开机自动读到：把文件重命名为 `ManagementPreset.json`，放进 **ClassIsland 的数据目录**（见下方说明）；
3. 首次使用的设备在欢迎向导首页点「加入管理」，已初始化过的设备在 **应用设置 → 更多选项（右上角三个点）→ 加入管理…**；
4. 点「连接」→ 弹窗确认「加入」→ 应用自动重启。

> **数据目录不是 exe 所在目录**（这是 1.x 时代的说法，2.x 已改）：
> - 便携版（folder）：`<包根>/data/`，即 `Settings.json` 所在的那一层；
> - 安装版 / deb / AppImage / pkg / msix：`%AppData%\ClassIsland\Data\`（Linux/macOS 对应各自的用户数据目录）。
>
> 文件放错位置不会报错，只是对话框不会自动加载它——直接在对话框里浏览选中，效果一样。

**方式 B：手动填写**

先在本地建一个 JSON（例如 `join.json`），在「加入管理」里选中它：

```json
{
  "ManagementServerKind": 0,
  "ManagementServer": "",
  "ManagementServerGrpc": "",
  "ManifestUrlTemplate": "https://raw.githubusercontent.com/<你的用户名>/<仓库名>/<分支>/manifest.json",
  "ClassIdentity": "5"
}
```

> 2.x 的「加入管理」对话框**只认这种客户端配置**（`ManagementServerKind` / `ManifestUrlTemplate` / `ClassIdentity`），没有单独的“清单 URL 输入框”。**不要把 `manifest.json` 当配置文件选**——它没有 `ManifestUrlTemplate` 字段，选它必然报 `Invalid URI: The URI is empty`。

集控 ID 即档案 URL 中的 `{id}`：ID 为 `5` 的设备会拉取 `classplans/5.json`（高一5班）。每台设备必须用唯一的班级号，否则会拿到别人的课表。

加入成功后：**集控选项**页会显示“您目前正在受 <组织名> 的管理”，菜单里出现「退出管理…」。主界面即按当日课表显示，周一至周五自动切换到对应的日课表（`TimeRule.WeekDay` 规则）。

### 第 4 步：学期开始时间（两周轮换必做）

本课表使用单双周（两周循环）：周五分为「返校周 / 回家周」两张课表，客户端需要知道两周循环从哪天起算才能自动切换。界面上这一项叫 **「学期开始时间」**（描述里才写“学期首日”），目前应为 **2026-11-22（周日）**。

- **全新安装的机器**：清单里挂了 `DefaultSettingsSource`，客户端在欢迎向导完成前会自动写入该日期，**无需手工设置**。该设置源只对“还没走完欢迎向导”的实例生效，且整体替换设置（其余项取默认值），所以仓库里这个文件只放这一个字段。
- **已经初始化过的机器**：仍需人工核对一次：应用设置 → 常规 → 学期开始时间 = 2026-11-22（周日）。未设置时客户端默认以“安装当周的周日”起算，单双周可能正好错开一周。
- 该日期由 `build-config.json` 的 `week_cycle.return_week_monday` 推导（= 返校周周一的前一天）。重新构建后，Action 运行摘要里会打印最新值。

## 两周轮换（单双周）

高一两周回一次家：**返校周**（循环第 1 周）周五全天正常；**回家周**（循环第 2 周）周五上午课间更短、下午提前——第一节后只有 **08:15–08:25** 的课间（平常日子默认是 08:15–08:40），其后上午节次整体前移；下午 **12:20 上第一节课，14:55 第三节课后放学**；周六周日无课。

> 平常日子的「大课间」也可缩放：`build-config.json` 的 `morning_big_break_shrink_minutes`（默认 5）表示把它缩短几分钟，缩短后上午其余节次整体前移。设为 `0` 则与源站原样一致（08:15–08:45）。

仓库实现方式（对应 ClassIsland 的多周轮换机制 `TimeRule.WeekCountDiv / WeekCountDivTotal`）：

| 课表 | 生效规则 | 时间表 |
| --- | --- | --- |
| 周一 ~ 周四（每班 4 张） | 每周生效（`WeekCountDiv = 0`） | 常规时间表 |
| 周五 · 返校周 | 每 2 周的第 1 周（`WeekCountDiv = 1`） | 常规时间表 |
| 周五 · 回家周 | 每 2 周的第 2 周（`WeekCountDiv = 2`） | 回家周周五时间表 |

回家周周五时间表：上午把第一个长课间压到 10 分钟、其后整体前移，午休分割线后下午三节提前：

| 节次 | 回家周周五 | 平常日子 |
| --- | --- | --- |
| 第 1 节 | 07:30 – 08:15 | 07:30 – 08:15 |
| 课间 | **08:15 – 08:25** | **08:15 – 08:40**（默认缩 5 分钟） |
| 第 2 节 | 08:25 – 09:10 | 08:40 – 09:25 |
| 第 3 节 | 09:20 – 10:05 | 09:35 – 10:20 |
| 第 4 节 | 10:15 – **11:00** | 10:30 – 11:15 |
| 第 5 节 | 12:20 – 13:05 | 14:00 – 14:45 |
| 第 6 节 | 13:15 – 14:00 | 14:55 – 15:40 |
| 第 7 节 | 14:10 – **14:55 放学** | 15:50 – 16:35 |

以学期开始时间 2026-11-22（周日）计：**11.23 那周为返校周**（如 11.23 周一、11.27 周五正常课），**11.30 那周为回家周**（如 12.04 周五 14:55 放学），之后每两周一循环。`scripts/simulate_client.py` 可在本地逐日复现客户端命中结果。

### 换学期 / 放假后怎么调整？

两周循环的锚点在 `build-config.json`：

```json
"week_cycle": {
  "enabled": true,
  "return_week_monday": "2026-11-23",       // 新学期第一个「返校周」的周一
  "home_week_friday_first_class": "12:20",   // 回家周周五下午第一节课
  "home_week_friday_school_over": "14:55"    // 回家周周五放学时间
}
```

放假打乱节奏后，只需把 `return_week_monday` 改成新学期第一个返校周的周一，提交后运行一次 Action；全新机器会自动拿到新的学期开始时间，**已初始化过的机器仍需人工改一次**（设置 → 常规 → 学期开始时间 = 其前一天）。若某个学期回家周周五的作息变了，改后两项即可；构建时若三节 45 分钟课 + 两个 10 分钟课间放不进所给时间段，脚本会直接报错而不是生成错课表。

### 开关 `week_cycle` 是安全的

周五的两张课表（返校周 / 回家周）用**固定 GUID** 生成，脚本总是同时输出这两张：

- `enabled: true` → 两张都启用，分别覆盖第 1、2 周；
- `enabled: false` → 第一张改为「每周生效」，第二张写成 `IsEnabled: false` 的**停用桩**（周五只命中一张）。

因为集控客户端合并档案时**只增不删**，这种“同键覆盖”而不是“换一套新键”的写法，保证反复开关配置时不会在设备上留下两张互相竞争的周五课表。

## 更新课程表

**手动更新（默认已启用）**：仓库 → Actions → 更新高一课程表 → Run workflow。可选项：
- `source_file`：源站数据文件名，默认 `result_actual.json`；
- `force_bump`：内容没变也强制递增版本号，用于让所有客户端立即重新拉取。

**自动定时更新（默认开启，每天 06:30 北京时间）**：`.github/workflows/update.yml` 里已配置

```yaml
on:
  schedule:
    - cron: "30 22 * * *"   # 22:30 UTC = 次日 06:30 北京时间（GitHub cron 用 UTC）
  workflow_dispatch:        # 保留手动触发
```

想改成每周一次就用 `"30 22 * * 0"`；想换时刻按 UTC 换算（每天 06:00 北京时间 → `"0 22 * * *"`）。定时开始时工作流会先 `reset --hard origin/main`，保证用最新代码重建；推送被抢先会自动重试最多 3 次。

**本地手动运行**（不经过 GitHub，适合内网排查）：

```bash
python scripts/build.py --repo <用户名>/<仓库> --branch main
python scripts/validate.py
git add -A && git commit -m "chore: 更新课表" && git push
```

仅需 Python 3.9+，无任何第三方依赖。

### 更新机制说明（为什么客户端会自动换课表）

ClassIsland 客户端只在「清单中某档案的 `Version` 比本地记录更新」时才重新下载该档案。因此 build.py 在每次构建时对内容做哈希比对：

- 内容或地址变化 → 自动 `Version + 1`；内容未变 → 不动版本号（不产生无意义提交，`data/last-update.*` 已被 gitignore）。
- 时间表与课表存在依赖关系（每张课表的节数必须与时间表上课点一致），时间表变化时会联动刷新课表版本。
- **policy.json 的版本号改为按内容哈希自动维护**：改完策略直接提交即可，忘了 +1 也能生效（这是原先手工维护时最容易踩的坑）。
- **退场课表中和**：源数据里消失的课表（年度调整、结构变更等）不会从客户端档案里被删掉，脚本会把它们改写为 `IsEnabled: false` 的停用桩（名称带「（已停用）」前缀、时间表引用指向常规表），确保它们不再参与当日命中。
- **陈旧文件清理**：源数据里不存在的班级，其 `classplans/{id}.json` 与 `presets/ManagementPreset-{id}.json` 会被删除，不再留在仓库里。
- `settings/default-settings.json` 随 `week_cycle` 联动：关闭轮换或把 `emit_default_settings` 改回 `false` 时，文件会被移除、清单字段一并去掉。

## 镜像加速（默认开启）

`raw.githubusercontent.com` 在国内教室网络基本连不上（客户端表现为一直拉取失败、课表不更新），所以构建时会把清单里的所有地址改写成「镜像 + GitHub 原始地址」的拼接形式：

```
https://gh-proxy.com/https://raw.githubusercontent.com/<用户名>/<仓库>/main/classplans/1.json
```

镜像从 <https://git.hiwiki.org/zh-CN/mirrors> 背后的测速数据（`/data/mirror-speeds.json`，每 12 小时更新）里挑：

1. 取状态为「正常」的镜像，按官方测速延迟升序；
2. 并发探测前 10 个（每种镜像同时试 `镜像+raw 路径` 与 `镜像+github.com/raw 路径` 两种形式），必须返回 200 且内容确实是本仓库的清单才算可用；
3. 在可用的镜像里取延迟最低者；
4. **迟滞**：如果上次选中的镜像仍然可用、且延迟不超过最优的 1.5 倍，就保持不变——镜像一换所有地址都变，版本号会全部 +1，客户端要重新下载全部档案，没必要为了几十毫秒来回折腾。

选中的镜像记在 `data/state.json` 的 `mirror` 字段；`validate.py` 会检查清单里的所有地址同根、同前缀，并与该记录一致。

**手动指定或关闭**：

```jsonc
// build-config.json
"mirror": {
  "enabled": true,        // 关掉就是直连 GitHub
  "host": "ghfast.top",   // 固定某家镜像，跳过自动选择
  "list_url": "https://git.hiwiki.org/data/mirror-speeds.json",
  "timeout": 10,          // 单个镜像探测超时（秒）
  "probe_candidates": 10, // 实际探测的镜像数量
  "hysteresis": 1.5       // 迟滞倍数
}
```

也可以临时用命令覆盖：`python scripts/build.py --repo <用户名>/<仓库> --no-mirror`（直连 GitHub）或 `--mirror-host ghfast.top`（固定镜像）。注意这些镜像都是网友公益提供、随时可能失效，换镜像后需要重新跑一次构建才会切过去。

## 集控策略（policy.json）

默认**不限制**任何功能（所有开关为默认值），`policy.json` 的内容由管理员手动维护，**构建脚本永不覆盖它**（只维护清单里的版本号）。常用字段：

| 字段 | 作用 |
| --- | --- |
| `DisableProfileEditing` | 禁止编辑整个档案（课表/时间表/科目） |
| `DisableProfileClassPlanEditing` / `DisableProfileTimeLayoutEditing` / `DisableProfileSubjectsEditing` | 分别仅禁止编辑课表 / 时间表 / 科目 |
| `DisableSettingsEditing` | 禁止编辑应用设置 |
| `DisableDebugMenu` | 禁用调试菜单 |
| `AllowExitManagement` | 是否允许退出集控（默认 true，改为 false 可锁定） |

> 注意顺序：若打算设 `DisableSettingsEditing: true`，请先确保各机器的「学期开始时间」都已核对正确，否则锁定后就改不了。
>
> 改了 `policy.json` 直接提交即可，版本号由脚本按内容哈希自动 +1；`validate.py` 会检查 `state.json` 记录的哈希与文件是否一致，防止“改了但没重新构建”。

## 自定义参数（build-config.json）

| 字段 | 说明 |
| --- | --- |
| `source_url` | 源站 API 地址（含 `file=` 参数），换数据源只改这里 |
| `organization_name` | 集控显示的组织名称，默认「2026级高一年级」，建议改成学校名 |
| `core_version` | 集控清单 CoreVersion，默认 `2.0.0.0`（匹配 ClassIsland 2.x；主版本号必须与客户端一致） |
| `generate_roster` | 是否生成 `docs/班级对照表.md`，默认 `false`。源数据 `teachers` 元数据与当前课表不一致、源站也不展示，开启前请先核对 |
| `exclude_slot_patterns` | 排除的节次正则，默认 `["看班"]`（即不生成晚自习节次）。若要纳入晚自习，改为 `[]` 并重新构建 |
| `emit_default_settings` | 是否生成 `settings/default-settings.json` 并经 `DefaultSettingsSource` 下发「学期开始时间」，默认 `true`。**只对未完成欢迎向导的全新实例生效** |
| `morning_big_break_shrink_minutes` | 平常日子大课间缩短的分钟数，默认 `5`（08:15–08:45 → 08:15–08:40，上午其余节次前移 5 分钟）；`0` 保持源站原样。改完必须重建，`validate.py` 会核对大课间是否等于「源站课间 − 该值」 |
| `mirror` | 镜像加速配置，见上文「镜像加速」；`enabled: false` 等价于 `--no-mirror`，`host` 可固定某家镜像 |
| `week_cycle.home_week_friday_morning_break` | 回家周周五上午缩短的课间，格式 `"08:15-08:25"`；留空则上午与平常一致。改完必须重新构建（`validate.py` 会检查时间表里真有这个课间） |
| `week_cycle` | 两周轮换（单双周）配置，见上文；`enabled: false` 可退回无轮换的单周五课表（两张周五课表折叠为“每周一张 + 一张停用桩”） |

> 纳入晚自习后，时间表会自动加入晚间时间点，课表节次由源数据的「看班」行填充。

## 数据口径

- 课表内容与「科目 + 教师」对应关系取自源站页面实际展示的每节次数据（`schedule` + `slot_teachers`），以每节次标注为准。
- 源数据中的 `teachers` 元数据（班主任/选科组合/汇总任课教师）与当前课表大面积对不上（例如班主任未在本班任何节次出现），源站页面也不展示这些字段，因此**不用于生成档案**；`docs/班级对照表.md` 默认不生成。
- 同一科目不同教师视为不同科目条目（ClassIsland 支持按科目显示教师）。
- 时间表里所有课间（`TimeType = 1`）名称统一为「课间休息」，午休分割线（`TimeType = 2`）名称为「午休」，客户端时间轴上显示的就是这两个文案。
- 科目表**按班级拆分**（清单里 `SubjectsSource` 用 `subjects/{id}.json` 模板）：每台班级机只会拿到本班用到的「科目+教师」（7–11 条），而不是全年级 78 条；全年级仍是 78 个条目，只是按班分发。
- 高一10班第 4 节为「自习」（王宁），已按正常科目生成。
- 回家周周五的三节下午课沿用源数据当周周五第 5~7 节的科目与教师，仅时间提前；上午与常规日一致。
- 如需查看班级元数据，可将 `build-config.json` 的 `generate_roster` 设为 `true` 后重新构建，生成 `docs/班级对照表.md`（内容仅作参考，使用前请核对）。
- **隐私提示**：教师姓名仍随仓库公开托管在 GitHub 上（按班拆分成 20 个文件只是缩小了**每台设备**能看到的范围，不改变公开托管的事实），请注意确认可公开范围。

## 已知边界（与 ClassIsland 2.x 能力的差距）

这些是协议/客户端侧的限制，**不是本仓库能改的**，部署时请知悉：

1. **屏幕组件（ComponentsSource）没有下发**：集控支持统一下发组件布局，但本仓库未使用，因此“屏幕上显示什么、怎么排”仍需逐台配置。20 块屏想完全一致，要另外推送组件配置。
2. **调休无法下发**：集控合并管理档案时只合并 `ClassPlans` / `ClassPlanGroups`，**不合并 `OrderedSchedules`（按日期指定的课表）**。所以“周六调休上课”“放假换休”即便写进档案也不会生效，只能在设备上用「调课/临时课表」手工处理。
3. **合并只增不删（课表已中和，科目无法清理）**：客户端合并时不会删除“上次有、这次没有”的条目。课表已用停用桩中和；但**科目没有停用开关**，换老师时旧的“科目+教师”条目会留在**本班**设备上（因为科目已按班分发，不会再扩散到别的班），长期仍会积累。真要清理，在该设备「退出管理 → 重新加入」会清空管理档案缓存。
   顺带一提：从旧的全局科目表升级过来的设备，先前拉取过的全年级条目会留在本地（合并不删除），同样需要「退出管理 → 重新加入」后才会只剩本班条目。
4. **设置源只对全新机器生效**：`DefaultSettingsSource` 只在欢迎向导完成前应用，且整体替换设置，无法用它给已初始化的机器改单项设置。
5. **源站证书不匹配时自动降级 TLS 校验**（极端情况会跳过证书校验）以兼容源站证书域名不匹配，这意味着链路内容有被替换的可能；请用 `data/snapshot/` 的提交 diff 做人工复核。
6. 周六周日没有课表，客户端当日会显示无课/空白，这是预期行为。
7. **镜像依赖第三方公益服务**：`git.hiwiki.org` 那份清单和镜像本身都由网友提供，随时可能超时、限速或下线。镜像挂了的表现是所有客户端都拉不到档案（清单里只有一个地址，没有备用）。真出问题时把 `mirror.enabled` 改成 `false`（或 `--no-mirror`）重新构建即可直连 GitHub；长期方案是 `--base-url` 指向自建/内网的静态托管。
8. **镜像探测在构建机上跑**：脚本测的是 GitHub Actions/你本机的网络，不是教室的真实网络。因此排序优先用清单里（国内众测）的延迟，本机探测只用来确认“这家镜像确实能返回我们的文件”。

## 常见问题

**在「加入管理」里选了 `manifest.json`，点连接报 `Invalid URI: The URI is empty`？**
选错文件了。对话框要的是客户端配置（含 `ManifestUrlTemplate`），不是集控清单。请选 `presets/ManagementPreset-<班级号>.json`，或按上文“方式 B”自建一份。

**预设文件放好了，但加入时不会自动加载？**
放到数据目录（便携版 `<包根>/data/`、安装版 `%AppData%\ClassIsland\Data\`），文件名 `ManagementPreset.json`；不是 exe 同级目录。

**客户端没有更新到新课表？**
按顺序检查：① Action 是否真的检测到变化（运行摘要中各档案状态）；② 客户端重启或集控重载后才会拉取（清单/档案有本地缓存）；③ 若确需强制刷新，用 `force_bump` 触发一次。

**课表一直拉不下来 / 提示下载失败？**
先确认镜像可用：把清单里的地址复制到浏览器（例如 `https://gh-proxy.com/https://raw.githubusercontent.com/<用户名>/<仓库>/main/manifest.json`），能返回 JSON 就没问题；打不开说明这家镜像挂了，把 `build-config.json` 的 `mirror.host` 改成另一家（如 `ghfast.top`、`fastgit.cc`）或直接 `enabled: false`，重新触发一次 Action。

**校园网访问 raw.githubusercontent.com 很慢或不通？**
默认已开镜像加速（见上文「镜像加速」），通常不需要额外处理；若想彻底不依赖公益镜像，用 `--base-url` 改到自建静态托管（内网 Web 服务器、对象存储等），地址变化会让脚本自动 +1 版本号。

**设备上只看到本班的老师，是不是档案生成漏了？**
不是。科目表按班分发（`subjects/{id}.json`），每台设备本来就只拿到本班任课教师；某个班的课表若引用了本班科目表里没有的条目，`validate.py` 会直接报错。

**加入时提示「核心版本不兼容」？**
ClassIsland 2.x 客户端要求清单 `CoreVersion` 主版本为 2。1.x 客户端格式不同（时间点用 `StartSecond` 而非 `StartTime`），不兼容。

**周五没有按返校周/回家周切换？**
依次检查：① 设置 → 常规 → **学期开始时间** 是否为构建摘要里给出的日期（当前 2026-11-22，周日）；② 是否有人在设备上动过“调整多周轮换偏移”，那会把轮换整体平移；③ 课表编辑器里应能看到每班的「周五（返校周）」标为「每 2 周的第 1 周」、「周五（回家周）」标为「第 2 周」；④ 若刚切换过 `week_cycle` 开关，课表编号固定、内容按覆盖方式更新，不会残留旧表。

**某天课表临时调换？**
本仓库按周分发固定周课表。临时调课建议直接在班级机的 ClassIsland 上用「调课/临时课表」功能处理（若 policy 未禁止编辑），或修改源站数据后重新运行 Action。

**源数据结构变了导致构建失败？**
构建脚本会明确报出缺失字段。先核对源站 API 是否改版（浏览器直接打开 `build-config.json` 里的 `source_url` 看返回 JSON），再调整 `scripts/build.py` 的解析部分。

## 许可

本仓库的生成脚本以 MIT 许可发布（见 `LICENSE`）。ClassIsland 为 GPL v3.0 / LGPL v3.0 开源软件，感谢 [ClassIsland 项目](https://github.com/ClassIsland/ClassIsland) 与其社区。
