#!/usr/bin/env python3
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path


def configure_stdio() -> None:
    """Use UTF-8 for script logs when the runtime supports reconfiguration."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def resolve_path(raw_path: str) -> Path:
    """Resolve a CLI path while preserving non-ASCII characters."""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def discover_doc_files(raw_inputs: list[str]) -> list[Path]:
    files: list[Path] = []

    if not raw_inputs:
        raw_inputs = ["."]

    for raw in raw_inputs:
        path = resolve_path(raw)

        if not path.exists():
            print(f"警告: 路径不存在，已跳过: {path}")
            continue

        if path.is_dir():
            for item in sorted(
                (
                    item.resolve()
                    for item in path.rglob("*")
                    if item.is_file()
                    and not item.name.startswith("~$")
                    and item.suffix.lower() == ".doc"
                ),
                key=lambda item: str(item.relative_to(path)).casefold(),
            ):
                files.append(item)
            continue

        if path.is_file():
            if path.suffix.lower() == ".doc" and not path.name.startswith("~$"):
                files.append(path)
            else:
                print(f"警告: 非 .doc 文件，已跳过: {path}")

    unique: list[Path] = []
    seen: set[str] = set()
    for f in files:
        key = str(f).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def convert_with_wps_on_windows(doc_path: Path) -> tuple[bool, str]:
    output_path = doc_path.with_suffix(".docx")
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except Exception as exc:
        return False, f"缺少 pywin32 依赖，无法调用 WPS COM: {exc}"

    wps = None
    doc = None
    try:
        pythoncom.CoInitialize()

        for prog_id in ("KWPS.Application", "WPS.Application", "wps.Application"):
            try:
                wps = win32com.client.DispatchEx(prog_id)
                if wps is not None:
                    break
            except Exception:
                continue

        if wps is None:
            return False, "无法创建 WPS COM 对象。请确认已安装 WPS Writer。"

        try:
            wps.Visible = False
        except Exception:
            pass
        try:
            wps.DisplayAlerts = 0
        except Exception:
            pass

        if output_path.exists():
            output_path.unlink()

        doc = wps.Documents.Open(str(doc_path))
        try:
            doc.SaveAs2(str(output_path), 12)
        except Exception:
            doc.SaveAs(str(output_path), 12)

        if not output_path.exists():
            return False, "WPS 返回成功，但未生成输出文件。"

        return True, f"{doc_path} -> {output_path}"
    except Exception as exc:
        return False, str(exc)
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        if wps is not None:
            try:
                wps.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def convert_with_libreoffice(doc_path: Path) -> tuple[bool, str]:
    output_path = doc_path.with_suffix(".docx")
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        return False, "非 Windows 环境未找到 soffice/libreoffice，无法转换 .doc。"

    proc = subprocess.run(
        [
            office,
            "--headless",
            "--convert-to",
            "docx",
            "--outdir",
            str(doc_path.parent),
            str(doc_path),
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if not err:
            err = "LibreOffice 转换失败。"
        return False, err

    if not output_path.exists():
        return False, "转换命令执行成功但未找到输出文件。"

    return True, f"{doc_path} -> {output_path}"


def main() -> int:
    doc_files = discover_doc_files(sys.argv[1:])
    if not doc_files:
        print("未找到可转换的 .doc 文件。")
        return 0

    is_windows = platform.system().lower().startswith("win")
    failures = 0

    for doc in doc_files:
        if is_windows:
            ok, msg = convert_with_wps_on_windows(doc)
        else:
            ok, msg = convert_with_libreoffice(doc)

        if ok:
            print(f"转换成功: {msg}")
        else:
            failures += 1
            print(f"转换失败: {doc}。错误: {msg}", file=sys.stderr)

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    configure_stdio()
    raise SystemExit(main())
