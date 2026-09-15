"""工具注册表：名称 -> 实现，统一执行入口（JSON Schema 校验 + 超时 + 截断）。"""
import asyncio

from langchain_core.tools import BaseTool


class ToolRegistry:
    def __init__(self, tools: list[BaseTool], timeout: float = 10.0, max_len: int = 2000):
        self._by_name = {t.name: t for t in tools}
        self.timeout = timeout
        self.max_len = max_len

    @property
    def schemas(self) -> list[dict]:
        """供 LLM bind_tools 使用的 JSON Schema 列表。"""
        return [t.get_input_schema() for t in self._by_name.values()]

    @property
    def tool_names(self) -> list[str]:
        return list(self._by_name.keys())

    async def execute(self, name: str, args: dict) -> str:
        """执行工具：校验参数、超时保护、结果截断。"""
        tool = self._by_name.get(name)
        if tool is None:
            return f"工具 {name} 不存在"
        try:
            result = await asyncio.wait_for(tool.ainvoke(args), timeout=self.timeout)
        except asyncio.TimeoutError:
            return f"工具 {name} 执行超时（>{self.timeout}s）"
        except Exception as exc:  # noqa: BLE001
            return f"工具 {name} 执行失败：{str(exc)[:200]}"
        text = str(result)
        return text[: self.max_len] + ("…(截断)" if len(text) > self.max_len else "")
