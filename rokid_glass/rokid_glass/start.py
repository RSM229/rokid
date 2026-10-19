#!/usr/bin/env python3
"""一键启动监理系统前后端服务"""
import os
import sys
import signal
import subprocess
import threading
import time
from pathlib import Path

# ============ 配置区 ============
ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "cloud-backend"
FRONTEND_DIR = ROOT_DIR / "web-admin"
MOBILE_DIR = ROOT_DIR / "mobile-h5"

# 后端：优先用 conda base 环境（含 uvicorn），可按需修改
BACKEND_PYTHON = os.environ.get("BACKEND_PYTHON", "/opt/anaconda3/bin/python")
BACKEND_PORT = 8000
FRONTEND_PORT = 3000
MOBILE_PORT = 5174

processes = []  # 运行中的子进程
process_tags = []


def log(msg, tag="SYS"):
    """带标签的日志输出"""
    print(f"[{tag}] {msg}", flush=True)


def is_port_in_use(port):
    """检查端口是否被占用"""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True
        )
        return bool(result.stdout.strip())
    except FileNotFoundError:
        return False


def kill_port(port):
    """杀掉占用指定端口的进程"""
    if not is_port_in_use(port):
        return
    log(f"端口 {port} 被占用，正在清理...", tag="PORT")
    result = subprocess.run(
        ["lsof", "-ti", f":{port}"],
        capture_output=True,
        text=True,
    )
    for value in result.stdout.split():
        try:
            os.kill(int(value), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            continue
    time.sleep(1)
    if is_port_in_use(port):
        log(f"警告：端口 {port} 仍被占用", tag="PORT")
    else:
        log(f"端口 {port} 已释放", tag="PORT")


def stream_output(proc, tag):
    """实时读取子进程输出并打印（带标签）"""
    try:
        for line in iter(proc.stdout.readline, ""):
            if line:
                print(f"[{tag}] {line.rstrip()}", flush=True)
    except Exception:
        pass


def start_process(cmd, cwd, tag, env=None):
    """启动一个子进程并开线程转发输出"""
    log(f"启动 {tag}：{' '.join(cmd)}", tag="START")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    processes.append(proc)
    process_tags.append(tag)
    t = threading.Thread(target=stream_output, args=(proc, tag), daemon=True)
    t.start()
    return proc


def wait_for_port(port, timeout=30, tag=""):
    """等待端口就绪"""
    start = time.time()
    while time.time() - start < timeout:
        if is_port_in_use(port):
            log(f"{tag} 端口 {port} 已就绪", tag="READY")
            return True
        time.sleep(0.5)
    log(f"{tag} 端口 {port} 在 {timeout}s 内未就绪", tag="WARN")
    return False


def check_python_env():
    """检查后端 Python 环境是否可用"""
    if not Path(BACKEND_PYTHON).exists():
        log(f"未找到 {BACKEND_PYTHON}，尝试使用当前 python3", tag="ENV")
        return sys.executable or "python3"
    # 验证 uvicorn 是否可用
    res = subprocess.run(
        [BACKEND_PYTHON, "-c", "import uvicorn; print('ok')"],
        capture_output=True, text=True
    )
    if res.returncode == 0 and "ok" in res.stdout:
        return BACKEND_PYTHON
    log(f"{BACKEND_PYTHON} 缺少 uvicorn，尝试 python3", tag="ENV")
    return sys.executable or "python3"


def main():
    log("=== 监理系统一键启动 ===", tag="SYS")
    log(f"项目根目录：{ROOT_DIR}", tag="SYS")

    # 1. 清理端口
    kill_port(BACKEND_PORT)
    kill_port(FRONTEND_PORT)
    kill_port(MOBILE_PORT)

    # 2. 检查 Python 环境
    python_bin = check_python_env()

    # 3. 启动后端
    backend_cmd = [
        python_bin, "-m", "uvicorn", "app.main:app",
        "--reload", "--host", "0.0.0.0", "--port", str(BACKEND_PORT)
    ]
    start_process(backend_cmd, BACKEND_DIR, "BACKEND")

    # 4. 等待后端就绪再启动前端
    wait_for_port(BACKEND_PORT, timeout=30, tag="BACKEND")

    # 5. 启动 PC 管理端和手机 H5
    frontend_cmd = ["npm", "run", "dev"]
    frontend_env = os.environ.copy()
    start_process(frontend_cmd, FRONTEND_DIR, "FRONTEND", env=frontend_env)
    start_process(frontend_cmd, MOBILE_DIR, "MOBILE-H5", env=frontend_env)

    # 6. 等待两个前端就绪
    wait_for_port(FRONTEND_PORT, timeout=30, tag="FRONTEND")
    wait_for_port(MOBILE_PORT, timeout=30, tag="MOBILE-H5")

    log("", tag="SYS")
    log("========================================", tag="SYS")
    log("  所有服务已启动！", tag="SYS")
    log(f"  Web 管理端 : http://localhost:{FRONTEND_PORT}", tag="SYS")
    log(f"  手机 H5    : http://localhost:{MOBILE_PORT}", tag="SYS")
    log(f"  后端 API   : http://localhost:{BACKEND_PORT}", tag="SYS")
    log(f"  API 文档    : http://localhost:{BACKEND_PORT}/docs", tag="SYS")
    log("  按 Ctrl+C 停止所有服务", tag="SYS")
    log("========================================", tag="SYS")
    log("", tag="SYS")

    # 7. 等待子进程结束 / 捕获 Ctrl+C
    def handle_signal(signum, frame):
        log("正在停止所有服务...", tag="STOP")
        for proc in processes:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        time.sleep(1)
        for proc in processes:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        log("所有服务已停止，再见！", tag="STOP")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # 主循环：监控进程存活
    try:
        while True:
            for proc in processes:
                if proc.poll() is not None:
                    idx = processes.index(proc)
                    log(f"{process_tags[idx]} 进程已退出（code={proc.returncode}）", tag="EXIT")
            time.sleep(2)
    except KeyboardInterrupt:
        handle_signal(None, None)


if __name__ == "__main__":
    main()
