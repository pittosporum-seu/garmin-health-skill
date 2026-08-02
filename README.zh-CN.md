# Garmin 健康数据 Skill

[English](README.md)

这是一个以隐私为先、只读的 Codex/Hermes Skill，用于导出个人 Garmin Connect 健康与运动数据：全天心率时序、睡眠 HRV、睡眠阶段、血氧、呼吸、压力、身体电量、活动流，以及原始 FIT 记录。

它面向数据所有者分析自己的账号。数据是否存在取决于设备型号、固件、地区、订阅及实际记录的指标。

## 特性

- 覆盖广泛的日级数据：统计、心率、HRV、睡眠、压力、身体电量及事件、步数、血氧、呼吸、强度分钟、准备度、楼层、静息心率、体能年龄、生活方式和营养日志/设置。
- 保留 Garmin 原始字段的标准化时序，包括全天心率与睡眠内 HRV。
- 导出活动详情流、可选路线 polyline，并解码原始 FIT 中的记录和 RR 间期（`hrv`）消息。
- 历史区间导出在每个日期后安全保存检查点；中断后可用 `--resume` 续传，不会重复请求已完成的日期。
- 默认安全：健康数据必须写入 `--output PATH` 的所有者专用文件；只有显式添加 `--stdout` 才会输出到终端。
- 某一设备不支持某指标时，逐端点返回结构化错误，不会使整份多源报告失败。

## 安装

直接克隆到 skill 目录并创建独立环境：

```bash
git clone https://github.com/pittosporum-seu/garmin-health-skill.git \
  ~/.hermes/skills/garmin-health
cd ~/.hermes/skills/garmin-health
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

```bash
PY=~/.hermes/skills/garmin-health/.venv/bin/python
CLI=~/.hermes/skills/garmin-health/garmin_health_cli.py
$PY $CLI status
```

默认 token 路径为 `~/.hermes/skills/garmin-health/tokens`。若要隔离账号，请将 `GARMIN_TOKENSTORE` 设为你控制的目录。

token 过期时，只能通过环境变量提供登录凭据；不要把密码写入命令行参数或文件：

```bash
GARMIN_EMAIL='账号' GARMIN_PASSWORD='密码' $PY $CLI login
```

`login` 会以仅所有者权限创建 token 目录；Skill 从不打印、导出或提交 token。

## 快速开始

所有会返回个人数据的命令默认要求安全输出文件。仅在确认终端私密、且确实需要显示时才加 `--stdout`。

```bash
# 简洁的每日健康报告
$PY $CLI overview 2026-08-01 --output ~/garmin/overview-2026-08-01.json

# 时序：全天心率与睡眠 HRV
$PY $CLI series heart-rate 2026-08-01 --output ~/garmin/hr-2026-08-01.json
$PY $CLI series sleep-hrv 2026-08-01 --output ~/garmin/sleep-hrv-2026-08-01.json

# 摘要未包含字段时，读取精确的原始端点
$PY $CLI raw body-battery-events 2026-08-01 --output ~/garmin/body-battery-events.json
```

使用 `--force` 才能覆盖已有导出。写入的 JSON 会原子保存，在 POSIX 系统中权限为 `0600`。

## 历史导出

区间导出的默认端点为 `stats`、`hrv`、`sleep`、`training-readiness`。使用重复的 `--kind` 可精确选择端点；使用 `--all` 可导出全部日级端点。

```bash
# 专注且可续传的月度导出
$PY $CLI export-range 2026-07-01 2026-07-31 \
  --kind stats --kind hrv --kind sleep \
  --output ~/garmin/july-core.json

# 中断后续传，日期范围必须完全相同；端点列表由已有文件决定
$PY $CLI export-range 2026-07-01 2026-07-31 \
  --resume --output ~/garmin/july-core.json

# 全量日级导出（文件可能很大，端点可用性因账号而异）
$PY $CLI export-range 2026-07-01 2026-07-31 \
  --all --output ~/garmin/july-all.json
```

每个完整日期会立即写入检查点。不可用或失败的端点保留为：

```json
{
  "available": false,
  "error": { "type": "ExceptionName", "message": "…" }
}
```

## 离线分析

`analyze` 只读取已有的 `export-range` JSON；不会认证，也不会发起 Garmin 网络请求，因此分析可由输入文件完整复现。

```bash
$PY $CLI analyze data-quality ~/garmin/july-core.json \
  --output ~/garmin/july-data-quality.json
