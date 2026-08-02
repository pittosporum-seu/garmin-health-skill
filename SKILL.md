---
name: garmin-health
description: 读取本人佳明（Garmin Connect 中国区）账户中的健康与运动数据，包括全天心率时序、睡眠 HRV、血氧、呼吸、压力、身体电量、步数、训练准备度、活动逐秒流和原始 FIT 中的 RR 间期。用户询问自己的佳明健康/运动数据、心率曲线、HRV、睡眠、血氧、呼吸、训练负荷或需要导出、分析佳明数据时使用此 skill。
---

# Garmin Health Data

本 skill 通过只读 Garmin Connect 接口读取用户自己的同步数据。设备、佩戴方式、功能开关、固件和账号地区都会影响某项数据是否存在。绝不读取、输出或提交 token 内容；不要把密码、token、完整健康数据或路线粘贴到对话中。

## 初始化与认证

```bash
python3.12 -m venv ~/.hermes/skills/garmin-health/.venv
~/.hermes/skills/garmin-health/.venv/bin/pip install -r ~/.hermes/skills/garmin-health/requirements.txt

PY=~/.hermes/skills/garmin-health/.venv/bin/python
CLI=~/.hermes/skills/garmin-health/garmin_health_cli.py
$PY $CLI status
```

默认 token 目录是 `~/.hermes/skills/garmin-health/tokens`。token 失效时，只从环境变量读取凭据，避免将密码写入 shell 历史：

```bash
GARMIN_EMAIL='账号' GARMIN_PASSWORD='密码' $PY $CLI login
```

`login` 将 token 目录设为 `0700`、其中的文件设为 `0600`。需要隔离账号时，设置 `GARMIN_TOKENSTORE`。

## 先确认用户意图和输出位置

健康、活动、设备资料和生殖健康数据默认不会直接打印到终端。每个读取数据的命令必须：

- 使用 `--output PATH` 写入原子保存、权限为 `0600` 的 JSON；或
- 仅在用户明确希望在私密终端查看时添加 `--stdout`。

不得用 `--force` 覆盖用户已有导出，除非用户明确要求。遇到不支持的端点，输出中的该端点会为：

```json
{"available": false, "error": {"type": "ExceptionName", "message": "…"}}
```

这表示数据或端点不可用，不表示生理值为零。

## 日常健康数据与时序

先请求概览，再根据用户问题选择原始端点或时序：

```bash
$PY $CLI overview 2026-08-01 --output ~/garmin/overview.json
$PY $CLI series heart-rate 2026-08-01 --output ~/garmin/heart-rate.json
$PY $CLI series sleep-hrv 2026-08-01 --output ~/garmin/sleep-hrv.json
$PY $CLI raw body-battery-events 2026-08-01 --output ~/garmin/body-battery-events.json
```

`raw` 的日级端点包括：`all`、`stats`、`heart-rate`、`hrv`、`sleep`、`stress`、`all-day-stress`、`body-battery`、`body-battery-events`、`steps`、`spo2`、`respiration`、`intensity`、`training-readiness`、`morning-readiness`、`events`、`floors`、`rhr`、`max-metrics`、`fitness-age`、`lifestyle`、`nutrition-food-log`、`nutrition-meals`、`nutrition-settings`。

`series` 支持 `heart-rate`、`hrv`、`stress`、`body-battery`、`respiration`、`spo2`、`steps`，以及 `sleep-levels`、`sleep-movement`、`sleep-heart-rate`、`sleep-hrv`、`sleep-stress`、`sleep-body-battery`、`sleep-spo2`、`sleep-respiration`、`breathing-disruption`。使用 `--timezone +08:00` 控制显示时区。

语义边界必须随结果说明：

- 全天心率是 Garmin Connect 时间线数据，通常按两分钟平均呈现；保留原始缺口，不插值。
- Garmin HRV Status 是睡眠期衍生的毫秒指标（Garmin 使用 RMSSD 和个人基线），不是带逐搏绝对时间戳的 RR 时序。
- `series` 中的 timestamp、字段和空值均以 Garmin 原始响应为准，不能补造读数或时间。

## 历史范围导出

当用户要看趋势、周期或多日数据，优先用带检查点的 `export-range`，不要循环在终端打印每天结果。

