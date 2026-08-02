# 官方能力与数据边界

## Garmin Connect Developer Program

- [Health API](https://developer.garmin.com/gc-developer-program/health-api/)：官方列出步数、强度分钟、睡眠、卡路里、心率、压力、Pulse Ox、Body Battery、体成分、呼吸和血压；说明可提供全天健康数据、详细压力/血氧和 epoch 汇总，并可有活动中的秒级心率。
- [Activity API](https://developer.garmin.com/gc-developer-program/activity-api/)：官方提供活动详细数据和 FIT、GPX、TCX 文件，覆盖跑步、骑行、游泳、瑜伽、力量等活动。
- [Developer Program FAQ](https://developer.garmin.com/gc-developer-program/program-faq/)：正式 API 面向企业；OAuth 2.0；个别指标可能需要额外商业许可或设备采购。
- [Health SDK overview](https://developer.garmin.com/health-sdk/overview/)：实时心率、加速度计、压力等传感器流及 Enhanced Beat-To-Beat Intervals 属于 Garmin Health SDK，面向企业合作伙伴，不能把 Connect 云端导出的睡眠 HRV 或活动 FIT 直接等同为实时逐搏流。

## FIT

- [FIT Activity File](https://developer.garmin.com/fit/file-types/activity/)：`record` 消息保存活动期间逐时刻位置、速度、距离、心率、功率等；`hrv` 消息保存 RR 间期。
- [Decoding Activity Files](https://developer.garmin.com/fit/cookbook/decoding-activity-files/)：HRV 消息与 record/event 交错、但自身无时间戳，应根据连续 RR 间期和相邻带时间戳的消息对齐。

## 本 skill 的访问方式

此 skill 读取当前 Garmin Connect 账户已同步的数据，底层库为社区维护的 `python-garminconnect`，并非 Garmin Connect Developer Program 的企业 OAuth 2.0 API。它不承诺所有官方指标均能由个人账户端点返回，也不尝试绕过 Garmin 的企业授权、设备能力、隐私设置或访问限制。接口没有数据时，保留空结果或错误信息。
