# 🎯 弹幕捕手 Pro (Danmu Catcher Pro)

> 专为直播带货（珠宝、文玩、水晶等需要公屏扣数/抢单的直播间）打造的桌面级直播中控工作台。

基于抖音实时弹幕捕获，提供自动识别、智能防刷、限量锁单、以及秒级静默标签打印的全自动化解决方案。

## ✨ 核心特性 (Features)

- 📡 **底层实时捕获引擎**：基于 Python + Protobuf 逆向解析，零延迟监听抖音直播间 WebSocket 数据。
- 🧠 **智能扣数与规则匹配**：
  - 支持数字区间、关键词匹配、限量抢单（如：仅放单 100 个）。
  - 支持复杂规则防刷：灯牌优先、同ID N秒内防多打拦截。
- 🖨️ **可视化标签打印引擎**：
  - 内置拖拽式标签模板编辑器（支持 60x40mm 等主流热敏标签纸）。
  - 连接本地打印机实现“扣中即打”的秒级静默出单，无需人工干预。
- 📦 **本地化数据安全**：采用本地 SQLite 数据库存储历史订单与用户黑白名单，无云端泄露风险，断网不丢单。
- 🌐 **内嵌授权系统**：内置 Webview 直接对接抖店后台授权。

## 🛠️ 技术栈 (Tech Stack)

本项目采用**前后端分离混合架构 (Hybrid Architecture)**，兼顾桌面端硬件控制与 Python 爬虫的优势：

- **桌面宿主框架**: [Electron](https://www.electronjs.org/) (打包为独立的 `.exe` / `.dmg` 客户端)
- **前端用户界面**: Vue 3 + Vite + Element Plus (现代化的高性能 UI)
- **底层弹幕引擎**: Python 3 + asyncio + websockets (集成高性能抖音抓包组件)
- **本地数据库**: SQLite3 (处理海量弹幕不卡顿)
- **进程间通信**: Electron IPC + WebSocket (前后端数据流转)

## ⚙️ 系统架构流转图

```text
[抖音直播间]
   │ (WebSocket / Protobuf)
   ▼
[Python 抓取引擎 (engine/main.py)] ──▶ 解析为标准 JSON ──▶ (通过本地 WS 推送)
   │
   ▼
[Electron 主进程 (src/main/)] ──▶ 规则引擎判断过滤 ──▶ 存入 SQLite 数据库
   │                                     │
   │ (IPC 实时推送)                      │ (触发打印事件)
   ▼                                     ▼
[Vue 3 前端界面 (src/renderer/)]     [本地标签打印机 (HPRT等)]
(实时滚动展示、操作控制面板)         (按自定义模板静默打印)
```

## 🚀 快速开始 (Quick Start)

### 1. 环境准备

- 安装 Node.js (v16+)
- 安装 Python (v3.8+)
- 全局安装 Yarn 或 pnpm (可选)

### 2. 启动 Python 核心引擎 (Engine)

```bash
cd engine
pip install -r requirements.txt
python main.py
```

引擎将在本地开启 `ws://localhost:8765` 用于推送弹幕。

### 3. 启动 Electron 桌面端 (Client)

```bash
# 新开一个终端窗口
npm install

# 启动开发者模式 (开启热更新)
npm run dev
```

### 4. 软件打包发布 (Build)

```bash
# 将项目打包为 Windows 平台的 .exe 安装包
npm run build:win
```
