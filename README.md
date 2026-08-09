# Miloto

把微信个人号接入 AI（AstrBot）的桥接程序。微信消息经 WeFlow 实时抓取，由 Miloto 做过滤、去重与缓冲合并，转成 OneBot v11 事件推给 AstrBot；AstrBot 的回复再经 Miloto 发回微信。

## 功能

- 微信 4.x 接入（依赖 WeFlow 的本地 API）
- 文本 / 图片 / 文件转发，群聊按 OneBot 标准转发
- 多个 OneBot / 多个 WeFlow 连接，单独开关与配置
- 插件钩子：入站、推送前、出站、媒体就绪、文件、定时 tick 等
- 网页控制台：状态、日志、连接管理、基础设置、插件管理、密码修改
- 配置热更新，连接开关实时生效，无需重启

## 架构

```
微信  ←→  WeFlow  ──SSE──→  Miloto  ──OneBot v11（反向 WS）──→  AstrBot
                               │
                               └── 插件总线（内容中间件）
```

Miloto 内部分几块：入站中继（relay）、桥接引擎（engine）、事件总线（bus）、扩展 / 插件运行时（extension + plugins）、网页控制台（web_panel），以及 WeFlow / OneBot / 文件服务相关的网络层（net）。

## 快速开始

前置条件：

- Windows + 桌面微信
- [WeFlow](https://weflow.top) 已登录并开启 API 服务（默认端口 5031）
- 已部署运行的 AstrBot，启用 aiocqhttp 适配器（反向 WebSocket，例如 `ws://127.0.0.1:11229/ws`）
- Python 3.10+

安装与运行：

```bash
pip install -r requirements.txt
python main.py
```

首次启动会在日志里打印一个随机初始密码；用它在网页控制台（默认 `http://127.0.0.1:8127`）登录，登录后强制设置新密码。之后修改密码走侧栏「修改密码」。

## 配置

配置写在 `config.yaml`，也可以在网页「基础设置」里改（保存即热重载）。常用项：

- `bot.names` / `bot.wxid`：机器人昵称与 wxid（用于防自回复）
- `web.host` / `web.port`：控制台监听地址与端口
- `connections.onebot` / `connections.weflow`：OneBot / WeFlow 连接列表
- `files.storage_dir` / `files.download_dir`：媒体缓存与文件监听目录
- `wechat.auto_original` / `wechat.auto_open_to_download`：原图相关行为

## 插件

插件放在 `plugins/<name>/`，通过钩子介入消息流：可以改内容、但不能直接发消息（发送由核心独占）。每个插件自带 `manifest.yaml`，声明名称、版本、最低 Miloto 版本与依赖。

怎么写一个插件（钩子、配置隔离、生命周期、边界约束）写在 [PLUGINS.md](PLUGINS.md)；`plugins/keyword_replacer/` 是一个可直接照抄的完整示例。

## 开源协议与出处

Miloto 是在 Akasha-WeChat（MIT 许可，作者 alingalingling）的基础上重写的，感谢原项目提供的架构思路。

Miloto 自身以 MIT 许可证发布，详见 `LICENSE`；Akasha-WeChat 的版权归 alingalingling 所有。

## 免责声明

本项目仅供学习与技术研究。微信个人号相关自动化可能违反微信服务条款，使用风险自负；请勿用于任何违规或侵害他人权益的用途。
