# Danmu Catcher MVP

基于 `DouyinDanmakuCatcher` 后端思路做的“弹幕捕手 + 抽奖打印”MVP。第一版重点是跑通业务闭环：直播间配置、弹幕流、规则匹配、抽取、模拟打印日志。

## 运行

```powershell
python backend/app.py
```

打开：

```text
http://127.0.0.1:8765
```

## 当前能力

- 原生 Web 控制台，视觉贴近参考截图
- 添加直播间，保存本地状态
- 开启/停止自动打印
- SSE 实时弹幕流，当前为模拟采集器
- 数字范围、关键词、限量、防重复等基础规则
- 弹幕列表筛选、导出
- 下一轮抽取并生成模拟打印任务
- 打印任务写入 `data/print-jobs.jsonl`

## 后续接真实抖音采集

`backend/app.py` 里的 `collector_loop()` 当前负责生成模拟弹幕。后续可以把它替换成对原项目 `DanmuFetcher` 的适配：

1. 用 `DanmuFetcher.start(on_chat)` 接收真实 `ChatMessage/GiftMessage`
2. 将消息转换为本项目的弹幕记录结构
3. 调用 `publish("danmu", item)` 推送到前端
4. 同步写入 `data/state.json` 或数据库

原项目里 protobuf 的职责仍然是解析抖音 WebSocket 二进制消息。
