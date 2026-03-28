#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ml0987 视频下载器 - GUI 版本
"""

import os
import sys
import json
import logging
import logging.handlers
import threading
import time
import io
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:
    print("错误: tkinter 不可用，请安装 Python 完整版")
    sys.exit(1)

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from crawler_core import CrawlerCore

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
    "list_type": "list",
    "page_start": 1,
    "page_end": 3,
}

# ==================== 日志 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=3,
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ==================== 工具函数 ====================

def get_app_dir() -> Path:
    """获取程序所在目录（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return APP_DIR


def get_ffmpeg_path() -> Path:
    """获取 ffmpeg.exe 路径"""
    return get_app_dir() / "ffmpeg.exe"


def download_image(url: str, timeout: int = 10) -> bytes:
    """下载图片并返回 bytes"""
    try:
        import requests
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://ml0987.xyz/"})
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return b""


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
        self.root.geometry("1000x720")
        self.root.minsize(800, 600)

        # 加载配置
        self.config = load_config()

        # 爬虫核心
        self.crawler = None
        self.crawl_thread = None

        # 封面图片缓存
        self._cover_photo = None  # 保持引用防止 GC

        # 创建 UI
        self._create_widgets()

        # 检查环境
        self._check_environment()

        # 检查 ffmpeg.exe
        self.check_ffmpeg()

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

        self.notebook.add(self.tab_env, text="  环境检测  ")
        self.notebook.add(self.tab_crawl, text="  批量爬取  ")
        self.notebook.add(self.tab_single, text="  单视频  ")
        self.notebook.add(self.tab_settings, text="  设置  ")
        self.notebook.add(self.tab_log, text="  日志  ")

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

        ttk.Button(btn_frame, text="重新检查", command=self._check_environment).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="安装 Python 依赖", command=self._install_deps).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="下载 ffmpeg", command=self._download_ffmpeg).pack(side="left", padx=5)

    def _build_tab_crawl(self):
        """批量爬取 Tab"""
        ttk.Label(self.tab_crawl, text="批量爬取视频", font=("Arial", 14, "bold")).pack(pady=10)

        # 控制面板
        control_frame = ttk.LabelFrame(self.tab_crawl, text="爬取设置", padding=10)
        control_frame.pack(fill="x", padx=20, pady=5)

        # 列表类型
        type_frame = ttk.Frame(control_frame)
        type_frame.pack(fill="x", pady=3)
        ttk.Label(type_frame, text="列表类型:").pack(side="left")
        self.list_type_var = tk.StringVar(value=self.config.get("list_type", "list"))
        type_combo = ttk.Combobox(type_frame, textvariable=self.list_type_var,
                                  values=["视频", "周榜", "月榜", "5分钟+", "10分钟+"],
                                  width=10, state="readonly")
        type_combo.pack(side="left", padx=5)

        # 页码范围
        page_frame = ttk.Frame(control_frame)
        page_frame.pack(fill="x", pady=3)
        ttk.Label(page_frame, text="起始页码:").pack(side="left")
        self.page_start_var = tk.IntVar(value=self.config["page_start"])
        ttk.Spinbox(page_frame, from_=1, to=100, textvariable=self.page_start_var, width=5).pack(side="left", padx=5)
        ttk.Label(page_frame, text="结束页码:").pack(side="left")
        self.page_end_var = tk.IntVar(value=self.config["page_end"])
        ttk.Spinbox(page_frame, from_=1, to=100, textvariable=self.page_end_var, width=5).pack(side="left", padx=5)

        # 按钮
        btn_frame = ttk.Frame(self.tab_crawl)
        btn_frame.pack(fill="x", padx=20, pady=5)
        ttk.Button(btn_frame, text="开始爬取", command=self._start_crawl).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="停止", command=self._stop_crawl).pack(side="left", padx=5)

        # ===== 下方区域：左边封面预览 + 右边进度日志 =====
        bottom_frame = ttk.Frame(self.tab_crawl)
        bottom_frame.pack(fill="both", expand=True, padx=20, pady=5)

        # 左侧：封面预览
        cover_frame = ttk.LabelFrame(bottom_frame, text="当前视频预览", padding=5)
        cover_frame.pack(side="left", fill="y", padx=(0, 10))

        # 固定宽度
        cover_frame.configure(width=220)
        cover_frame.pack_propagate(False)

        self.cover_label = tk.Label(cover_frame, text="等待爬取...", bg="#f0f0f0",
                                     width=22, height=13, anchor="center",
                                     fg="#999", font=("Arial", 10))
        self.cover_label.pack(fill="both", expand=True)

        self.preview_title_label = tk.Label(cover_frame, text="",
                                             wraplength=200, justify="left",
                                             font=("Arial", 9))
        self.preview_title_label.pack(fill="x", pady=(5, 0))

        # 右侧：进度和日志
        right_frame = ttk.LabelFrame(bottom_frame, text="进度", padding=5)
        right_frame.pack(side="left", fill="both", expand=True)

        self.crawl_progress = ttk.Progressbar(right_frame, mode="determinate")
        self.crawl_progress.pack(fill="x", pady=(0, 5))

        self.crawl_status_text = scrolledtext.ScrolledText(right_frame, height=12, wrap="word",
                                                            font=("Consolas", 9))
        self.crawl_status_text.pack(fill="both", expand=True)

    def _build_tab_single(self):
        """单视频 Tab"""
        ttk.Label(self.tab_single, text="单个视频下载", font=("Arial", 14, "bold")).pack(pady=20)

        # URL 输入
        url_frame = ttk.LabelFrame(self.tab_single, text="视频 URL", padding=10)
        url_frame.pack(fill="x", padx=20, pady=10)

        self.url_var = tk.StringVar()
        ttk.Entry(url_frame, textvariable=self.url_var, width=60).pack(fill="x", padx=5, pady=5)

        # 标题输入
        title_frame = ttk.LabelFrame(self.tab_single, text="视频标题（可选）", padding=10)
        title_frame.pack(fill="x", padx=20, pady=10)

        self.title_var = tk.StringVar()
        ttk.Entry(title_frame, textvariable=self.title_var, width=60).pack(fill="x", padx=5, pady=5)

        # 按钮
        btn_frame = ttk.Frame(self.tab_single)
        btn_frame.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_frame, text="开始下载", command=self._start_single).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="停止", command=self._stop_crawl).pack(side="left", padx=5)

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
        proxy_frame = ttk.LabelFrame(self.tab_settings, text="SOCKS5 代理（可选）", padding=10)
        proxy_frame.pack(fill="x", padx=20, pady=10)

        # 代理启用复选框
        self.proxy_enabled_var = tk.BooleanVar(value=self.config.get("proxy_enabled", False))
        ttk.Checkbutton(proxy_frame, text="启用代理", variable=self.proxy_enabled_var).pack(anchor="w", padx=5, pady=5)

        ttk.Label(proxy_frame, text="主机:").pack(anchor="w", padx=5)
        self.proxy_host_var = tk.StringVar(value=self.config["proxy_host"])
        ttk.Entry(proxy_frame, textvariable=self.proxy_host_var).pack(fill="x", padx=5, pady=5)

        ttk.Label(proxy_frame, text="端口:").pack(anchor="w", padx=5)
        self.proxy_port_var = tk.StringVar(value=self.config["proxy_port"])
        ttk.Entry(proxy_frame, textvariable=self.proxy_port_var).pack(fill="x", padx=5, pady=5)

        # 账号密码
        auth_frame = ttk.Frame(proxy_frame)
        auth_frame.pack(fill="x", padx=5, pady=5)
        ttk.Label(auth_frame, text="账号（可选）:").pack(anchor="w")
        self.proxy_user_var = tk.StringVar(value=self.config["proxy_user"])
        ttk.Entry(auth_frame, textvariable=self.proxy_user_var).pack(fill="x")
        ttk.Label(auth_frame, text="密码（可选）:").pack(anchor="w")
        self.proxy_pass_var = tk.StringVar(value=self.config["proxy_pass"])
        ttk.Entry(auth_frame, textvariable=self.proxy_pass_var, show="*").pack(fill="x")

        # 保存按钮
        btn_frame = ttk.Frame(self.tab_settings)
        btn_frame.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_frame, text="保存设置", command=self._save_settings).pack(side="left", padx=5)

    def _build_tab_log(self):
        """日志 Tab"""
        ttk.Label(self.tab_log, text="运行日志", font=("Arial", 14, "bold")).pack(pady=20)

        log_frame = ttk.Frame(self.tab_log)
        log_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, wrap="word")
        self.log_text.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(self.tab_log)
        btn_frame.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_frame, text="清空日志", command=self._clear_log).pack(side="left", padx=5)

    # ==================== 封面预览 ====================

    def _update_cover_preview(self, info: dict):
        """在线程中下载封面，然后通过 root.after 更新 UI"""
        cover_url = info.get("cover", "")
        title = info.get("title", "")

        # 先更新标题（线程安全）
        try:
            self.root.after(0, lambda: self.preview_title_label.config(text=title))
        except Exception:
            pass

        if not cover_url:
            return

        # 在子线程下载图片
        img_data = download_image(cover_url)
        if not img_data:
            return

        # 调度到主线程显示图片
        def show_image():
            try:
                if HAS_PIL:
                    # 用 PIL 缩放图片
                    img = Image.open(io.BytesIO(img_data))
                    # 目标尺寸
                    w, h = 200, 130
                    img.thumbnail((w, h), Image.LANCZOS)
                    self._cover_photo = ImageTk.PhotoImage(img)
                    self.cover_label.config(image=self._cover_photo, text="", bg="white")
                else:
                    # 没有 PIL，直接显示原始数据（仅支持 GIF/PNG/PGM/PPM）
                    self._cover_photo = tk.PhotoImage(data=img_data)
                    self.cover_label.config(image=self._cover_photo, text="", bg="white")
            except Exception:
                # webp 等格式 tkinter 无法直接显示
                self.cover_label.config(image="", text="封面加载失败\n(需要 Pillow)", bg="#f0f0f0")

        try:
            self.root.after(0, show_image)
        except Exception:
            pass

    # ==================== 功能方法 ====================

    def _check_environment(self):
        """检查运行环境"""
        self.env_status_text.delete(1.0, tk.END)

        # 检测 ffmpeg.exe
        ffmpeg_path = get_ffmpeg_path()
        ffmpeg_found = ffmpeg_path.exists()

        self._append_status(f"ffmpeg:", "OK" if ffmpeg_found else "FAIL")
        if not ffmpeg_found:
            self._append_status(f"  请将 ffmpeg.exe 放置于: {ffmpeg_path.parent}", "WARN")
        else:
            self._append_status(f"  路径: {ffmpeg_path}", "OK")

        # 检测 requests
        try:
            import requests
            self._append_status(f"requests: OK (v{requests.__version__})", "OK")
        except ImportError:
            self._append_status(f"requests: 未安装", "FAIL")

        # 检测 Pillow
        if HAS_PIL:
            self._append_status(f"Pillow: OK (封面预览可用)", "OK")
        else:
            self._append_status(f"Pillow: 未安装 (封面预览不可用，可运行 pip install Pillow)", "WARN")

        # 检测 pycryptodome
        try:
            from Crypto.Cipher import AES
            self._append_status(f"pycryptodome: OK (AES 解密可用)", "OK")
        except ImportError:
            self._append_status(f"pycryptodome: 未安装 (加密视频无法下载)", "WARN")

        return ffmpeg_found

    def check_ffmpeg(self):
        """检查 ffmpeg.exe 是否存在，缺失时询问用户是否下载"""
        ffmpeg_path = get_ffmpeg_path()

        if not ffmpeg_path.exists():
            result = messagebox.askyesno(
                "缺少 ffmpeg.exe",
                f"检测到程序目录下缺少 ffmpeg.exe 文件。\n\n"
                f"程序需要 ffmpeg.exe 才能正常工作。\n\n"
                f"请将 ffmpeg.exe 放置于: {ffmpeg_path.parent}\n\n"
                f"是否跳转到 ffmpeg 官网下载？"
            )
            if result:
                import webbrowser
                webbrowser.open("https://ffmpeg.org/download.html")
        return ffmpeg_path.exists()

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
            import subprocess
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(get_app_dir() / "requirements.txt")],
                capture_output=True, text=True
            )

            self.env_status_text.insert(tk.END, result.stdout)
            if result.returncode == 0:
                self.env_status_text.insert(tk.END, "\n依赖安装成功\n")
            else:
                self.env_status_text.insert(tk.END, f"\n安装失败: {result.stderr}\n")

            self._check_environment()
        except Exception as e:
            self.env_status_text.insert(tk.END, f"\n安装失败: {e}\n")

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
        self.config["proxy_user"] = self.proxy_user_var.get()
        self.config["proxy_pass"] = self.proxy_pass_var.get()
        save_config(self.config)
        messagebox.showinfo("保存成功", "设置已保存")

    def _clear_log(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)

    def _log_to_ui(self, text, level="info"):
        """记录日志到 UI（线程安全，通过 root.after 调度到主线程）"""
        timestamp = time.strftime("%H:%M:%S")
        prefix = {"error": "✗", "warn": "⚠", "info": "ℹ"}.get(level, "·")
        line = f"[{timestamp}] {prefix} {text}\n"
        try:
            self.root.after(0, lambda: self._append_log(line))
        except Exception:
            pass
        logger.info(text)

    def _append_log(self, line):
        """在主线程中安全追加日志"""
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)

    def _status_to_ui(self, text_widget, text):
        """记录状态到指定文本框（线程安全）"""
        if text_widget is None:
            return
        try:
            self.root.after(0, lambda: self._append_text(text_widget, text))
        except Exception:
            pass

    def _append_text(self, widget, text):
        """在主线程中安全追加文本到指定控件"""
        widget.insert(tk.END, f"{text}\n")
        widget.see(tk.END)

    def _start_crawl(self):
        """开始批量爬取"""
        if self.crawl_thread and self.crawl_thread.is_alive():
            messagebox.showwarning("警告", "正在运行中，请先停止")
            return

        # 初始化爬虫
        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_ui,
            progress_callback=lambda c, t: self._update_progress(self.crawl_progress, c, t),
            info_callback=self._update_cover_preview,
        )

        # 在新线程中运行
        def run():
            try:
                self.crawler.crawl_batch(
                    page_start=self.page_start_var.get(),
                    page_end=self.page_end_var.get(),
                    list_type=self.list_type_var.get()
                )
                self._status_to_ui(self.crawl_status_text, "批量爬取完成")
            except Exception as e:
                self._status_to_ui(self.crawl_status_text, f"错误: {e}")
                logger.exception("批量爬取失败")

        self.crawl_thread = threading.Thread(target=run)
        self.crawl_thread.daemon = True
        self.crawl_thread.start()

    def _start_single(self):
        """开始单个下载"""
        if self.crawl_thread and self.crawl_thread.is_alive():
            messagebox.showwarning("警告", "正在运行中，请先停止")
            return

        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "请输入视频 URL")
            return

        title = self.title_var.get().strip() or None

        # 初始化爬虫
        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_ui,
            progress_callback=lambda c, t: self._update_progress(self.single_progress, c, t)
        )

        # 在新线程中运行
        def run():
            try:
                self.crawler.download_single(url, title)
                self._status_to_ui(self.single_status_text, "下载完成")
            except Exception as e:
                self._status_to_ui(self.single_status_text, f"错误: {e}")
                logger.exception("单个下载失败")

        self.crawl_thread = threading.Thread(target=run)
        self.crawl_thread.daemon = True
        self.crawl_thread.start()

    def _stop_crawl(self):
        """停止任务"""
        if self.crawler:
            self.crawler.stop()
            if self.crawl_thread and self.crawl_thread.is_alive():
                self.crawl_thread.join(timeout=5)
            self._status_to_ui(self.crawl_status_text, "已停止")
            self._status_to_ui(self.single_status_text, "已停止")

    def _update_progress(self, progressbar, current, total):
        """更新进度条（线程安全）"""
        if total > 0:
            percent = (current / total) * 100
            try:
                self.root.after(0, lambda: progressbar.configure(value=percent))
            except Exception:
                pass

def main():
    """主函数"""
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
