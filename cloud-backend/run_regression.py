# -*- coding: utf-8 -*-
"""全量回归统一入口：tests/test_*.py 每个文件一个独立 pytest 进程。

为什么逐文件独立进程：
    本仓库两类测试套路互斥——「清 sys.modules + 临时 SQLite」型
    （test_form_doc_flow / test_xlsx_case_regression 等）会在进程内重建
    app 模块；「app.dependency_overrides」型（test_files_auth 等）持有
    旧模块引用。同进程混跑会引用错位、静默失败。仓库惯例一直是按文件
    单独跑；本脚本把这件事变成一条命令。

用法（cloud-backend 目录下）：
    python run_regression.py                    # 全量，逐文件串行
    python run_regression.py --filter szcj      # 只跑文件名含 szcj 的
    python run_regression.py --list             # 只列文件不执行
    python run_regression.py --fail-fast        # 首个失败即停
    python run_regression.py --jobs 4           # 4 进程并行（默认串行）
    python run_regression.py --pytest-args "-k TC037"
    python run_regression.py --timeout 300      # 单文件超时秒数（默认 600）

退出码：0 = 全绿；1 = 存在失败/错误/无用例文件。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

# 汇总行计数对："12 passed", "4 failed", "1 error", "2 skipped" ...
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)")

# 脚本型测试（模块级直接跑断言，无 unittest/pytest 套件）：
#   a) 带 sys.exit( 尾行 —— pytest 收集阶段因 SystemExit 崩（INTERNALERROR）；
#   b) 无 sys.exit 但用 check()/FAILED 计数自报告 —— pytest 收集到 0 用例。
# 两者都必须直接 python 执行。
_SCRIPT_EXITS_RE = re.compile(r"^sys\.exit\s*\(", re.M)
_SCRIPT_COUNTER_RE = re.compile(
    r"^\s*(?:def check\s*\(|FAILED\s*[=:].*(?:int|list)|FAILURES?\s*[=:]"
    r"|ok\s*=\s*True|def main\s*\()", re.M)


def _is_script_style(src: str) -> bool:
    """脚本型 = 无 unittest/pytest 套件的可独立执行测试。

    判据（满足其一）：模块级 sys.exit( 尾行；或带自报告计数的
    check()/FAILED/ok=True/main() 结构。有测试框架 import 的不是脚本型。
    """
    if "import unittest" in src or "import pytest" in src:
        return False
    if _SCRIPT_EXITS_RE.search(src):
        return True
    return bool(_SCRIPT_COUNTER_RE.search(src))


def discover(filter_sub: str) -> list[tuple[Path, str]]:
    """返回 (文件, 模式) 列表；模式 = pytest | script。"""
    out = []
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        if filter_sub and filter_sub.lower() not in f.name.lower():
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        mode = "script" if _is_script_style(src) else "pytest"
        out.append((f, mode))
    return out


def _parse_counts(stdout: str) -> str:
    """取 pytest 输出最后一行汇总（"12 passed, 2 warnings in 5.05s"）。"""
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if _COUNT_RE.search(ln):
            return ln
    return "（无汇总行）"


def run_one(file: Path, mode: str, pytest_args: str, timeout: float):
    """跑单文件，返回 (状态, 计数行, 耗时, 尾部输出)。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if mode == "script":
        cmd = [PY, "-X", "utf8", str(file)]
    else:
        cmd = [PY, "-X", "utf8", "-m", "pytest", str(file.relative_to(ROOT)),
               "-q", "-p", "no:cacheprovider"]
        if pytest_args:
            cmd += pytest_args.split()
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), env=env, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"超过 {timeout:.0f}s", time.monotonic() - t0, ""
    dt = time.monotonic() - t0
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    combined = out + err
    if mode == "script":
        # 脚本型：退出码 0 = 通过；非 0 = 失败。成功字样多套口径都认：
        # 「全部通过」/「RESULT: ALL PASS」/裸「ALL PASS」/「N 通过 / 0 失败」/「N/N 通过」
        ok = proc.returncode == 0 and (
            "全部通过" in combined
            or "RESULT: ALL PASS" in combined
            or any(ln.strip() == "ALL PASS" for ln in combined.splitlines())
            or re.search(r"\d+\s*/\s*\d+\s*通过", combined)
            or re.search(r"\d+\s*通过\s*/\s*0\s*失败", combined)
        )
        status = "PASS" if ok else "FAIL"
        m = re.search(r"(全部通过|RESULT: ALL PASS|失败 \d+ 项[^\n]*)", combined)
        if m:
            summary = m.group(1)
        elif any(ln.strip() == "ALL PASS" for ln in combined.splitlines()):
            summary = "ALL PASS"
        else:
            m2 = re.search(r"(\d+)\s*/?\s*(?:通过\s*/\s*)(\d+)\s*失败", combined)
            if not m2:
                m2 = re.search(r"(\d+)\s*/\s*(\d+)\s*通过", combined)
            summary = (f"{m2.group(1)}/{m2.group(2)} 通过" if m2
                       else f"exit={proc.returncode}")
        tail = "\n".join(combined.splitlines()[-40:])
        return status, summary, dt, tail
    summary = _parse_counts(out)
    rc = proc.returncode
    if rc == 0 and "failed" not in summary and "error" not in summary:
        status = "PASS"
    elif rc == 5 or "no tests ran" in summary:
        status = "NO-TESTS"
    elif rc in (3, 4):
        status = "ERROR"
    else:
        status = "FAIL"
    tail = "\n".join((out + err).splitlines()[-40:])
    return status, summary, dt, tail


