#!/usr/bin/env python3
"""熔断管理。

维护 {output_dir}/{skill-name}/.pipeline_state.json，跟踪迭代轮数和熔断状态。

三种熔断机制:
  1. 迭代预算: 达到 max_iterations → 停止，取历史最佳
  2. 连续无改进: 连续 N 次 pass_rate 无提升 → 停止，保留原版本
  3. 单次退化: pass_rate 下降 > pass_rate_drop → 丢弃，回退

用法:
  python circuit_breaker.py init [--output-dir <path>] [--max-iterations <n>] ...
  python circuit_breaker.py check [--output-dir <path>] [--pass-rate <float>]
  python circuit_breaker.py record [--output-dir <path>] --pass-rate <float> --iteration <n>
"""

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from utils import log, read_json, write_json, load_config_with_overrides, parse_cli_overrides


DEFAULT_STATE = {
    "iteration": 0,
    "max_iterations": 5,
    "no_improvement_count": 0,
    "no_improvement_limit": 3,
    "pass_rate_drop": 0.2,
    "best_pass_rate": 0.0,
    "last_pass_rate": None,
    "history": [],
    "tripped": False,
    "trip_reason": None,
}


def get_state_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / ".pipeline_state.json"


def init_state(output_dir: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """初始化熔断状态文件。"""
    state = {
        **DEFAULT_STATE,
        "max_iterations": config.get("max_iterations", 5),
        "no_improvement_limit": config.get("no_improvement_limit", 3),
        "pass_rate_drop": config.get("pass_rate_drop", 0.2),
        "output_dir": str(output_dir),
    }
    state_path = get_state_path(output_dir)
    write_json(state_path, state)
    log(f"熔断状态已初始化: {state_path}")
    return state


def check(output_dir: str | Path, pass_rate: float | None = None) -> dict[str, Any]:
    """检查熔断状态。

    返回:
      {
        "tripped": bool,
        "reason": str | None,
        "should_stop": bool,
        "state": {...}  # 当前状态
      }
    """
    state_path = get_state_path(output_dir)
    if not state_path.exists():
        log("状态文件不存在，使用默认值", "WARN")
        state = dict(DEFAULT_STATE)
    else:
        state = read_json(state_path)

    checks = []

    # 1. 迭代预算
    if state["iteration"] >= state["max_iterations"]:
        checks.append(f"达到迭代上限 ({state['iteration']}/{state['max_iterations']})")

    # 2. 连续无改进
    if state["no_improvement_count"] >= state["no_improvement_limit"]:
        checks.append(
            f"连续 {state['no_improvement_count']} 次无改进 "
            f"(限制 {state['no_improvement_limit']})"
        )

    # 3. 单次退化（需要本次 pass_rate）
    if pass_rate is not None and state["last_pass_rate"] is not None:
        drop = state["last_pass_rate"] - pass_rate
        if drop > state["pass_rate_drop"]:
            checks.append(
                f"单次退化: pass_rate 下降 {drop:.3f} > {state['pass_rate_drop']}"
            )

    tripped = len(checks) > 0
    reason = "; ".join(checks) if checks else None

    return {
        "tripped": tripped,
        "reason": reason,
        "should_stop": tripped,
        "state": state,
    }


def record(
    output_dir: str | Path,
    pass_rate: float,
    iteration: int,
) -> dict[str, Any]:
    """记录一轮迭代结果，更新熔断状态。"""
    state_path = get_state_path(output_dir)
    if not state_path.exists():
        log("状态文件不存在，先初始化", "WARN")
        state = dict(DEFAULT_STATE)
    else:
        state = read_json(state_path)

    prev_best = state.get("best_pass_rate", 0)
    improved = pass_rate > prev_best

    if improved:
        state["best_pass_rate"] = pass_rate
        state["no_improvement_count"] = 0
    else:
        state["no_improvement_count"] = state.get("no_improvement_count", 0) + 1

    state["last_pass_rate"] = pass_rate
    state["iteration"] = iteration

    state.setdefault("history", []).append({
        "iteration": iteration,
        "pass_rate": pass_rate,
        "improved": improved,
        "best_pass_rate": state["best_pass_rate"],
    })

    write_json(state_path, state)
    log(
        f"迭代 {iteration} 已记录: pass_rate={pass_rate:.4f}, "
        f"improved={improved}, best={state['best_pass_rate']:.4f}, "
        f"no_imp={state['no_improvement_count']}"
    )

    return state


def _get_extra_arg(args: list[str], flag: str) -> str | None:
    """从 argv 列表中提取指定 flag 的值。"""
    try:
        idx = args.index(flag)
        return args[idx + 1]
    except (ValueError, IndexError):
        return None


def main():
    if len(sys.argv) < 2:
        print("用法: circuit_breaker.py <init|check|record> [options]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    extra_args = sys.argv[2:]
    overrides = parse_cli_overrides(extra_args)
    config = load_config_with_overrides(
        Path(__file__).parent.parent / "references" / "pipeline_config.json",
        overrides,
    )
    output_dir = Path(config["output_dir"])

    if command == "init":
        state = init_state(output_dir, config)
        print(json.dumps(state, indent=2, ensure_ascii=False))

    elif command == "check":
        pr_str = _get_extra_arg(extra_args, "--pass-rate")
        pass_rate = float(pr_str) if pr_str else None
        result = check(output_dir, pass_rate)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result["tripped"]:
            sys.exit(1)

    elif command == "record":
        pr_str = _get_extra_arg(extra_args, "--pass-rate")
        it_str = _get_extra_arg(extra_args, "--iteration")
        if pr_str is None:
            log("record 命令需要 --pass-rate 参数", "ERROR")
            sys.exit(1)
        pass_rate = float(pr_str)
        iteration = int(it_str) if it_str else 0
        state = record(output_dir, pass_rate, iteration)
        print(json.dumps(state, indent=2, ensure_ascii=False))

    else:
        log(f"未知命令: {command}", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()
