# -*- coding: utf-8 -*-
"""独立更新器：等待主程序退出，校验并替换 Capture.exe，然后重启。"""
import argparse
import ctypes
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time

APP_NAME = "Capture"
ERROR_TITLE = "Capture 更新失败"


def show_error(message):
    try:
        ctypes.windll.user32.MessageBoxW(0, str(message), ERROR_TITLE, 0x10)
    except Exception:
        pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def wait_for_pid(pid, timeout=60):
    if not pid:
        return
    try:
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass


def wait_until_writable(path, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if not os.path.exists(path):
            return True
        try:
            with open(path, "rb+"):
                return True
        except (PermissionError, OSError):
            time.sleep(0.5)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--sha256", default="")
    parser.add_argument("--log", default="")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    log_path = args.log or os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                                        "MuriokiCapture", "updates", "updater.log")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            encoding="utf-8",
        )
    except Exception:
        logging.basicConfig(level=logging.INFO)

    source = os.path.abspath(args.source)
    target = os.path.abspath(args.target)
    backup = target + ".old"
    staging = target + ".new"

    logging.info("更新开始: source=%s target=%s pid=%s", source, target, args.pid)
    try:
        if not os.path.isfile(source):
            raise RuntimeError("更新文件不存在")
        if os.path.getsize(source) < 1024 * 1024:
            raise RuntimeError("更新文件大小异常")
        if args.sha256:
            actual = sha256_file(source)
            if actual != args.sha256.lower():
                raise RuntimeError("更新文件 SHA256 校验失败")

        # 先等主进程退出，再等目标 exe 解锁；PyInstaller 单文件可能还有子进程持有文件
        wait_for_pid(args.pid, timeout=60)
        if not wait_until_writable(target, timeout=120):
            raise RuntimeError("等待 Capture.exe 退出超时")

        if os.path.exists(backup):
            try:
                os.remove(backup)
            except OSError:
                pass
        os.replace(target, backup)
        logging.info("已备份旧版本: %s", backup)

        try:
            shutil.copy2(source, staging)
            if args.sha256 and sha256_file(staging) != args.sha256.lower():
                raise RuntimeError("复制后的文件校验失败")
            os.replace(staging, target)
        except Exception:
            if os.path.exists(backup) and not os.path.exists(target):
                os.replace(backup, target)
            raise

        logging.info("替换完成，准备启动新版本")
        if args.restart:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen([target], cwd=os.path.dirname(target), close_fds=True, creationflags=flags)
        logging.info("更新成功")
        return 0
    except Exception as e:
        logging.exception("更新失败: %s", e)
        # 尝试恢复旧版本
        try:
            if os.path.exists(backup) and not os.path.exists(target):
                os.replace(backup, target)
        except Exception:
            logging.exception("恢复旧版本失败")
        try:
            if os.path.exists(staging):
                os.remove(staging)
        except Exception:
            pass
        show_error(f"{e}\n\n可以到 GitHub Releases 页面手动下载最新版本。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