def main() -> int:
    ap = argparse.ArgumentParser(description="逐文件全量回归入口")
    ap.add_argument("--filter", default="", help="只跑文件名含该子串的测试")
    ap.add_argument("--list", action="store_true", help="只列文件不执行")
    ap.add_argument("--fail-fast", action="store_true", help="首个失败/错误即停")
    ap.add_argument("--jobs", type=int, default=1, help="并行进程数（默认 1 串行）")
    ap.add_argument("--pytest-args", default="", help="透传给 pytest 的额外参数")
    ap.add_argument("--timeout", type=float, default=600, help="单文件超时秒数")
    args = ap.parse_args()

    files = discover(args.filter)
    if not files:
        print("没有匹配的测试文件")
        return 1
    if args.list:
        for f, mode in files:
            print(f"{f.name:<50} {mode}")
        n_script = sum(1 for _, m in files if m == "script")
        print(f"\n共 {len(files)} 个文件（pytest {len(files) - n_script} / 脚本型 {n_script}）")
        return 0

    n_script = sum(1 for _, m in files if m == "script")
    print(f"共 {len(files)} 个测试文件（pytest {len(files) - n_script} / 脚本型 {n_script}），"
          f"逐文件独立进程"
          f"{'，并行 ' + str(args.jobs) + ' 进程' if args.jobs > 1 else '串行'}执行\n")
    results: list[tuple[Path, str, str, float, str]] = []
    t0 = time.monotonic()

    def _emit(f, status, summary, dt, tail):
        results.append((f, status, summary, dt, tail))
        mark = {"PASS": "✔", "FAIL": "✘", "ERROR": "!",
                "NO-TESTS": "○", "TIMEOUT": "⏱"}.get(status, "?")
        print(f"[{mark} {status:<8}] {f.name:<50} {summary}  ({dt:.1f}s)")

    if args.jobs <= 1:
        for f, mode in files:
            status, summary, dt, tail = run_one(f, mode, args.pytest_args, args.timeout)
            _emit(f, status, summary, dt, tail)
            if args.fail_fast and status != "PASS":
                break
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = {pool.submit(run_one, f, mode, args.pytest_args, args.timeout): (f, mode)
                    for f, mode in files}
            for fut in as_completed(futs):
                f, mode = futs[fut]
                status, summary, dt, tail = fut.result()
                _emit(f, status, summary, dt, tail)
                if args.fail_fast and status != "PASS":
                    for x in futs:
                        x.cancel()
                    break

    wall = time.monotonic() - t0
    bad = [r for r in results if r[1] != "PASS"]
    print(f"\n{'=' * 70}")
    print(f"结果：{len(results)}/{len(files)} 个文件执行，"
          f"通过 {len(results) - len(bad)}，异常 {len(bad)}，总耗时 {wall / 60:.1f} 分钟")

    if bad:
        print("\n---- 失败/异常明细（每文件尾部输出）----")
        for f, status, summary, dt, tail in bad:
            print(f"\n### {f.name}  [{status}]  {summary}")
            print(tail or "（无输出）")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    main()
