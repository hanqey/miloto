

from miloto import Star, Context, logger, on_pre_push, register

@register(name="keyword_replacer", version="1.0.0", author="Miloto",
          desc="关键词替换示例（内容中间件最小演示）", miloto_version=">=0.1.0")
class KeywordReplacer(Star):
    def on_load(self, ctx: Context) -> None:

        self.replacements = self._collect_rules(ctx.config)
        logger.info(f"[插件] keyword_replacer 已加载，规则数={len(self.replacements)}")

    @on_pre_push
    def rewrite_message(self, event):

        if not self.replacements:
            return event
        for seg in event.get("message", []):
            if seg.get("type") == "text":
                text = seg["data"].get("text", "")
                for source, target in self.replacements:
                    text = text.replace(source, target)
                seg["data"]["text"] = text
        logger.debug("[keyword_replacer] 已改写一条消息")
        return event

    def on_config_change(self, cfg: dict) -> None:

        self.replacements = self._collect_rules(cfg)
        logger.info(f"[插件] keyword_replacer 配置已热更新，规则数={len(self.replacements)}")

    @staticmethod
    def _collect_rules(cfg: dict):

        rules = []
        for item in (cfg.get("rules") or []):
            source = item.get("from")
            if source:
                rules.append((source, item.get("to") or ""))
        return rules