```

`data-quality` 是解读趋势前的必经步骤。它报告请求日期覆盖率、缺失日期、端点错误、各端点可用性，以及睡眠时长、睡眠 HRV、静息心率、压力和训练准备度等已定义摘要字段的样本量。它不把缺失当作零，不评估设备准确性，也不作医疗结论。

`recovery` 将最新有效日与此前最多 28 个个人观测比较，覆盖 Garmin 睡眠 HRV、静息心率、睡眠时长和 Garmin 训练准备度。单一指标至少需要 7 个先前观测，结果才会给出中位数、中位数绝对偏差（MAD）和“最新值减中位数”；否则只报告数据不足的限制。

```bash
$PY $CLI analyze recovery ~/garmin/july-core.json \
  --output ~/garmin/july-recovery.json
```

它刻意不计算替代性的“恢复分数”，不诊断疾病，也不声称某个变化的原因。

`sleep` 在数据可用时报告 Garmin 提供的睡眠时长、深/浅/REM/清醒组成、睡眠心率、睡眠压力和睡眠 HRV。最新睡眠分期占比只以实际记录到的深/浅/REM 时长为分母；每个指标同样遵循至少 7 个先前观测才进行基线比较的规则。

```bash
$PY $CLI analyze sleep ~/garmin/july-core.json \
  --output ~/garmin/july-sleep.json
```

睡眠开始/结束时间会保留 Garmin 原字段；时区语义不明确时不擅自转换。这是描述性睡眠趋势分析，不是睡眠障碍筛查。

## 活动与 FIT 数据

```bash
$PY $CLI activities --limit 20 --output ~/garmin/activities.json
$PY $CLI activity 623002723 --output ~/garmin/activity.json
$PY $CLI activity-stream 623002723 --include-route \
  --output ~/garmin/activity-stream.json
$PY $CLI fit-summary 623002723 --output ~/garmin/fit-summary.json
$PY $CLI fit-stream 623002723 --message record \
  --output ~/garmin/fit-records.json
$PY $CLI fit-stream 623002723 --message hrv \
  --output ~/garmin/rr-intervals.json
```

先运行 `fit-summary`，确认该活动实际包含哪些消息和字段。其中 `unrecognized_field_definitions` 会列出解码器无法命名的 profile 字段。`fit-stream` 对这些字段仍保留数值、原始值、字段编号、基础类型、单位和 developer-field 标记，避免静默丢失数据。若 FIT 内存在，`fit-stream --message hrv` 会导出 RR 间期。FIT 规范中的 `hrv` 消息本身没有绝对时间戳：必须保持消息顺序，仅可与相邻 `record`/`event` 对齐；不得伪造逐搏绝对时间。

## 其他数据

```bash
$PY $CLI performance 2026-08-01 --output ~/garmin/performance.json
$PY $CLI profile --output ~/garmin/profile.json
$PY $CLI body body-composition 2026-07-01 2026-08-01 \
  --output ~/garmin/body-composition.json
$PY $CLI body hydration 2026-08-01 --output ~/garmin/hydration.json

# 仅当数据所有者明确要求时使用
$PY $CLI reproductive menstrual-calendar 2026-07-01 2026-07-31 \
  --output ~/garmin/menstrual-calendar.json
```

执行 `raw --help` 可查看全部日级端点。包括 `all`、`stats`、`heart-rate`、`hrv`、`sleep`、`stress`、`all-day-stress`、`body-battery`、`body-battery-events`、`steps`、`spo2`、`respiration`、`intensity`、`training-readiness`、`morning-readiness`、`events`、`floors`、`rhr`、`max-metrics`、`fitness-age`、`lifestyle` 和营养数据。

## 数据语义与边界

- Garmin Connect 的全天心率时间线通常以两分钟均值呈现。Skill 保留原始空档，不插值。
- Garmin HRV Status 是睡眠期派生、以毫秒为单位的 Garmin 指标，不等同于带逐搏时间戳的 RR 时序。Garmin 说明其使用 RMSSD 和个人基线，见 [HRV Status 说明](https://www.garmin.com/en-US/garmin-technology/health-science/hrv-status/)。
- 数值、字段和采样率取决于设备与账号。端点不可用并不表示某一生理指标为零。
- Garmin 健康指标不用于医疗诊断、治疗、治愈或预防疾病。
- 本项目通过社区 `garminconnect` 客户端读取个人 Garmin Connect 账号；它不是 Garmin 官方 SDK，也不能替代独立的商业项目 [Garmin Health API](https://developer.garmin.com/gc-developer-program/health-api/)。
- FIT 记录遵循 Garmin [FIT 协议](https://developer.garmin.com/fit/protocol/)，字段是否存在取决于源设备与活动。

## 开发

```bash
$PY -m pip install -r requirements-dev.txt
$PY -m pytest -q
$PY garmin_health_cli.py --help
```

## 许可证

[MIT](LICENSE)
