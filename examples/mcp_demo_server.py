"""MCP 演示服务器：把电商商品查询暴露为标准 MCP 工具（MCP 工具集成示例）。

说明：MCP（Model Context Protocol）是模型与工具间的开放标准。
本示例用 FastMCP 把业务库里的商品查询能力包成标准工具，任何 MCP 客户端
（Claude Desktop / Claude Code / 自定义 client）都能自动发现并调用。

运行：
    uv run python examples/mcp_demo_server.py
联调：
    - Claude Desktop 配置 mcpServers 指向该命令即可。
    - 或用 MCP Inspector：npx @modelcontextprotocol/inspector \
        python examples/mcp_demo_server.py
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from app.db.models.product import Product

# 声明 MCP 服务器，名称会显示在客户端工具列表中
mcp = FastMCP("commerce-product-server")


@mcp.tool()
async def query_product(name: str) -> str:
    """按名称模糊查询商品：返回价格、库存与上下架状态。

    参数 name：商品名称关键词（如"耳机"）。
    """
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Product)
                .where(Product.name.contains(name))
                .order_by(Product.id)
                .limit(5)
            )
        ).scalars().all()
        if not rows:
            return f"未找到名称包含「{name}」的商品"
        lines = [
            f"- {p.name}：¥{p.price:.2f}，库存 {p.stock}，{'在售' if p.status == 'on' else '下架'}"
            for p in rows
        ]
        return "查询结果（来自真实业务库）：\n" + "\n".join(lines)


if __name__ == "__main__":
    # stdio 传输：Claude Desktop / CLI 客户端默认使用
    mcp.run()
