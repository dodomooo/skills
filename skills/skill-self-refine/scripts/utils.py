"""skill-self-refine 共享工具函数。"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def resolve_path(path: str, base_dir: str | None = None) -> Path:
    """解析路径，支持 ~ 和相对路径。"""
    p = Path(path).expanduser()
    if not p.is_absolute() and base_dir:
        p = Path(base_dir) / p
    return p.resolve()


def read_json(path: str | Path) -> dict[str, Any]:
    """读取 JSON 文件，不存在或格式错误时抛出明确异常。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 格式错误 ({p}): {e}")


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    """写入 JSON 文件，自动创建父目录。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def read_text(path: str | Path) -> str:
    """读取文本文件。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    return p.read_text(encoding="utf-8")


def timestamp_str() -> str:
    """生成 ISO 时间戳字符串（用于备份目录命名）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def log(msg: str, level: str = "INFO") -> None:
    """统一日志输出到 stderr，不污染 stdout。"""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr)


def parse_cli_overrides(args: list[str] | None = None) -> dict[str, Any]:
    """从命令行参数解析配置覆盖值。

    支持的标志:
        -o, --output-dir <path>
        -n, --max-iterations <int>
        --no-improvement-limit <int>
        --pass-rate-drop <float>
        --pass-rate-tolerance <float>

    返回 dict，仅包含用户显式指定的键。未指定的键不在 dict 中。
    """
    if args is None:
        args = sys.argv[1:]

    overrides: dict[str, Any] = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-o", "--output-dir"):
            i += 1
            if i < len(args):
                overrides["output_dir"] = args[i]
        elif arg in ("-n", "--max-iterations"):
            i += 1
            if i < len(args):
                overrides["max_iterations"] = int(args[i])
        elif arg == "--no-improvement-limit":
            i += 1
            if i < len(args):
                overrides["no_improvement_limit"] = int(args[i])
        elif arg == "--pass-rate-drop":
            i += 1
            if i < len(args):
                overrides["pass_rate_drop"] = float(args[i])
        elif arg == "--pass-rate-tolerance":
            i += 1
            if i < len(args):
                overrides["pass_rate_tolerance"] = float(args[i])
        i += 1
    return overrides


def load_config_with_overrides(
    config_path: str | Path,
    cli_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """加载 pipeline_config.json 并应用 CLI 覆盖。

    config_path 应指向 references/pipeline_config.json。
    CLI 参数和用户对话中指定的值通过 cli_overrides 传入，优先级最高。
    """
    config_file = Path(config_path)
    config = read_json(config_file) if config_file.exists() else {}

    # 兜底默认值（配置文件不存在时使用）
    defaults = {
        "output_dir": "./skill-refine-output/",
        "max_iterations": 5,
        "no_improvement_limit": 3,
        "pass_rate_drop": 0.2,
        "pass_rate_tolerance": 0.05,
    }
    for k, v in defaults.items():
        config.setdefault(k, v)

    if cli_overrides:
        config.update(cli_overrides)

    return config
