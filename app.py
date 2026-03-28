#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ml0987.xyz 视频下载器 - GUI 版本
"""

import os
import sys
import json
import logging
import threading
import subprocess
import queue
import time
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:
    print("错误: tkinter 不可用，请安装 Python 完整版")
    sys.exit(1)

# ==================== 配置 ====================

APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "config.json"
PROGRESS_FILE = APP_DIR / "progress.json"
LOG_FILE = APP_DIR / "app.log"

DEFAULT_CONFIG = {
    "output_dir": str(APP_DIR / "downloads"),
    "ffmpeg_path": "",
    "proxy_enabled": False,
    "proxy_host": "127.0.0.1",
    "proxy_port": "1080",
    "proxy_user": "",
    "proxy_pass": "",
    "headless": True,
}

# ==================== 日志 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ==================== 配置读写 ====================

def load_config() -> dict:
    """加载配置文件"""
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            logger.warning(f"加载配置失败: {e}")
    return cfg

def save_config(cfg: dict):
    """保存配置文件"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存配置失败: {e}")

# ==================== GUI 主界面 ====================

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("ml0987 视频下载器")
        self.root.geometry("900x650")
        
        # 加载配置
        self.config = load_config()
        
        # 创建 UI
        self._create_widgets()
        
        # 检查环境
        self._check_environment()
    
    def _create_widgets(self):
        """创建界面组件"""
        # 创建 Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)
        
        # 添加 Tab 页
        self.tab_env = ttk.Frame(self.notebook)
        self.tab_crawl = ttk.Frame(self.notebook)
        self.tab_single = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)
        self.tab_log = ttk.Frame(self.notebook)
        
        self.notebook.add(self.tab_env, text="  ✅ 环境检测  ")
        self.notebook.add(self.tab_crawl, text="  📋 批量爬取  ")
        self.notebook.add(self.tab_single, text="  🔗 单视频  ")
        self.notebook.add(self.tab_settings, text="  ⚙️ 设置  ")
        self.notebook.add(self.tab_log, text="  📄 日志  ")
        
        # 构建各 Tab
        self._build_tab_env()
        self._build_tab_crawl()
        self._build_tab_single()
        self._build_tab_settings()
        self._build_tab_log()
    
    def _build_tab_env(self):
        """环境检测 Tab"""
        ttk.Label(self.tab_env, text="运行环境检查", font=("Arial", 14, "bold")).pack(pady=20)
        
        # 检查结果框
        result_frame = ttk.LabelFrame(self.tab_env, text="检查结果", padding=10)
        result_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.env_status_text = scrolledtext.ScrolledText(result_frame, height=15, wrap="word")
        self.env_status_text.pack(fill="both", expand=True)
        
        # 按钮
        btn_frame = ttk.Frame(self.tab_env)
        btn_frame.pack(fill="x", padx=20, pady=10)
        
        ttk.Button(btn_frame, text="🔍 重新检查", command=self._check_environment).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="📦 安装 Python 依赖", command=self._install_deps).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="🌐 下载 ffmpeg", command=self._download_ffmpeg).pack(side="left", padx=5)
    
    def _build_tab_crawl(self):
        """批量爬取 Tab"""
        ttk.Label(self.tab_crawl, text="批量爬取视频", font=("Arial", 14, "bold")).pack(pady=20)
        
        # 控制面板
        control_frame = ttk.LabelFrame(self.tab_crawl, text="爬取设置", padding=10)
        control_frame.pack(fill="x", padx=20, pady=10)
        
        # 页码范围
        page_frame = ttk.Frame(control_frame)
        page_frame.pack(fill="x", pady=5)
        ttk.Label(page_frame, text="起始页码:").pack(side="left")
        self.page_start_var = tk.IntVar(value=1)
        ttk.Spinbox(page_frame, from_=1, to=100, textvariable=self.page_start_var, width=5).pack(side="left", padx=5)
        ttk.Label(page_frame, text="结束页码:").pack(side="left")
        self.page_end_var = tk.IntVar(value=3)
        ttk.Spinbox(page_frame, from_=1, to=100, textvariable=self.page_end_var, width=5).pack(side="left", padx=5)
        
        # 按钮
        btn_frame = ttk.Frame(self.tab_crawl)
        btn_frame.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_frame, text="🚀 开始爬取", command=self._start_crawl).pack(side="left", padx=5)
        
        # 进度显示
        progress_frame = ttk.LabelFrame(self.tab_crawl, text="进度", padding=10)
        progress_frame.pack(fill="both", expand=True, padx=20, pady=10)
        self.crawl_progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.crawl_progress.pack(fill="x", pady=5)
        self.crawl_status_text = scrolledtext.ScrolledText(progress_frame, height=10, wrap="word")
        self.crawl_status_text.pack(fill="both", expand=True)
    
    def _build_tab_single(self):
        """单视频 Tab"""
        ttk.Label(self.tab_single, text="单个视频下载", font=("Arial", 14, "bold")).pack(pady=20)
        
        # URL 输入
        url_frame = ttk.LabelFrame(self.tab_single, text="视频 URL", padding=10)
        url_frame.pack(fill="x", padx=20, pady=10)
        
        self.url_var = tk.StringVar()
        ttk.Entry(url_frame, textvariable=self.url_var, width=60).pack(fill="x", padx=5, pady=5)
        
        # 按钮
        btn_frame = ttk.Frame(self.tab_single)
        btn_frame.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_frame, text="🚀 开始下载", command=self._start_single).pack(side="left", padx=5)
        
        # 进度显示
        progress_frame = ttk.LabelFrame(self.tab_single, text="进度", padding=10)
        progress_frame.pack(fill="both", expand=True, padx=20, pady=10)
        self.single_progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.single_progress.pack(fill="x", pady=5)
        self.single_status_text = scrolledtext.ScrolledText(progress_frame, height=10, wrap="word")
        self.single_status_text.pack(fill="both", expand=True)
    
    def _build_tab_settings(self):
        """设置 Tab"""
        ttk.Label(self.tab_settings, text="应用设置", font=("Arial", 14, "bold")).pack(pady=20)
        
        # 保存目录
        dir_frame = ttk.LabelFrame(self.tab_settings, text="保存目录", padding=10)
        dir_frame.pack(fill="x", padx=20, pady=10)
        
        ttk.Label(dir_frame, text="下载保存到:").pack(anchor="w")
        self.save_dir_var = tk.StringVar(value=self.config["output_dir"])
        entry = ttk.Entry(dir_frame, textvariable=self.save_dir_var)
        entry.pack(fill="x", padx=5, pady=5)
        ttk.Button(dir_frame, text="选择目录...", command=self._browse_dir).pack(anchor="w", padx=5, pady=5)
        
        # 代理设置
        proxy_frame = ttk.LabelFrame(self.tab_settings, text="SOCKS5 代理", padding=10)
        proxy_frame.pack(fill="x", padx=20, pady=10)
        
        self.proxy_enabled_var = tk.BooleanVar(value=self.config["proxy_enabled"])
        ttk.Checkbutton(proxy_frame, text="启用代理", variable=self.proxy_enabled_var).pack(anchor="w", padx=5, pady=5)
        
        ttk.Label(proxy_frame, text="主机:").pack(anchor="w", padx=5)
        self.proxy_host_var = tk.StringVar(value=self.config["proxy_host"])
        ttk.Entry(proxy_frame, textvariable=self.proxy_host_var).pack(fill="x", padx=5, pady=5)
        
        ttk.Label(proxy_frame, text="端口:").pack(anchor="w", padx=5)
        self.proxy_port_var = tk.StringVar(value=self.config["proxy_port"])
        ttk.Entry(proxy_frame, textvariable=self.proxy_port_var).pack(fill="x", padx=5, pady=5)
        
        # 保存按钮
        btn_frame = ttk.Frame(self.tab_settings)
        btn_frame.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_frame, text="💾 保存设置", command=self._save_settings).pack(side="left", padx=5)
    
    def _build_tab_log(self):
        """日志 Tab"""
        ttk.Label(self.tab_log, text="运行日志", font=("Arial", 14, "bold")).pack(pady=20)
        
        log_frame = ttk.Frame(self.tab_log)
        log_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, wrap="word")
        self.log_text.pack(fill="both", expand=True)
        
        btn_frame = ttk.Frame(self.tab_log)
        btn_frame.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_frame, text="🗑️ 清空日志", command=self._clear_log).pack(side="left", padx=5)
    
    # ==================== 功能方法 ====================
    
    def _check_environment(self):
        """检查运行环境"""
        self.env_status_text.delete(1.0, tk.END)
        
        # 检查 Python 依赖
        missing_deps = []
        for pkg in ["selenium", "webdriver_manager", "bs4", "lxml", "requests", "PySocks", "pycryptodome"]:
            try:
                __import__(pkg.replace("-", "_"))
            except ImportError:
                missing_deps.append(pkg)
        
        self._append_status(f"✓ Python 依赖检查:", "OK" if not missing_deps else "FAIL")
        if missing_deps:
            self._append_status(f"  缺失: {', '.join(missing_deps)}", "FAIL")
        else:
            self._append_status("  所有依赖已安装", "OK")
        
        # 检查 Chrome
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
        ]
        chrome_found = any(Path(p).exists() for p in chrome_paths)
        self._append_status(f"✓ Chrome 浏览器:", "OK" if chrome_found else "FAIL")
        if not chrome_found:
            self._append_status("  未找到 Chrome", "FAIL")
        
        # 检查 ffmpeg
        ffmpeg_path = self.config["ffmpeg_path"]
        if not ffmpeg_path or not Path(ffmpeg_path).exists():
            try:
                subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
                ffmpeg_found = True
                ffmpeg_path = "系统 PATH"
            except:
                ffmpeg_found = False
        else:
            ffmpeg_found = True
        
        self._append_status(f"✓ ffmpeg:", "OK" if ffmpeg_found else "FAIL")
        if not ffmpeg_found:
            self._append_status("  未找到 ffmpeg", "FAIL")
        else:
            self._append_status(f"  路径: {ffmpeg_path}", "OK")
        
        return not missing_deps and chrome_found and ffmpeg_found
    
    def _append_status(self, text, status):
        """追加状态信息"""
        color = {"OK": "green", "FAIL": "red", "WARN": "orange"}.get(status, "black")
        tag = f"status_{status}"
        self.env_status_text.tag_config(tag, foreground=color)
        self.env_status_text.insert(tk.END, text + "\n", tag)
        self.env_status_text.see(tk.END)
    
    def _install_deps(self):
        """安装 Python 依赖"""
        self.env_status_text.insert(tk.END, "\n正在安装依赖...\n")
        self.root.update()
        
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(APP_DIR / "requirements.txt")],
                capture_output=True,
                text=True
            )
            
            self.env_status_text.insert(tk.END, result.stdout)
            if result.returncode == 0:
                self.env_status_text.insert(tk.END, "\n✓ 依赖安装成功\n")
            else:
                self.env_status_text.insert(tk.END, f"\n✗ 安装失败: {result.stderr}\n")
            
            self._check_environment()
        except Exception as e:
            self.env_status_text.insert(tk.END, f"\n✗ 安装失败: {e}\n")
    
    def _download_ffmpeg(self):
        """下载 ffmpeg"""
        import webbrowser
        webbrowser.open("https://ffmpeg.org/download.html")
        messagebox.showinfo("下载 ffmpeg", "请在浏览器中下载 ffmpeg 并解压到指定目录")
    
    def _browse_dir(self):
        """浏览目录"""
        path = filedialog.askdirectory()
        if path:
            self.save_dir_var.set(path)
    
    def _save_settings(self):
        """保存设置"""
        self.config["output_dir"] = self.save_dir_var.get()
        self.config["proxy_enabled"] = self.proxy_enabled_var.get()
        self.config["proxy_host"] = self.proxy_host_var.get()
        self.config["proxy_port"] = self.proxy_port_var.get()
        save_config(self.config)
        messagebox.showinfo("保存成功", "设置已保存")
    
    def _clear_log(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)
    
    def _start_crawl(self):
        """开始批量爬取"""
        messagebox.showinfo("提示", "批量爬取功能需要 crawler_core.py 支持")
    
    def _start_single(self):
        """开始单个下载"""
        messagebox.showinfo("提示", "单个下载功能需要 crawler_core.py 支持")
    
    def _log_to_ui(self, message):
        """记录日志到 UI"""
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        logger.info(message)

def main():
    """主函数"""
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
