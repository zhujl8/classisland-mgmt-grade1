# ClassIsland 高一课程表静态集控

面向全高一年级（1–20 班）的 [ClassIsland](https://github.com/ClassIsland/ClassIsland) **静态集控仓库**：课表数据自动从源站抓取、转换为 ClassIsland 档案格式，托管在 GitHub 上，班级多媒体屏幕上的 ClassIsland 通过集控功能按班级自动拉取对应课表。

内置 GitHub Actions：**手动点一下（或开启定时后自动）** 即可完成「抓取源数据 → 生成档案 → 递增版本号 → 提交推送」全流程，无需手工编辑任何 JSON。

```
源站课表页面 (duanjr.site/tools/class_schedule)
        │  API: /tools/class_schedule/api/schedule?file=result_actual.json
        ▼
GitHub Action（本仓库 scripts/build.py）
        │  转换为 ClassIsland 档案 + 版本管理
        ▼
manifest.json + subjects.json + timelayouts.json + classplans/{id}.json
        │  raw.githubusercontent.com 静态托管
        ▼
班级机 ClassIsland（加入集控，ID = 班级号 1–20）
```

> 集控规范参考官方文档：[docs.classisland.tech/management](https://docs.classisland.tech/management/)（本仓库的 manifest、档案、客户端预设均按其格式生成）。

## 仓库结构

```
├── manifest.json                  # 集控清单（Action 自动维护版本号）
├── policy.json                    # 集控策略（管理员手动维护，默认不限制）
├── subjects.json                  # 科目总表（自动生成，78 个「科目+教师」条目）
├── timelayouts.json               # 统一时间表（自动生成，7 节课 + 课间 + 午休分割线）
├── classplans/
│   ├── 1.json … 20.json           # 每班课表档案（自动生成，每班含周一至周五 5 张日课表）
├── presets/
│   └── ManagementPreset-1..20.json # 每班客户端预设文件（自动生成）
├── scripts/
│   ├── build.py                   # 抓取源数据 → 生成全部档案（零第三方依赖）
│   └── validate.py                # 结构校验（CI 中自动执行）
├── docs/班级对照表.md              # 集控 ID ↔ 班级/班主任/选科组合（自动生成）
├── build-config.json              # 构建参数（组织名、源地址、排除节次等）
├── data/
│   ├── state.json                 # 版本状态（内容哈希，自动维护）
│   └── snapshot/                  # 源数据快照（便于人工审查每次变更）
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

### 第 3 步：班级机加入集控

**方式 A：预设文件（免手输，推荐）**

1. 从仓库下载对应班级的 `presets/ManagementPreset-<班级号>.json`；
2. 复制到班级机的 **ClassIsland 安装目录**（与 ClassIsland.exe 同一文件夹），并**重命名为 `ManagementPreset.json`**；
3. 启动 ClassIsland：首次使用时欢迎向导中点「加入集控」，已初始化过的设备在应用设置中进入集控页面加入；
4. 确认界面上 ID 已自动填为班级号，点击「连接」→「加入」即可。

**方式 B：手动填写**

在加入集控界面填入：
- 集控清单 URL：`https://raw.githubusercontent.com/<你的用户名>/<仓库名>/<分支>/manifest.json`
- ID：班级号（`1`–`20`，对照表见 [docs/班级对照表.md](docs/班级对照表.md)）

> 集控 ID 即档案 URL 中的 `{id}`：例如 ID 为 `5` 的设备会拉取 `classplans/5.json`（高一5班）。因此每台设备必须使用唯一的班级号，否则会拿到别人的课表。

加入成功后，应用设置右上角会出现「由贵单位管理」徽章，主界面即按当日课表显示。周一至周五每天自动切换到对应的日课表（`TimeRule.WeekDay` 规则）。

## 更新课程表

**手动更新（默认已启用）**：仓库 → Actions → 更新高一课程表 → Run workflow。可选项：
- `source_file`：源站数据文件名，默认 `result_actual.json`；
- `force_bump`：内容没变也强制递增版本号，用于让所有客户端立即重新拉取。

**自动定时更新（可选，默认关闭）**：编辑 `.github/workflows/update.yml`，按文件内注释取消 `on.schedule` 段的注释并提交即可，例如每周日晚自动同步一次：

```yaml
on:
  schedule:
    - cron: "30 22 * * 0"   # 22:30 UTC = 北京时间周一 06:30（GitHub cron 使用 UTC）
  workflow_dispatch: { }    # 保留手动触发；inputs 配置一并保留
```

**本地手动运行**（不经过 GitHub，适合内网排查）：

```bash
python scripts/build.py --repo <用户名>/<仓库> --branch main
python scripts/validate.py
git add -A && git commit -m "chore: 更新课表" && git push
```

仅需 Python 3.9+，无任何第三方依赖。脚本已内置源站证书域名不匹配（证书签发给 `www.duanjr.site`）的自动降级处理。

### 更新机制说明（为什么客户端会自动换课表）

ClassIsland 客户端只在「清单中某档案的 `Version` 比本地记录更新」时才重新下载该档案。因此 build.py 在每次构建时对内容做哈希比对：内容或地址变化 → 自动 `Version + 1`；内容未变 → 不动版本号（不产生无意义提交）。时间表与课表存在依赖关系（每张课表的节数必须与时间表上课点一致），时间表变化时会联动刷新课表版本。

## 集控策略（policy.json）

默认**不限制**任何功能（所有开关为默认值），`policy.json` 由管理员手动维护，**构建脚本永不覆盖它**。常用字段：

| 字段 | 作用 |
| --- | --- |
| `DisableProfileEditing` | 禁止编辑整个档案（课表/时间表/科目） |
| `DisableProfileClassPlanEditing` / `TimeLayoutEditing` / `SubjectsEditing` | 分别仅禁止编辑课表 / 时间表 / 科目 |
| `DisableSettingsEditing` | 禁止编辑应用设置 |
| `DisableDebugMenu` | 禁用调试菜单 |
| `AllowExitManagement` | 是否允许退出集控（默认 true，改为 false 可锁定） |

**修改后必须**到 `manifest.json` 把 `PolicySource` 的 `Version` 加 1 并提交，客户端才会拉取新策略（这是唯一需要手动动版本号的地方）。

## 自定义参数（build-config.json）

| 字段 | 说明 |
| --- | --- |
| `source_url` | 源站 API 地址（含 `file=` 参数），换数据源只改这里 |
| `organization_name` | 集控显示的组织名称，默认「2026级高一年级」，建议改成学校名 |
| `core_version` | 集控清单 CoreVersion，默认 `2.0.0.0`（匹配 ClassIsland 2.x；主版本号必须与客户端一致） |
| `exclude_slot_patterns` | 排除的节次正则，默认 `["看班"]`（即不生成晚自习节次）。若要纳入晚自习，改为 `[]` 并重新构建 |

> 纳入晚自习后，时间表会自动加入晚间时间点，课表节次由源数据的「看班」行填充。

## 数据口径

- 课表内容与「科目 + 教师」对应关系取自源站页面实际展示的每节次数据（`schedule` + `slot_teachers`），与源站元数据中的教师汇总表（`teachers`）存在少量不一致（代课/调课所致），以每节次标注为准。
- 同一科目不同教师视为不同科目条目（ClassIsland 支持按科目显示教师）。
- 高一10班第 4 节为「自习」（王宁），已按正常科目生成。
- 班主任、选科组合等班级元数据见 [docs/班级对照表.md](docs/班级对照表.md)（自动生成，仅作部署参考）。
- **隐私提示**：教师姓名等信息公开托管在 GitHub 上，请注意确认可公开范围。

## 常见问题

**客户端没有更新到新课表？**
按顺序检查：① Action 是否真的检测到变化（运行摘要中各档案状态）；② 客户端重启或集控重载后才会拉取（清单/档案有本地缓存）；③ 若确需强制刷新，用 `force_bump` 触发一次。

**校园网访问 raw.githubusercontent.com 很慢或不通？**
可改用其它静态托管：把 `build-config.json` 之外的问题交给 `--base-url` 参数（或 Action 中追加该参数），例如自建镜像或内网静态服务器；也可以将仓库同步到 Gitee 等平台后改写 manifest 中的地址（修改后注意版本号 +1）。

**加入集控时提示「核心版本不兼容」？**
ClassIsland 2.x 客户端要求 manifest 的 `CoreVersion` 主版本为 2。1.x 客户端请自行评估格式差异（2.x 才强制该校验）。

**某天课表临时调换？**
本仓库按周分发固定周课表。临时调课建议直接在班级机的 ClassIsland 上用「调课/临时课表」功能处理（若 policy 未禁止编辑），或修改源站数据后重新运行 Action。

**源数据结构变了导致构建失败？**
构建脚本会明确报出缺失字段。先核对源站 API 是否改版（浏览器直接打开 `build-config.json` 里的 `source_url` 看返回 JSON），再调整 `scripts/build.py` 的解析部分。

## 许可

本仓库的生成脚本以 MIT 许可发布。ClassIsland 为 GPL v3.0 / LGPL v3.0 开源软件，感谢 [ClassIsland 项目](https://github.com/ClassIsland/ClassIsland) 与其社区。
