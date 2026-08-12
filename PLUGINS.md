# 插件开发

Miloto 的插件是一个「内容中间件」运行时：插件在消息转发前后拿到内容、可以改写它，但**发送这件事始终由核心（relay）负责**。本文说明怎么写一个插件、能用哪些钩子、配置怎么隔离，以及有哪些不能碰的边界。

读完照着写一个最小插件大约十分钟，`plugins/keyword_replacer/` 是一个可直接照抄的完整示例。

## 目录结构

每个插件一个文件夹，放在 `plugins/` 下：

```
plugins/
  your_plugin/
    __init__.py        # 插件入口，必须有
    manifest.yaml      # 元数据，建议有
    config_schema.json # WebUI 配置表单，可选
    config.json        # 运行时状态，插件自己读写，别手改
```


## 一个最小的插件

```python
from miloto import Star, Context, logger, register
from miloto import on_pre_push


@register(name="hello", version="1.0.0", desc="示例", miloto_version=">=0.1.0")
class Hello(Star):
    def on_load(self, ctx: Context) -> None:
        self.greeting = ctx.config.get("greeting", "hi")

    @on_pre_push
    def rewrite(self, event):
        for seg in event.get("message", []):
            if seg.get("type") == "text":
                text = seg["data"].get("text", "")
                seg["data"]["text"] = f"[{self.greeting}] {text}"
        return event
```

插件唯一该 import 的入口是 `miloto` 包，不要直接 import `core.*`——这样插件和宿主核心解耦，核心内部重构不会牵连你。

## 钩子一览

钩子用一个方法加一个装饰器声明。方法名随意，装饰器决定它订阅哪个事件。

| 装饰器 | 方法签名 | 作用 | 返回值含义 |
| --- | --- | --- | --- |
| `@on_inbound` | `fn(self, msg)` | 入站消息进缓冲前，改单条消息 | 改后的 msg；`None` 表示丢弃 |
| `@on_pre_push` | `fn(self, event)` | **核心**：合并后、转发前改 OneBot 事件 | 改后的 event；`None` 丢弃 |
| `@on_outbound` | `fn(self, req)` | AstrBot→微信 反向 | `True` 拦截（不发微信）；其他继续 |
| `@on_media_ready` | `fn(self, media)` | 图片/文件落地成本地 URL | 忽略 |
| `@on_post_push` | `fn(self, event)` | 已推送给 AstrBot | 忽略 |
| `@on_file` | `fn(self, path, meta)` | 监听目录落盘新文件 | 忽略 |
| `@on_tick` | `fn(self, ts)` | 定时心跳 | 忽略 |

`on_pre_push` 最常用。`event` 是 OneBot 事件字典，要改的内容在 `event["message"]`，它是一个 segment 列表：

```python
# 文本段
{"type": "text", "data": {"text": "你好"}}
# 图片段
{"type": "image", "data": {"url": "http://...", "file": "..."}}
```

改写时遍历 `event["message"]`，按 `seg["type"]` 修改对应的 `seg["data"]`，最后 `return event`（不返回或返回 `None` 会丢弃这条消息）：

`on_outbound` 的 `req` 结构是 `{"contact": 昵称, "group": 是否群, "target": id, "text": 文本, "segments": 段列表}`。返回 `True` 表示这条消息你已经自己处理完了，核心不会再发给微信。

## 生命周期

- `on_load(self, ctx)`：加载时调用一次，用来读配置、建缓存、起后台线程。
- `on_unload(self)`：热重载或退出前调用。如果你起了后台线程或监听，**必须在这里停掉**，否则热重载几次就会叠出一堆。
- `on_config_change(self, cfg)`：只改了你这个插件的参数、没有增删插件时，框架不重建实例，而是直接调这个方法传最新配置，你原地刷新即可。
- `on_error(self, hook, exc)`：你的某个钩子抛异常时框架会调它，方便自己兜底（框架已经记了日志）。
- `on_all_loaded(self)`：所有插件都 `on_load` 完之后调一次，适合做依赖别的插件的初始化，通过 `self.ctx.extensions` 访问其它插件实例。


**1. 静态配置（`config.yaml` 的 `plugins.<name>` 命名空间）**

开发者或用户在 `config.yaml` 里写：

