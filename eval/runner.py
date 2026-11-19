"""评测主入口：跑数据集 -> 聚合指标 -> 写 report.json / report.md（可选入库与门禁）。

用法（需已启动依赖服务并完成 seed + 知识库入库）：
    python -m eval.runner [--dataset all|retrieval|customer_service|analytics|robustness]
        [--tag TAG ...] [--difficulty easy|medium|hard] [--limit N]
        [--no-judge] [--no-persist] [--gate]
        [--gate-config eval/thresholds.json]
        [--json-out PATH] [--md-out PATH]

退出码：0 成功；1 执行失败；2 门禁未通过。
"""
import argparse
import asyncio
import json
from pathlib import Path

from app.core.logging import get_logger
from eval import config, datasets, engine
from eval.report import build_markdown

logger = get_logger("eval")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.runner", description="运行 RAG / Agent 评测"
    )
    parser.add_argument(
        "--dataset",
        action="append",
        choices=[*datasets.DATASETS, "all"],
        default=None,
        help="可重复指定；缺省/包含 all 时跑全部数据集",
    )
    parser.add_argument("--tag", action="append", default=None, help="按标签过滤（可重复）")
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"])
    parser.add_argument("--limit", type=int, default=None, help="每个数据集最多跑 N 条")
    parser.add_argument("--no-judge", action="store_true", help="关闭 LLM-as-judge")
    parser.add_argument("--no-persist", action="store_true", help="不写入数据库")
    parser.add_argument("--gate", action="store_true", help="按阈值判定门禁")
    parser.add_argument(
        "--gate-config", type=Path, default=config.GATE_CONFIG, help="门禁阈值 JSON"
    )
    parser.add_argument("--json-out", type=Path, default=config.REPORT_JSON)
    parser.add_argument("--md-out", type=Path, default=config.REPORT_MD)
    return parser


def _options_from_args(args: argparse.Namespace) -> engine.EvalOptions:
    names = args.dataset or ["all"]
    if "all" in names:
        names = list(datasets.DATASETS)
    return engine.EvalOptions(
        datasets=names,
        tags=args.tag or [],
        difficulty=args.difficulty,
        limit=args.limit,
        judge=config.ENABLE_LLM_JUDGE and not args.no_judge,
        top_k=config.RETRIEVAL_TOP_K,
    )


def _write_outputs(report: dict, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(build_markdown(report), encoding="utf-8")


async def _execute(
    options: engine.EvalOptions,
    *,
    persist: bool,
    gate_thresholds: dict | None = None,
) -> tuple[dict, str | None]:
    if persist:
        from app.services import eval_service

        row, report = await eval_service.run_eval_now(
            options, triggered_by="cli", gate_thresholds=gate_thresholds
        )
        return report, row.run_id
    report = await engine.run_datasets(options)
    if gate_thresholds is not None:
        report["gate"] = engine.evaluate_gate(report, gate_thresholds)
    return report, None


async def run(options: engine.EvalOptions | None = None) -> dict:
    """向后兼容的脚本入口：执行评测、写 report 文件，不入库。"""
    report, _ = await _execute(options or engine.EvalOptions(), persist=False)
    _write_outputs(report, config.REPORT_JSON, config.REPORT_MD)
    logger.info("eval_done", report=config.REPORT_MD.name)




async def amain(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = _options_from_args(args)
    gate_thresholds = (
        engine.load_thresholds(args.gate_config) if args.gate else None
    )

    try:
        report, run_id = await _execute(
            options, persist=not args.no_persist, gate_thresholds=gate_thresholds
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("eval_run_failed", exc=str(exc)[:300])
        return 1

    _write_outputs(report, args.json_out, args.md_out)
    if run_id:
        logger.info("eval_persisted", run_id=run_id)

    gate = report.get("gate")
    if gate is not None and not gate.get("passed", False):
        failures = [c["name"] for c in gate.get("checks", []) if not c["ok"]]
        logger.error("eval_gate_failed", failures=", ".join(failures))
        return 2
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