```bash
# 默认：stats、hrv、sleep、training-readiness
$PY $CLI export-range 2026-07-01 2026-07-31 --output ~/garmin/july-core.json

# 精确端点；--kind 可以重复
$PY $CLI export-range 2026-07-01 2026-07-31 \
  --kind stats --kind hrv --kind sleep --output ~/garmin/july.json

# 所有日级端点，输出可能很大
$PY $CLI export-range 2026-07-01 2026-07-31 --all --output ~/garmin/july-all.json

# 在相同日期范围下续传中断的导出
$PY $CLI export-range 2026-07-01 2026-07-31 --resume --output ~/garmin/july.json
```

每一个完成日期都会立即安全写入。`--resume` 只能用于同一日期范围的该导出文件，端点列表也由该文件决定，且不能与 `--force`、`--kind` 或 `--all` 并用。对大量日期请求时保留默认 `--delay 0.25`，不要通过并发绕过服务端限制。

导出后先运行离线数据质量检查，再解释任何趋势。该命令仅读本地文件，不会认证或请求 Garmin：

```bash
$PY $CLI analyze data-quality ~/garmin/july-core.json --output ~/garmin/july-data-quality.json
```

检查请求日期覆盖、端点错误、缺失日期及各摘要字段的有效样本数。数据缺失、端点错误或采样不足时，只说明限制，不把它们解释为生理数值或健康结论。

## 活动、逐秒流和 FIT

```bash
$PY $CLI activities --limit 20 --output ~/garmin/activities.json
$PY $CLI activity 623002723 --output ~/garmin/activity.json
$PY $CLI activity-stream 623002723 --include-route --output ~/garmin/stream.json
$PY $CLI fit-summary 623002723 --output ~/garmin/fit-summary.json
$PY $CLI fit-stream 623002723 --message record --output ~/garmin/fit-records.json
$PY $CLI fit-stream 623002723 --message hrv --output ~/garmin/rr.json
```

- 活动流包含该活动实际记录的图表指标，可能有心率、定位、配速、功率、步频、海拔等。结果里的 `truncated` 指示服务端是否仍截断；不要假定请求的采样数一定可得。
- 先用 `fit-summary` 确认可用消息和字段，再导出 `fit-stream`。升级后的 `fitdecode` 会尽可能解释字段；`unrecognized_field_definitions` 报告未命名字段，`fit-stream` 同时保留其数值、原始值、字段编号、基础类型、单位和 developer-field 标记，不能臆测其含义。
- FIT `hrv` 消息提供 RR 间期，但该消息自身没有绝对时间戳。必须保留消息顺序、最多与相邻 `record`/`event` 对齐，不得编造逐搏时间。
- 路线和精细活动流高度敏感，仅在用户明确请求时使用 `--include-route`，并始终保存到私有输出文件。

## 训练、身体、资料与生殖健康

```bash
$PY $CLI performance 2026-08-01 --output ~/garmin/performance.json
$PY $CLI profile --output ~/garmin/profile.json
$PY $CLI body body-composition 2026-07-01 2026-08-01 --output ~/garmin/body.json
$PY $CLI body hydration 2026-08-01 --output ~/garmin/hydration.json
```

`performance` 汇总训练状态、最大指标、准备度、耐力/爬坡分数、乳酸阈值、比赛预测与跑步耐受度；`profile` 包含设备、设置、分区、FTP 和个人纪录；`body` 支持 `body-composition`、`weigh-ins`、`blood-pressure` 和单日 `hydration`。

仅在用户明确提出后才能查询特别敏感的生殖健康数据：

```bash
$PY $CLI reproductive menstrual-calendar 2026-07-01 2026-07-31 --output ~/garmin/menstrual.json
$PY $CLI reproductive pregnancy-summary --output ~/garmin/pregnancy.json
```

## 解释与引用

健康数据仅用于个人记录和趋势分析，不做医疗诊断、治疗、治愈或预防疾病的结论。解释 HRV、心率、血氧或呼吸异常时，说明数据质量限制，建议有症状或担忧时联系专业医疗人员。

官方能力、数据边界和引用链接见 [references/official-capabilities.md](references/official-capabilities.md)。Garmin Health API 是面向合作伙伴的独立商业项目；本 skill 使用社区 `garminconnect` 客户端读取个人 Garmin Connect 账户。
