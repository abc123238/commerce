"""评测配置：数据集路径、检索参数、LLM-as-judge 开关与每集上限。"""
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
DATASETS_DIR = EVAL_DIR / "datasets"
REPORT_JSON = EVAL_DIR / "report.json"
REPORT_MD = EVAL_DIR / "report.md"

RETRIEVAL_TOP_K = 6
MAX_AGENT_CASES = 5  # 每数据集最多跑多少个 agent 用例（LLM 耗时，控制冒烟时长）

# 是否启用 LLM-as-judge（correctness/faithfulness）；离线或无 Key 时可置 False
ENABLE_LLM_JUDGE = True
# 是否将评分回写 Langfuse（需 LANGKFUSE_ENABLED=true 且已部署）
WRITE_BACK_LANGFUSE = False
