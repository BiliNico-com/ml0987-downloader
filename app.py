#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ml0987.xyz 视频下载器 - GUI 版本
使用 tkinter（Python 自带），无需额外安装 GUI 库
"""

import os
import sys
import json
import queue
import shutil
import logging
import platform
import threading
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime
from pathlib import Path

# ──────────────────────────────────────────────
#  路径常量
# ──────────────────────────────────────────────
APP_DIR      = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_FILE  = APP_DIR / "config.json"
PROGRESS_FILE = APP_DIR / "progress.json"
LOG_FILE     = APP_DIR / "app.log"
FFMPEG_WIN   = APP_DIR / "ffmpeg" / "ffmpeg.exe"   # 便携版 ffmpeg 位置

BASE_URL = "https://ml0987.xyz"

# ──────────────────────────────────────────────
#  默认配置
# ──────────────────────────────────────────────
DEFAULT_CONFIG = {
    "output_dir":    str(APP_DIR / "downloads"),
    "ffmpeg_path":   "",           # 空 = 自动探测
    "proxy_enabled": False,
    "proxy_host":    "127.0.0.1",
    "proxy_port":    "1080",
    "proxy_user":    "",
    "proxy_pass":    "",
    "list_type":     "list",       # list | hot
    "page_start":    1,
    "page_end":      3,
    "sniff_wait":    15,
    "headless":      True,
}

# ──────────────────────────────────────────────
#  日志
# ──────────────────────────────────────────────
log_queue: queue.Queue = queue.Queue()

class QueueHandler(logging.Handler):
    def emit(self, record):
        log_queue.put(self.format(record))

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(fmt)
_qh = QueueHandler()
_qh.setFormatter(fmt)
root_logger.addHandler(_fh)
root_logger.addHandler(_qh)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  配置读写
# ──────────────────────────────────────────────
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text("utf-8")))
        except Exception:
            pass
    return cfg

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

# ──────────────────────────────────────────────
#  依赖检测
# ──────────────────────────────────────────────
REQUIRED_PKGS = ["selenium", "webdriver_manager", "bs4", "requests"]

def check_python_deps() -> dict[str, bool]:
    result = {}
    for pkg in REQUIRED_PKGS:
        try:
            __import__(pkg)
            result[pkg] = True
        except ImportError:
            result[pkg] = False
    return result

def check_ffmpeg(cfg: dict) -> tuple[bool, str]:
    """返回 (ok, 实际路径)"""
    candidates = []
    # 1. 配置文件指定路径
    if cfg.get("ffmpeg_path"):
        candidates.append(cfg["ffmpeg_path"])
    # 2. 程序目录便携版
    candidates.append(str(FFMPEG_WIN))
    # 3. 系统 PATH
    candidates.append("ffmpeg")

    for path in candidates:
        try:
            r = subprocess.run(
                [path, "-version"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=5
            )
            if r.returncode == 0:
                return True, path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False, ""

def install_python_deps(callback):
    """后台安装 Python 依赖"""
    def _run():
        pkgs = ["selenium", "webdriver-manager", "beautifulsoup4", "lxml", "requests", "PySocks", "pycryptodome"]
        log.info("开始安装 Python 依赖...")
        for pkg in pkgs:
            log.info(f"  安装 {pkg}...")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg, "--quiet"],
                capture_output=True, text=True
            )
            if r.returncode != 0:
                log.error(f"  安装 {pkg} 失败: {r.stderr[:300]}")
            else:
                log.info(f"  {pkg} 安装成功")
        log.info("Python 依赖安装完成，请重启程序使其生效")
        callback()
    threading.Thread(target=_run, daemon=True).start()

# ──────────────────────────────────────────────
#  GUI 主窗口
# ──────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.title("🎬 ml0987 视频下载器")
        self.geometry("820x620")
        self.minsize(700, 500)
        self.configure(bg="#1e1e2e")
        self.resizable(True, True)

        # 任务控制
        self._task_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._build_ui()
        self._start_log_poll()
        # 延迟自检，等窗口渲染完成
        self.after(300, self._run_health_check)

    # ── UI 构建 ────────────────────────────────
    def _build_ui(self):
        self._apply_style()

        # 顶部标题栏
        hdr = tk.Frame(self, bg="#181825", pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🎬  ml0987 视频下载器", font=("Microsoft YaHei", 16, "bold"),
                 bg="#181825", fg="#cdd6f4").pack(side="left", padx=16)
        self._status_var = tk.StringVar(value="就绪")
        tk.Label(hdr, textvariable=self._status_var, font=("Microsoft YaHei", 10),
                 bg="#181825", fg="#a6e3a1").pack(side="right", padx=16)

        # 标签页
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(4, 0))

        self._tab_check  = ttk.Frame(nb)
        self._tab_crawl  = ttk.Frame(nb)
        self._tab_single = ttk.Frame(nb)
        self._tab_set    = ttk.Frame(nb)
        self._tab_log    = ttk.Frame(nb)

        nb.add(self._tab_check,  text="  ✅ 环境检测  ")
        nb.add(self._tab_crawl,  text="  📋 批量爬取  ")
        nb.add(self._tab_single, text="  🔗 单视频    ")
        nb.add(self._tab_set,    text="  ⚙️ 设置      ")
        nb.add(self._tab_log,    text="  📄 日志      ")

        self._build_tab_check()
        self._build_tab_crawl()
        self._build_tab_single()
        self._build_tab_settings()
        self._build_tab_log()

        # 底部进度条
        bot = tk.Frame(self, bg="#1e1e2e", pady=4)
        bot.pack(fill="x", padx=8, pady=(0, 6))
        self._progress = ttk.Progressbar(bot, mode="indeterminate", length=200)
        self._progress.pack(side="left", padx=(0, 10))
        self._prog_label = tk.Label(bot, text="", bg="#1e1e2e", fg="#cdd6f4",
                                    font=("Microsoft YaHei", 9))
        self._prog_label.pack(side="left")
        self._stop_btn = ttk.Button(bot, text="⏹ 停止", command=self._stop_task, state="disabled")
        self._stop_btn.pack(side="right")

    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        bg, fg, sel = "#1e1e2e", "#cdd6f4", "#313244"
        s.configure("TNotebook",        background=bg, borderwidth=0)
        s.configure("TNotebook.Tab",    background="#313244", foreground=fg,
                     padding=[12, 6], font=("Microsoft YaHei", 10))
        s.map("TNotebook.Tab",          background=[("selected", "#45475a")])
        s.configure("TFrame",           background=bg)
        s.configure("TLabel",           background=bg, foreground=fg,
                     font=("Microsoft YaHei", 10))
        s.configure("TButton",          font=("Microsoft YaHei", 10),
                     background="#585b70", foreground=fg, borderwidth=0, padding=6)
        s.map("TButton",                background=[("active", "#7f849c")])
        s.configure("Accent.TButton",   background="#89b4fa", foreground="#1e1e2e",
                     font=("Microsoft YaHei", 10, "bold"), padding=8)
        s.map("Accent.TButton",         background=[("active", "#b4befe")])
        s.configure("TEntry",           fieldbackground="#313244", foreground=fg,
                     insertcolor=fg, borderwidth=1)
        s.configure("TCheckbutton",     background=bg, foreground=fg,
                     font=("Microsoft YaHei", 10))
        s.configure("TProgressbar",     troughcolor="#313244", background="#89b4fa",
                     borderwidth=0)
        s.configure("TLabelframe",      background=bg, foreground="#89dceb", borderwidth=1)
        s.configure("TLabelframe.Label",background=bg, foreground="#89dceb",
                     font=("Microsoft YaHei", 10, "bold"))
        s.configure("TCombobox",        fieldbackground="#313244", foreground=fg,
                     selectbackground="#45475a")
        s.configure("TSpinbox",         fieldbackground="#313244", foreground=fg)

    # ── 环境检测 Tab ────────────────────────────
    def _build_tab_check(self):
        f = self._tab_check
        f.configure(style="TFrame")

        tk.Label(f, text="运行环境检测", font=("Microsoft YaHei", 13, "bold"),
                 bg="#1e1e2e", fg="#89b4fa").pack(pady=(18, 4))
        tk.Label(f, text="首次运行请先完成下方所有检测项", bg="#1e1e2e",
                 fg="#a6adc8", font=("Microsoft YaHei", 10)).pack(pady=(0, 12))

        # 检测结果卡片
        card = ttk.LabelFrame(f, text="依赖状态", padding=14)
        card.pack(fill="x", padx=30, pady=4)

        self._dep_rows = {}
        items = [
            ("python_pkgs", "Python 依赖包", "selenium / webdriver-manager / beautifulsoup4 等"),
            ("chrome",      "Google Chrome", "需已安装 Chrome 浏览器"),
            ("chromedriver","ChromeDriver",  "由 webdriver-manager 自动管理"),
            ("ffmpeg",      "ffmpeg",        "视频转码工具"),
        ]
        for key, label, desc in items:
            row = tk.Frame(card, bg="#1e1e2e")
            row.pack(fill="x", pady=3)
            icon = tk.Label(row, text="⏳", bg="#1e1e2e", font=("", 14), width=3)
            icon.pack(side="left")
            tk.Label(row, text=f"{label}", bg="#1e1e2e", fg="#cdd6f4",
                     font=("Microsoft YaHei", 10, "bold"), width=16, anchor="w").pack(side="left")
            tk.Label(row, text=desc, bg="#1e1e2e", fg="#6c7086",
                     font=("Microsoft YaHei", 9)).pack(side="left", padx=6)
            status = tk.Label(row, text="检测中...", bg="#1e1e2e", fg="#f9e2af",
                               font=("Microsoft YaHei", 9))
            status.pack(side="right", padx=8)
            self._dep_rows[key] = {"icon": icon, "status": status}

        # 操作按钮
        btn_frame = tk.Frame(f, bg="#1e1e2e")
        btn_frame.pack(pady=16)

        ttk.Button(btn_frame, text="🔄 重新检测", command=self._run_health_check,
                   style="TButton").pack(side="left", padx=8)
        ttk.Button(btn_frame, text="📦 一键安装 Python 依赖",
                   command=self._install_deps, style="Accent.TButton").pack(side="left", padx=8)
        ttk.Button(btn_frame, text="⬇️ 下载 ffmpeg (Windows)",
                   command=self._guide_ffmpeg).pack(side="left", padx=8)

        # 说明
        note = ttk.LabelFrame(f, text="安装说明", padding=12)
        note.pack(fill="x", padx=30, pady=8)
        notes = [
            "① 点击「一键安装 Python 依赖」→ 等待日志显示完成 → 点击「重新检测」",
            "② Chrome 需手动安装：https://www.google.cn/intl/zh-CN/chrome/",
            "③ ffmpeg：点击「下载 ffmpeg」按钮，下载后解压，将 ffmpeg.exe 放入程序目录的 ffmpeg/ 文件夹",
            "④ 或在「设置」页指定 ffmpeg.exe 的完整路径",
        ]
        for n in notes:
            tk.Label(note, text=n, bg="#1e1e2e", fg="#a6adc8",
                     font=("Microsoft YaHei", 9), justify="left", anchor="w").pack(fill="x", pady=1)

    def _run_health_check(self):
        def _check():
            # Python 依赖
            deps = check_python_deps()
            all_ok = all(deps.values())
            self._set_dep_row("python_pkgs", all_ok,
                              "全部就绪" if all_ok else "缺少: " + ", ".join(k for k, v in deps.items() if not v))

            # Chrome
            chrome_ok = bool(shutil.which("chrome") or shutil.which("google-chrome") or
                             Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists() or
                             Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe").exists())
            self._set_dep_row("chrome", chrome_ok,
                              "已安装" if chrome_ok else "未检测到，请安装 Chrome")

            # ChromeDriver（依赖 webdriver-manager）
            try:
                from webdriver_manager.chrome import ChromeDriverManager
                self._set_dep_row("chromedriver", True, "由 webdriver-manager 自动管理")
            except ImportError:
                self._set_dep_row("chromedriver", False, "需先安装 Python 依赖")

            # ffmpeg
            ffmpeg_ok, ffmpeg_path = check_ffmpeg(self.cfg)
            self._set_dep_row("ffmpeg", ffmpeg_ok,
                              f"路径: {ffmpeg_path}" if ffmpeg_ok else "未找到 ffmpeg")
            if ffmpeg_ok and not self.cfg.get("ffmpeg_path"):
                self.cfg["ffmpeg_path"] = ffmpeg_path
                save_config(self.cfg)

            status_text = "✅ 环境就绪，可以开始下载" if (all_ok and ffmpeg_ok) else "⚠️ 部分依赖未就绪，请查看环境检测页"
            self.after(0, lambda: self._status_var.set(status_text))

        threading.Thread(target=_check, daemon=True).start()

    def _set_dep_row(self, key: str, ok: bool, text: str):
        def _upd():
            row = self._dep_rows[key]
            row["icon"].config(text="✅" if ok else "❌")
            row["status"].config(
                text=text,
                fg="#a6e3a1" if ok else "#f38ba8"
            )
        self.after(0, _upd)

    def _install_deps(self):
        self._set_status("正在安装 Python 依赖，请稍候...")
        self._progress.start(10)
        def done():
            self.after(0, lambda: self._progress.stop())
            self.after(0, lambda: self._set_status("依赖安装完成，请重新检测"))
            self.after(0, self._run_health_check)
        install_python_deps(done)

    def _guide_ffmpeg(self):
        webbrowser.open("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip")
        messagebox.showinfo(
            "ffmpeg 安装指南",
            "1. 已为您打开 ffmpeg 下载页面\n"
            "2. 下载完成后解压 ZIP 文件\n"
            "3. 将 bin/ffmpeg.exe 复制到本程序目录下的 ffmpeg/ 文件夹\n"
            "   （如果 ffmpeg 目录不存在，请手动创建）\n"
            "4. 或在「设置」页手动指定 ffmpeg.exe 路径\n"
            "5. 完成后点击「重新检测」",
            parent=self
        )

    # ── 批量爬取 Tab ────────────────────────────
    def _build_tab_crawl(self):
        f = self._tab_crawl

        # 参数区
        param_frame = ttk.LabelFrame(f, text="爬取参数", padding=14)
        param_frame.pack(fill="x", padx=20, pady=12)

        row0 = tk.Frame(param_frame, bg="#1e1e2e")
        row0.pack(fill="x", pady=4)
        ttk.Label(row0, text="列表类型：").pack(side="left", padx=(0, 6))
        self._list_type = tk.StringVar(value=self.cfg.get("list_type", "list"))
        ttk.Radiobutton(row0, text="最新 (list)", variable=self._list_type,
                        value="list").pack(side="left", padx=6)
        ttk.Radiobutton(row0, text="最热 (hot_list)", variable=self._list_type,
                        value="hot").pack(side="left", padx=6)

        row1 = tk.Frame(param_frame, bg="#1e1e2e")
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="起始页：").pack(side="left", padx=(0, 4))
        self._page_start = ttk.Spinbox(row1, from_=1, to=999, width=6,
                                        textvariable=tk.StringVar(value=str(self.cfg.get("page_start", 1))))
        self._page_start.pack(side="left", padx=(0, 16))
        ttk.Label(row1, text="结束页：").pack(side="left", padx=(0, 4))
        self._page_end = ttk.Spinbox(row1, from_=1, to=999, width=6,
                                      textvariable=tk.StringVar(value=str(self.cfg.get("page_end", 3))))
        self._page_end.pack(side="left", padx=(0, 16))
        ttk.Label(row1, text="嗅探等待(秒)：").pack(side="left", padx=(0, 4))
        self._sniff_wait = ttk.Spinbox(row1, from_=5, to=120, width=6,
                                        textvariable=tk.StringVar(value=str(self.cfg.get("sniff_wait", 15))))
        self._sniff_wait.pack(side="left")

        row2 = tk.Frame(param_frame, bg="#1e1e2e")
        row2.pack(fill="x", pady=4)
        self._headless_var = tk.BooleanVar(value=self.cfg.get("headless", True))
        ttk.Checkbutton(row2, text="无头模式（不显示浏览器窗口）",
                        variable=self._headless_var).pack(side="left")

        # 输出目录展示
        out_frame = ttk.LabelFrame(f, text="输出目录", padding=10)
        out_frame.pack(fill="x", padx=20, pady=4)
        dir_row = tk.Frame(out_frame, bg="#1e1e2e")
        dir_row.pack(fill="x")
        self._out_dir_label = tk.Label(dir_row, text=self.cfg.get("output_dir", ""),
                                        bg="#313244", fg="#cdd6f4", anchor="w",
                                        font=("Consolas", 9), relief="flat", padx=6)
        self._out_dir_label.pack(side="left", fill="x", expand=True, pady=2)
        ttk.Button(dir_row, text="更改", command=self._change_output_dir).pack(side="right", padx=(6, 0))

        # 进度信息
        prog_frame = ttk.LabelFrame(f, text="任务进度", padding=10)
        prog_frame.pack(fill="x", padx=20, pady=4)
        self._crawl_info = tk.Label(prog_frame, text="等待开始...",
                                     bg="#1e1e2e", fg="#a6adc8",
                                     font=("Microsoft YaHei", 10))
        self._crawl_info.pack(anchor="w")

        # 启动按钮
        btn_row = tk.Frame(f, bg="#1e1e2e")
        btn_row.pack(pady=16)
        ttk.Button(btn_row, text="🚀  开始批量下载", style="Accent.TButton",
                   command=self._start_crawl).pack(side="left", padx=10, ipadx=20)
        ttk.Button(btn_row, text="📂 打开输出目录",
                   command=self._open_output_dir).pack(side="left", padx=10)

    def _build_tab_single(self):
        f = self._tab_single

        ttk.LabelFrame(f, text="", padding=0)  # spacer

        url_frame = ttk.LabelFrame(f, text="视频页 URL", padding=14)
        url_frame.pack(fill="x", padx=20, pady=20)

        tk.Label(url_frame, text="粘贴视频页地址（支持多行，每行一个）：",
                 bg="#1e1e2e", fg="#a6adc8", font=("Microsoft YaHei", 9)).pack(anchor="w", pady=(0, 4))
        self._single_urls = tk.Text(url_frame, height=6, bg="#313244", fg="#cdd6f4",
                                     insertbackground="#cdd6f4", font=("Consolas", 10),
                                     relief="flat", padx=6, pady=4)
        self._single_urls.pack(fill="x")

        example = "例：https://ml0987.xyz/video-1186527.htm"
        tk.Label(url_frame, text=example, bg="#1e1e2e", fg="#6c7086",
                 font=("Consolas", 9)).pack(anchor="w", pady=(4, 0))

        wait_row = tk.Frame(f, bg="#1e1e2e")
        wait_row.pack(pady=8, padx=20, anchor="w")
        ttk.Label(wait_row, text="嗅探等待(秒)：").pack(side="left", padx=(0, 4))
        self._single_wait = ttk.Spinbox(wait_row, from_=5, to=120, width=6,
                                         textvariable=tk.StringVar(value=str(self.cfg.get("sniff_wait", 15))))
        self._single_wait.pack(side="left")
        self._single_headless = tk.BooleanVar(value=self.cfg.get("headless", True))
        ttk.Checkbutton(wait_row, text="  无头模式", variable=self._single_headless).pack(side="left", padx=12)

        btn_row = tk.Frame(f, bg="#1e1e2e")
        btn_row.pack(pady=12)
        ttk.Button(btn_row, text="▶  开始下载", style="Accent.TButton",
                   command=self._start_single).pack(side="left", padx=10, ipadx=20)
        ttk.Button(btn_row, text="📂 打开输出目录",
                   command=self._open_output_dir).pack(side="left", padx=10)

        # 直接 m3u8
        m3u8_frame = ttk.LabelFrame(f, text="直接输入 m3u8 地址（无需浏览器）", padding=14)
        m3u8_frame.pack(fill="x", padx=20, pady=4)
        m3u8_row = tk.Frame(m3u8_frame, bg="#1e1e2e")
        m3u8_row.pack(fill="x")
        self._m3u8_url = ttk.Entry(m3u8_row, font=("Consolas", 9))
        self._m3u8_url.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(m3u8_frame, text="标题：", bg="#1e1e2e").pack(side="left", padx=(0, 4))
        self._m3u8_title = ttk.Entry(m3u8_frame, width=20, font=("Microsoft YaHei", 9))
        self._m3u8_title.insert(0, "视频")
        self._m3u8_title.pack(side="left", padx=(0, 8))
        ttk.Button(m3u8_frame, text="▶ 转码下载", command=self._start_m3u8).pack(side="left")

    # ── 设置 Tab ────────────────────────────────
    def _build_tab_settings(self):
        f = self._tab_set

        # 保存目录
        dir_frame = ttk.LabelFrame(f, text="📁  保存目录", padding=14)
        dir_frame.pack(fill="x", padx=20, pady=12)

        dir_row = tk.Frame(dir_frame, bg="#1e1e2e")
        dir_row.pack(fill="x")
        self._out_dir_var = tk.StringVar(value=self.cfg.get("output_dir", ""))
        ttk.Entry(dir_row, textvariable=self._out_dir_var,
                  font=("Consolas", 9)).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(dir_row, text="浏览...", command=self._browse_dir).pack(side="right")

        tk.Label(dir_frame, text="视频按「保存目录 / 转码日期 / 视频标题 / 视频标题.mp4」结构存放",
                 bg="#1e1e2e", fg="#6c7086", font=("Microsoft YaHei", 9)).pack(anchor="w", pady=(6, 0))

        # ffmpeg 路径
        ff_frame = ttk.LabelFrame(f, text="🔧  ffmpeg 路径", padding=14)
        ff_frame.pack(fill="x", padx=20, pady=4)

        ff_row = tk.Frame(ff_frame, bg="#1e1e2e")
        ff_row.pack(fill="x")
        self._ffmpeg_var = tk.StringVar(value=self.cfg.get("ffmpeg_path", ""))
        ttk.Entry(ff_row, textvariable=self._ffmpeg_var,
                  font=("Consolas", 9)).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(ff_row, text="浏览...", command=self._browse_ffmpeg).pack(side="right")
        tk.Label(ff_frame, text="留空则自动检测（优先使用程序目录下的 ffmpeg/ffmpeg.exe）",
                 bg="#1e1e2e", fg="#6c7086", font=("Microsoft YaHei", 9)).pack(anchor="w", pady=(6, 0))

        # SOCKS5 代理
        proxy_frame = ttk.LabelFrame(f, text="🌐  SOCKS5 代理（网站无法访问时启用）", padding=14)
        proxy_frame.pack(fill="x", padx=20, pady=4)

        self._proxy_enabled = tk.BooleanVar(value=self.cfg.get("proxy_enabled", False))
        ttk.Checkbutton(proxy_frame, text="启用 SOCKS5 代理",
                        variable=self._proxy_enabled,
                        command=self._toggle_proxy_fields).pack(anchor="w", pady=(0, 8))

        self._proxy_fields_frame = tk.Frame(proxy_frame, bg="#1e1e2e")
        self._proxy_fields_frame.pack(fill="x")

        # Host + Port
        row1 = tk.Frame(self._proxy_fields_frame, bg="#1e1e2e")
        row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="主机：", width=8).pack(side="left")
        self._proxy_host = ttk.Entry(row1, width=20, font=("Consolas", 10))
        self._proxy_host.insert(0, self.cfg.get("proxy_host", "127.0.0.1"))
        self._proxy_host.pack(side="left", padx=(0, 16))
        ttk.Label(row1, text="端口：", width=6).pack(side="left")
        self._proxy_port = ttk.Entry(row1, width=8, font=("Consolas", 10))
        self._proxy_port.insert(0, self.cfg.get("proxy_port", "1080"))
        self._proxy_port.pack(side="left")

        # 用户名 + 密码（可选）
        row2 = tk.Frame(self._proxy_fields_frame, bg="#1e1e2e")
        row2.pack(fill="x", pady=3)
        ttk.Label(row2, text="用户名：", width=8).pack(side="left")
        self._proxy_user = ttk.Entry(row2, width=20, font=("Consolas", 10))
        self._proxy_user.insert(0, self.cfg.get("proxy_user", ""))
        self._proxy_user.pack(side="left", padx=(0, 16))
        ttk.Label(row2, text="密码：", width=6).pack(side="left")
        self._proxy_pass = ttk.Entry(row2, width=16, font=("Consolas", 10), show="*")
        self._proxy_pass.insert(0, self.cfg.get("proxy_pass", ""))
        self._proxy_pass.pack(side="left")

        tk.Label(proxy_frame,
                 text="支持 v2ray / Clash / SS 等本地 SOCKS5 代理，默认端口 1080",
                 bg="#1e1e2e", fg="#6c7086", font=("Microsoft YaHei", 9)).pack(anchor="w", pady=(8, 0))

        self._toggle_proxy_fields()

        # 保存按钮
        ttk.Button(f, text="💾  保存设置", style="Accent.TButton",
                   command=self._save_settings).pack(pady=16, ipadx=30)

    def _toggle_proxy_fields(self):
        state = "normal" if self._proxy_enabled.get() else "disabled"
        for child in self._proxy_fields_frame.winfo_children():
            for w in ([child] + child.winfo_children()):
                try:
                    w.configure(state=state)
                except Exception:
                    pass

    def _browse_dir(self):
        d = filedialog.askdirectory(title="选择保存目录", parent=self)
        if d:
            self._out_dir_var.set(d)

    def _browse_ffmpeg(self):
        f = filedialog.askopenfilename(
            title="选择 ffmpeg 可执行文件",
            filetypes=[("ffmpeg", "ffmpeg.exe"), ("所有文件", "*.*")],
            parent=self
        )
        if f:
            self._ffmpeg_var.set(f)

    def _save_settings(self):
        self.cfg["output_dir"]     = self._out_dir_var.get().strip()
        self.cfg["ffmpeg_path"]    = self._ffmpeg_var.get().strip()
        self.cfg["proxy_enabled"]  = self._proxy_enabled.get()
        self.cfg["proxy_host"]     = self._proxy_host.get().strip()
        self.cfg["proxy_port"]     = self._proxy_port.get().strip()
        self.cfg["proxy_user"]     = self._proxy_user.get().strip()
        self.cfg["proxy_pass"]     = self._proxy_pass.get().strip()
        save_config(self.cfg)
        # 同步到爬取页目录标签
        self._out_dir_label.config(text=self.cfg["output_dir"])
        messagebox.showinfo("保存成功", "设置已保存！", parent=self)
        log.info(f"设置已保存: 输出目录={self.cfg['output_dir']}, 代理={'开' if self.cfg['proxy_enabled'] else '关'}")

    def _change_output_dir(self):
        d = filedialog.askdirectory(title="选择保存目录", parent=self)
        if d:
            self.cfg["output_dir"] = d
            self._out_dir_label.config(text=d)
            self._out_dir_var.set(d)
            save_config(self.cfg)

    # ── 日志 Tab ────────────────────────────────
    def _build_tab_log(self):
        f = self._tab_log
        self._log_text = scrolledtext.ScrolledText(
            f, bg="#11111b", fg="#cdd6f4", insertbackground="#cdd6f4",
            font=("Consolas", 9), state="disabled", relief="flat"
        )
        self._log_text.pack(fill="both", expand=True, padx=4, pady=4)

        # 颜色标签
        self._log_text.tag_config("INFO",    foreground="#cdd6f4")
        self._log_text.tag_config("WARNING", foreground="#f9e2af")
        self._log_text.tag_config("ERROR",   foreground="#f38ba8")
        self._log_text.tag_config("SUCCESS", foreground="#a6e3a1")

        btn_row = tk.Frame(f, bg="#1e1e2e")
        btn_row.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(btn_row, text="清空", command=self._clear_log).pack(side="right", padx=4)
        ttk.Button(btn_row, text="📋 复制全部", command=self._copy_log).pack(side="right", padx=4)

    def _start_log_poll(self):
        """每 200ms 从队列读取日志写入文本框"""
        while not log_queue.empty():
            msg = log_queue.get_nowait()
            self._append_log(msg)
        self.after(200, self._start_log_poll)

    def _append_log(self, msg: str):
        tag = "INFO"
        ml = msg.upper()
        if "WARNING" in ml or "警告" in ml:
            tag = "WARNING"
        elif "ERROR" in ml or "失败" in ml or "错误" in ml:
            tag = "ERROR"
        elif "完成" in msg or "成功" in msg or "✓" in msg:
            tag = "SUCCESS"

        self._log_text.configure(state="normal")
        self._log_text.insert("end", msg + "\n", tag)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _clear_log(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _copy_log(self):
        content = self._log_text.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(content)

    # ── 任务调度 ────────────────────────────────
    def _set_status(self, text: str):
        self.after(0, lambda: self._status_var.set(text))

    def _set_progress(self, text: str, running: bool = True):
        def _upd():
            self._prog_label.config(text=text)
            if running:
                self._progress.start(10)
                self._stop_btn.config(state="normal")
            else:
                self._progress.stop()
                self._stop_btn.config(state="disabled")
        self.after(0, _upd)

    def _stop_task(self):
        self._stop_event.set()
        log.info("用户请求停止任务...")
        self._set_status("正在停止...")

    def _open_output_dir(self):
        d = self.cfg.get("output_dir", "")
        if d and os.path.isdir(d):
            os.startfile(d) if sys.platform == "win32" else subprocess.Popen(["xdg-open", d])
        else:
            messagebox.showwarning("提示", f"目录不存在: {d}", parent=self)

    def _get_proxy_args(self) -> dict:
        """返回代理配置字典（用于 Selenium 和 requests）"""
        if not self.cfg.get("proxy_enabled"):
            return {}
        host = self.cfg.get("proxy_host", "127.0.0.1")
        port = self.cfg.get("proxy_port", "1080")
        user = self.cfg.get("proxy_user", "")
        pwd  = self.cfg.get("proxy_pass", "")
        auth = f"{user}:{pwd}@" if user else ""
        return {
            "socks5": f"socks5://{auth}{host}:{port}",
            "host": host, "port": int(port),
            "user": user, "pass": pwd,
        }

    # ── 批量爬取启动 ────────────────────────────
    def _start_crawl(self):
        if self._task_thread and self._task_thread.is_alive():
            messagebox.showwarning("提示", "已有任务在运行中", parent=self)
            return

        # 收集参数
        try:
            start  = int(self._page_start.get())
            end    = int(self._page_end.get())
            wait   = int(self._sniff_wait.get())
        except ValueError:
            messagebox.showerror("参数错误", "页码和等待时间必须为整数", parent=self)
            return

        self.cfg["list_type"]   = self._list_type.get()
        self.cfg["page_start"]  = start
        self.cfg["page_end"]    = end
        self.cfg["sniff_wait"]  = wait
        self.cfg["headless"]    = self._headless_var.get()
        save_config(self.cfg)

        self._stop_event.clear()
        self._set_progress("爬取中...", True)
        self._set_status("批量下载运行中...")

        proxy = self._get_proxy_args()
        self._task_thread = threading.Thread(
            target=self._crawl_worker,
            args=(self.cfg["list_type"], start, end, wait,
                  self.cfg["headless"], self.cfg["output_dir"],
                  self.cfg.get("ffmpeg_path",""), proxy),
            daemon=True
        )
        self._task_thread.start()

    def _crawl_worker(self, list_type, start, end, wait, headless, output_dir, ffmpeg_path, proxy):
        try:
            from crawler_core import CrawlerCore
            core = CrawlerCore(
                output_dir=output_dir,
                ffmpeg_path=ffmpeg_path or None,
                headless=headless,
                sniff_wait=wait,
                proxy=proxy,
                stop_event=self._stop_event,
                progress_cb=self._on_crawl_progress,
            )
            core.crawl_list(list_type, start, end)
        except ImportError as e:
            log.error(f"爬虫核心模块加载失败: {e}，请先完成依赖安装")
        except Exception as e:
            log.error(f"爬取过程出错: {e}", exc_info=True)
        finally:
            self.after(0, lambda: self._set_progress("", False))
            self.after(0, lambda: self._set_status("任务结束"))

    def _on_crawl_progress(self, current: int, total: int, title: str):
        def _upd():
            self._crawl_info.config(text=f"[{current}/{total}]  {title}")
            self._prog_label.config(text=f"{current}/{total}")
        self.after(0, _upd)

    # ── 单视频启动 ──────────────────────────────
    def _start_single(self):
        urls_raw = self._single_urls.get("1.0", "end").strip()
        urls = [u.strip() for u in urls_raw.splitlines() if u.strip()]
        if not urls:
            messagebox.showwarning("提示", "请输入至少一个视频页 URL", parent=self)
            return
        if self._task_thread and self._task_thread.is_alive():
            messagebox.showwarning("提示", "已有任务在运行中", parent=self)
            return

        try:
            wait = int(self._single_wait.get())
        except ValueError:
            wait = 15

        self._stop_event.clear()
        self._set_progress("嗅探中...", True)
        self._set_status("单视频下载中...")

        proxy = self._get_proxy_args()
        self._task_thread = threading.Thread(
            target=self._single_worker,
            args=(urls, wait, self._single_headless.get(),
                  self.cfg["output_dir"], self.cfg.get("ffmpeg_path",""), proxy),
            daemon=True
        )
        self._task_thread.start()

    def _single_worker(self, urls, wait, headless, output_dir, ffmpeg_path, proxy):
        try:
            from crawler_core import CrawlerCore
            core = CrawlerCore(
                output_dir=output_dir,
                ffmpeg_path=ffmpeg_path or None,
                headless=headless,
                sniff_wait=wait,
                proxy=proxy,
                stop_event=self._stop_event,
                progress_cb=None,
            )
            for i, url in enumerate(urls, 1):
                if self._stop_event.is_set():
                    break
                log.info(f"[{i}/{len(urls)}] 处理: {url}")
                core.process_single(url)
        except ImportError as e:
            log.error(f"爬虫核心模块加载失败: {e}，请先完成依赖安装")
        except Exception as e:
            log.error(f"处理出错: {e}", exc_info=True)
        finally:
            self.after(0, lambda: self._set_progress("", False))
            self.after(0, lambda: self._set_status("任务结束"))

    # ── 直接 m3u8 下载 ──────────────────────────
    def _start_m3u8(self):
        url   = self._m3u8_url.get().strip()
        title = self._m3u8_title.get().strip() or "视频"
        if not url:
            messagebox.showwarning("提示", "请输入 m3u8 URL", parent=self)
            return
        if self._task_thread and self._task_thread.is_alive():
            messagebox.showwarning("提示", "已有任务在运行中", parent=self)
            return

        self._set_progress("转码中...", True)
        proxy = self._get_proxy_args()

        def _worker():
            try:
                from crawler_core import CrawlerCore
                core = CrawlerCore(
                    output_dir=self.cfg["output_dir"],
                    ffmpeg_path=self.cfg.get("ffmpeg_path") or None,
                    proxy=proxy,
                    stop_event=self._stop_event,
                )
                core.download_m3u8_direct(url, title)
            except Exception as e:
                log.error(f"转码失败: {e}", exc_info=True)
            finally:
                self.after(0, lambda: self._set_progress("", False))
        threading.Thread(target=_worker, daemon=True).start()


# ──────────────────────────────────────────────
#  主入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