```yaml
plugins:
  your_plugin:
    enabled: true
    greeting: "hi"
```

插件通过 `ctx.config` 读取（在 `on_load` 或任意钩子里用 `self.ctx.config`）。

如果附带了 `config_schema.json`，WebUI 的插件页会自动生成配置表单。schema 是一个字段数组，每个字段描述 `key / label / type / hint` 等，嵌套用 `item_schema`。可参考 `plugins/keyword_replacer/config_schema.json`。

**2. 运行时配置（插件自己的 `config.json`）**

需要持久化运行时状态（计数、缓存、开关）时，用它，别碰别处：

```python
data = self.read_own_config({"count": 0})
data["count"] += 1
self.save_own_config(data)
```

文件落在 `plugins/your_plugin/config.json`，由基类保证隔离，不会和别的插件或中央配置冲突。

## 元数据：`manifest.yaml` 与 `@register`

两种声明插件身份的方式，二选一。框架优先用 `manifest.yaml`，缺失的字段回退到 `@register`：

`manifest.yaml`：

```yaml
name: your_plugin
version: 1.0.0
author: 你的名字
description: 一句话说明
miloto_version: ">=0.1.0"
deps: []
```

`@register` 装饰器（写在类上，效果同上）：

```python
@register(name="your_plugin", version="1.0.0", author="你的名字",
          desc="一句话说明", miloto_version=">=0.1.0")
class YourPlugin(Star):
    ...
```

`miloto_version` 是版本门槛。不满足当前 Miloto 版本时插件会被禁用并告警（当前版本见 `core/version.py` 的 `MILOTO_VERSION`）。`deps` 留作依赖声明位，目前不强制安装。

## 启用与开关

**安装即启用**：把插件文件夹放进 `plugins/` 就会被加载，不需要在 `config.yaml` 额外登记。只有当你把 `plugins.<name>.enabled` 显式写成 `false` 时才会跳过。WebUI 的插件页也能直接开关。

## 边界：插件不能直接发消息

这是框架的核心约定——**插件可以改内容，但不能改发送方式**。

插件拿到的 `Context` 里，`sender` 和 `pusher` 已经被换成禁用桩。任何试图直接调用它们发送的操作都会抛一个清晰的运行时错误，而不是悄悄发错地方。需要主动推送消息的能力，请走内置扩展机制，不要塞进第三方插件。

## 多个插件怎么配合（优先级）

钩子按插件实例的 `priority` 升序执行（数字越小越先）。默认 `priority = 50`，子类可覆盖。

多个 `on_pre_push` 会形成一条接力链，顺序有意义。比如想做「先脱敏（priority=10）→ 再翻译（priority=30）→ 最后加前缀（priority=50）」，直接给每个插件设不同的 `priority` 即可，后一个看到的已经是前一步改过的内容。

## 发布你的插件

把插件文件夹发出来即可（带上 `manifest.yaml`，最好带上 `config_schema.json` 让用户能在 WebUI 配）。使用者把它放进自己的 `plugins/` 目录就生效。几条建议：

- 在 `manifest.yaml` 写准 `miloto_version`，别让用户在用不了的新版本上白装；
- 运行时状态走 `read_own_config` / `save_own_config`，别去写公共位置；
- 后台线程记得在 `on_unload` 停掉；
- 永远不要直接发消息，只改内容。

## 提交到插件市场

Miloto 的插件市场是一个的索引
注意作者没用经历保证所有插件都合规，请使用者谨慎下载使用，若发现者可以举报，若被发现，该插件将下架且把该github账户列入插件黑名单。
流程如下：

1. 把插件源码推到公开的 Git 仓库（GitHub 等），确保带 `manifest.yaml`，`author` / `name` / `version` 写准。
2. 向上游索引仓库提一个 issue，字段至少包含 `name` / `version` / `author` / `desc` / `repo` / `zip_url`；`zip_url` 指向可下载的压缩包（GitHub archive 或 Release 附件），若插件在仓库子目录里用 `subdir` 指明。
3. 维护者 review 代码，确认无恶意行为、能正常加载后合并。合并后所有客户端在下次刷新市场时就会看到你的插件。

> 注意：安装插件等于在用户机器上运行第三方代码。请作者对自己代码负责；使用者装前可用卡片上的github图标先审一遍。

