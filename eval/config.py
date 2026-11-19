"""评测配置：数据集路径、检索参数、LLM-as-judge 开关、成本单价与门禁配置。"""
import os
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
DATASETS_DIR = EVAL_DIR / "datasets"
REPORT_JSON = EVAL_DIR / "report.json"
REPORT_MD = EVAL_DIR / "report.md"
GATE_CONFIG = EVAL_DIR / "thresholds.json"

RETRIEVAL_TOP_K = 6

# 是否启用 LLM-as-judge（correctness/faithfulness）；离线或无 Key 时可置 False
ENABLE_LLM_JUDGE = True

# 成本估算单价（USD / 百万 token）。默认按 Anthropic Sonnet 官方价，
# 走第三方网关时实际价格可能不同，用环境变量覆盖。
INPUT_PRICE_PER_MTOK_USD = float(os.getenv("EVAL_INPUT_PRICE_PER_MTOK", "3"))
OUTPUT_PRICE_PER_MTOK_USD = float(os.getenv("EVAL_OUTPUT_PRICE_PER_MTOK", "15"))
