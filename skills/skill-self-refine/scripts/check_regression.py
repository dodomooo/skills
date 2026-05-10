#!/usr/bin/env python3
"""golden_set 回归检查。

判定规则（三项 OR 关系，任一项超标即判回归）:
  1. pass_rate < golden_pass_rate - tolerance
  2. total_tokens > golden_token_mean * 1.3
  3. total_duration_seconds > golden_time_mean * 1.5

用法:
  python check_regression.py --benchmark <path> --golden-set <path> [--pass-rate-tolerance 0.05]
"""

import json
import sys
from pathlib import Path
from typing import Any

# 确保能导入同目录的 utils
sys.path.insert(0, str(Path(__file__).parent))
from utils import log, read_json, parse_cli_overrides


def check_regression(
    benchmark: dict[str, Any],
    golden_set: dict[str, Any],
    tolerance: float = 0.05,
) -> dict[str, Any]:
    """执行回归检查，返回结果 dict。

    返回格式:
      {
        "pass": bool,
        "golden_skipped": bool,  # 无 golden_set 时跳过
        "details": [
          {"metric": "...", "golden": ..., "current": ..., "threshold": ..., "exceeded": bool}
        ]
      }
    """
    # 提取 benchmark 摘要
    summary = benchmark.get("summary", benchmark)
    with_skill = summary.get("with_skill", summary)

    current_pr = with_skill.get("pass_rate", 0)
    current_tokens = with_skill.get("total_tokens", 0)
    current_time = with_skill.get("total_duration_seconds", 0)

    # 提取 golden 基准
    golden_pr = golden_set.get("golden_pass_rate", 0)
    golden_tokens = golden_set.get("golden_token_mean", 0)
    golden_time = golden_set.get("golden_time_mean", 0)

    details = []

    # 1. pass_rate 检查
    pr_threshold = golden_pr - tolerance
    pr_exceeded = current_pr < pr_threshold
    details.append({
        "metric": "pass_rate",
        "golden": golden_pr,
        "current": current_pr,
        "threshold": pr_threshold,
        "tolerance": tolerance,
        "exceeded": pr_exceeded,
    })

    # 2. tokens 检查
    token_threshold = golden_tokens * 1.3
    token_exceeded = current_tokens > token_threshold
    details.append({
        "metric": "total_tokens",
        "golden": golden_tokens,
        "current": current_tokens,
        "threshold": token_threshold,
        "multiplier": 1.3,
        "exceeded": token_exceeded,
    })

    # 3. time 检查
    time_threshold = golden_time * 1.5
    time_exceeded = current_time > time_threshold
    details.append({
        "metric": "total_duration_seconds",
        "golden": golden_time,
        "current": current_time,
        "threshold": time_threshold,
        "multiplier": 1.5,
        "exceeded": time_exceeded,
    })

    any_exceeded = any(d["exceeded"] for d in details)

    return {
        "pass": not any_exceeded,
        "golden_skipped": golden_set.get("_skipped", False),
        "details": details,
    }


def update_golden_set(
    golden_path: Path,
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    """将 benchmark 提升为新的 golden_set（首次创建）。"""
    summary = benchmark.get("summary", benchmark)
    with_skill = summary.get("with_skill", summary)

    # 从 evals 中提取 pass_rate，或直接使用 summary 中的
    evals_data = benchmark.get("evals", [])
    eval_count = len(evals_data)
    if eval_count > 0:
        total_assertions = sum(len(e.get("assertions", [])) for e in evals_data)
        passed = sum(
            sum(1 for a in e.get("assertions", []) if a.get("passed", False))
            for e in evals_data
        )
        pass_rate = passed / total_assertions if total_assertions > 0 else 0
    else:
        pass_rate = with_skill.get("pass_rate", 0)

    golden = {
        "golden_pass_rate": pass_rate,
        "golden_token_mean": with_skill.get("total_tokens", 0),
        "golden_time_mean": with_skill.get("total_duration_seconds", 0),
        "source_benchmark": str(benchmark.get("_source_path", "unknown")),
        "created_at": benchmark.get("created_at", ""),
        "eval_count": eval_count,
    }

    golden_path.parent.mkdir(parents=True, exist_ok=True)
    with open(golden_path, "w", encoding="utf-8") as f:
        json.dump(golden, f, indent=2, ensure_ascii=False)
        f.write("\n")

    log(f"golden_set 已创建: {golden_path}")
    return golden


def update_golden_benchmark(
    golden_path: Path,
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    """更新 golden_benchmark（不降低 golden_pass_rate）。"""
    existing = read_json(golden_path)
    summary = benchmark.get("summary", benchmark)
    with_skill = summary.get("with_skill", summary)

    current_pr = with_skill.get("pass_rate", 0)
    current_tokens = with_skill.get("total_tokens", 0)
    current_time = with_skill.get("total_duration_seconds", 0)

    # 不降低 pass_rate
    if current_pr > existing.get("golden_pass_rate", 0):
        existing["golden_pass_rate"] = current_pr

    # token/time 取较优值（更低更好）
    if current_tokens < existing.get("golden_token_mean", float("inf")):
        existing["golden_token_mean"] = current_tokens
    if current_time < existing.get("golden_time_mean", float("inf")):
        existing["golden_time_mean"] = current_time

    with open(golden_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
        f.write("\n")

    log(f"golden_set 已更新: {golden_path}")
    return existing


def main():
    overrides = parse_cli_overrides()
    tolerance = overrides.get("pass_rate_tolerance", 0.05)

    # 解析位置参数
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    # 从 sys.argv 中提取 --benchmark 和 --golden-set
    benchmark_path = None
    golden_set_path = None
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--benchmark" and i + 1 < len(sys.argv):
            benchmark_path = sys.argv[i + 1]
        elif sys.argv[i] == "--golden-set" and i + 1 < len(sys.argv):
            golden_set_path = sys.argv[i + 1]
        i += 1

    if not benchmark_path:
        log("缺少 --benchmark 参数", "ERROR")
        sys.exit(1)
    if not golden_set_path:
        log("缺少 --golden-set 参数", "ERROR")
        sys.exit(1)

    benchmark = read_json(benchmark_path)
    benchmark["_source_path"] = benchmark_path

    golden_path = Path(golden_set_path)

    if not golden_path.exists():
        log("golden_set 不存在，跳过回归检查，自动创建")
        golden = update_golden_set(golden_path, benchmark)
        result = {
            "pass": True,
            "golden_skipped": True,
            "details": [],
            "message": "首次运行，已创建 golden_set",
        }
    else:
        golden = read_json(golden_path)
        result = check_regression(benchmark, golden, tolerance)
        if result["pass"]:
            update_golden_benchmark(golden_path, benchmark)
            result["message"] = "回归检查通过"
        else:
            result["message"] = "回归检查失败，存在指标退化"

    # 输出结果到 stdout
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # 退出码
    if not result["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
