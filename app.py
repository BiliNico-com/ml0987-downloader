#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
hsex 视频下载器 - GUI 版本
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
LOG_FILE = APP_DIR / "app.log"

DEFAULT_CONFIG = {
    "output_dir": str(APP_DIR / "downloads"),
    "ffmpeg_path": "",
    "proxy_enabled": False,
    "proxy_host": "127.0.0.1",
    "proxy_port": "1080",
    "proxy_user": "",
    "proxy_pass": "",
    "site": "https://ml0987.xyz",
    "list_type": "list",
    "page_start": 1,
    "page_end": 3,
    "title_with_author": True,
    "sort_by_upload_date": True,
}

# ==================== 日志 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=10*1024*1024,
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
                            headers={"User-Agent": "Mozilla/5.0"})
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
        self.root.title("hsex 视频下载器")
        self.root.geometry("1000x720")
        self.root.minsize(800, 600)

        # 加载配置
        self.config = load_config()

        # 爬虫核心
        self.crawler = None
        self.crawl_thread = None

        # 封面图片缓存
        self._cover_photo = None  # 保持引用防止 GC
        self._search_cover_photo = None  # 搜索 Tab 封面缓存

        # 批量爬取统计
        self._batch_total_videos = 0
        self._batch_done_videos = 0
        self._batch_success = 0

        # 创建 UI
        self._create_widgets()

        # 启动时静默检查环境，出错才提示
        self._silent_env_check()

    def _create_widgets(self):
        """创建界面组件"""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # Tab 页顺序：批量爬取 → 搜索 → 单视频 → 设置 → 日志 → 环境检测（隐藏）
        self.tab_crawl = ttk.Frame(self.notebook)
        self.tab_search = ttk.Frame(self.notebook)
        self.tab_single = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)
        self.tab_log = ttk.Frame(self.notebook)
        self.tab_env = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_crawl, text="  批量爬取  ")
        self.notebook.add(self.tab_search, text="  搜索  ")
        self.notebook.add(self.tab_single, text="  单视频  ")
        self.notebook.add(self.tab_settings, text="  设置  ")
        self.notebook.add(self.tab_log, text="  日志  ")
        # 环境检测 Tab 不显示标签，通过 select() 跳转
        self.notebook.add(self.tab_env, text="  环境检测  ")

        # 构建各 Tab
        self._build_tab_crawl()
        self._build_tab_search()
        self._build_tab_single()
        self._build_tab_settings()
        self._build_tab_log()
        self._build_tab_env()

    # ==================== 批量爬取 Tab ====================

    def _build_tab_crawl(self):
        """批量爬取 Tab"""
        # 控制面板
        control_frame = ttk.LabelFrame(self.tab_crawl, text="爬取设置", padding=10)
        control_frame.pack(fill="x", padx=20, pady=(10, 5))

        # 第一行：域名 + 列表类型
        type_frame = ttk.Frame(control_frame)
        type_frame.pack(fill="x", pady=3)
        ttk.Label(type_frame, text="站点:").pack(side="left")
        self.site_var = tk.StringVar(value=self.config.get("site", "https://ml0987.xyz"))
        site_combo = ttk.Combobox(type_frame, textvariable=self.site_var,
                                  values=["https://ml0987.xyz", "https://hsex.icu", "https://hsex.men", "https://hsex.tv"],
                                  width=16, state="readonly")
        site_combo.pack(side="left", padx=(5, 20))
        ttk.Label(type_frame, text="列表:").pack(side="left")
        self.list_type_var = tk.StringVar(value=self.config.get("list_type", "list"))
        type_combo = ttk.Combobox(type_frame, textvariable=self.list_type_var,
                                  values=["视频", "周榜", "月榜", "5分钟+", "10分钟+"],
                                  width=10, state="readonly")
        type_combo.pack(side="left", padx=5)

        # 第二行：页码 + 按钮
        page_frame = ttk.Frame(control_frame)
        page_frame.pack(fill="x", pady=3)
        ttk.Label(page_frame, text="页码:").pack(side="left")
        self.page_start_var = tk.IntVar(value=self.config["page_start"])
        ttk.Spinbox(page_frame, from_=1, to=100, textvariable=self.page_start_var, width=5).pack(side="left", padx=2)
        ttk.Label(page_frame, text="~").pack(side="left")
        self.page_end_var = tk.IntVar(value=self.config["page_end"])
        ttk.Spinbox(page_frame, from_=1, to=100, textvariable=self.page_end_var, width=5).pack(side="left", padx=(2, 15))

        ttk.Button(page_frame, text="▶ 开始爬取", command=self._start_crawl).pack(side="left", padx=3)
        ttk.Button(page_frame, text="■ 停止", command=self._stop_crawl).pack(side="left", padx=3)

        # 下方区域：左边封面 + 右边进度
        bottom_frame = ttk.Frame(self.tab_crawl)
        bottom_frame.pack(fill="both", expand=True, padx=20, pady=(5, 10))

        # 左侧：封面预览
        cover_frame = ttk.LabelFrame(bottom_frame, text="当前视频", padding=5)
        cover_frame.pack(side="left", fill="y", padx=(0, 10))
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

        # 右侧：进度
        right_frame = ttk.LabelFrame(bottom_frame, text="下载进度", padding=5)
        right_frame.pack(side="left", fill="both", expand=True)

        # 整体进度标签（视频计数）
        self.crawl_overall_label = tk.Label(right_frame, text="就绪",
                                             font=("Arial", 9), anchor="w")
        self.crawl_overall_label.pack(fill="x")

        # 进度条（当前视频切片进度）
        self.crawl_progress = ttk.Progressbar(right_frame, mode="determinate")
        self.crawl_progress.pack(fill="x", pady=(3, 5))

        # 切片进度标签
        self.crawl_slice_label = tk.Label(right_frame, text="",
                                           font=("Consolas", 9), anchor="w", fg="#555")
        self.crawl_slice_label.pack(fill="x")

        # 合并进度
        self.crawl_merge_label = tk.Label(right_frame, text="",
                                           font=("Consolas", 9), anchor="w", fg="#888")
        self.crawl_merge_label.pack(fill="x")
        self.crawl_merge_progress = ttk.Progressbar(right_frame, mode="determinate")
        self.crawl_merge_progress.pack(fill="x", pady=(3, 5))

        # 日志文本框
        self.crawl_status_text = scrolledtext.ScrolledText(right_frame, height=8, wrap="word",
                                                            font=("Consolas", 9))
        self.crawl_status_text.pack(fill="both", expand=True, pady=(5, 0))

    # ==================== 搜索 Tab ====================

    def _build_tab_search(self):
        """搜索 Tab"""
        # 控制面板
        control_frame = ttk.LabelFrame(self.tab_search, text="搜索设置", padding=10)
        control_frame.pack(fill="x", padx=20, pady=(10, 5))

        # 第一行：域名 + 搜索类型 + 关键词
        row1 = ttk.Frame(control_frame)
        row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="站点:").pack(side="left")
        self.search_site_var = tk.StringVar(value=self.config.get("site", "https://ml0987.xyz"))
        site_combo = ttk.Combobox(row1, textvariable=self.search_site_var,
                                  values=["https://ml0987.xyz", "https://hsex.icu", "https://hsex.men", "https://hsex.tv"],
                                  width=16, state="readonly")
        site_combo.pack(side="left", padx=(5, 15))
        ttk.Label(row1, text="类型:").pack(side="left")
        self.search_type_var = tk.StringVar(value="搜视频")
        type_combo = ttk.Combobox(row1, textvariable=self.search_type_var,
                                  values=["搜视频", "搜作者"], width=8, state="readonly")
        type_combo.pack(side="left", padx=(5, 15))
        ttk.Label(row1, text="关键词:").pack(side="left")
        self.search_keyword_var = tk.StringVar()
        search_entry = ttk.Entry(row1, textvariable=self.search_keyword_var, width=20)
        search_entry.pack(side="left", padx=5)
        search_entry.bind("<Return>", lambda e: self._on_search_action())

        # 第二行：排序 + 页码 + 按钮（搜视频模式）
        self.search_video_frame = ttk.Frame(control_frame)
        self.search_video_frame.pack(fill="x", pady=3)

        ttk.Label(self.search_video_frame, text="排序:").pack(side="left")
        self.search_sort_var = tk.StringVar(value="最新")
        sort_combo = ttk.Combobox(self.search_video_frame, textvariable=self.search_sort_var,
                                  values=["最新", "最热"], width=8, state="readonly")
        sort_combo.pack(side="left", padx=(5, 20))
        ttk.Label(self.search_video_frame, text="页码:").pack(side="left")
        self.search_page_start_var = tk.IntVar(value=1)
        ttk.Spinbox(self.search_video_frame, from_=1, to=100, textvariable=self.search_page_start_var, width=5).pack(side="left", padx=2)
        ttk.Label(self.search_video_frame, text="~").pack(side="left")
        self.search_page_end_var = tk.IntVar(value=3)
        ttk.Spinbox(self.search_video_frame, from_=1, to=100, textvariable=self.search_page_end_var, width=5).pack(side="left", padx=(2, 15))

        ttk.Button(self.search_video_frame, text="▶ 搜索并下载", command=self._start_search).pack(side="left", padx=3)
        ttk.Button(self.search_video_frame, text="■ 停止", command=self._stop_crawl).pack(side="left", padx=3)

        # 第二行：搜作者模式（按钮不同）
        self.search_author_frame = ttk.Frame(control_frame)
        # 不 pack，由 _toggle_search_mode 控制显示

        ttk.Button(self.search_author_frame, text="🔍 搜索作者", command=self._search_authors).pack(side="left", padx=3)
        ttk.Button(self.search_author_frame, text="全选", command=self._select_all_authors).pack(side="left", padx=3)
        ttk.Button(self.search_author_frame, text="取消全选", command=self._deselect_all_authors).pack(side="left", padx=3)
        ttk.Label(self.search_author_frame, text="作者页码:").pack(side="left", padx=(15, 0))
        self.search_author_page_start_var = tk.IntVar(value=1)
        ttk.Spinbox(self.search_author_frame, from_=1, to=100, textvariable=self.search_author_page_start_var, width=5).pack(side="left", padx=2)
        ttk.Label(self.search_author_frame, text="~").pack(side="left")
        self.search_author_page_end_var = tk.IntVar(value=1)
        ttk.Spinbox(self.search_author_frame, from_=1, to=100, textvariable=self.search_author_page_end_var, width=5).pack(side="left", padx=(2, 15))
        ttk.Button(self.search_author_frame, text="▶ 下载选中作者的视频", command=self._start_author_crawl).pack(side="left", padx=3)
        ttk.Button(self.search_author_frame, text="■ 停止", command=self._stop_crawl).pack(side="left", padx=3)

        # 作者列表区域（搜作者模式时显示，在封面和进度之间）
        self.search_author_list_frame = ttk.LabelFrame(self.tab_search, text="搜索到的作者（勾选要下载的）", padding=5)
        # 不 pack，由 _toggle_search_mode 控制显示
        self.search_author_listbox_frame = ttk.Frame(self.search_author_list_frame)
        self.search_author_listbox_frame.pack(fill="x")

        # 用 Canvas + Scrollbar + Checkbutton 实现可勾选列表（固定高度，不抢占空间）
        self._author_canvas = tk.Canvas(self.search_author_listbox_frame, height=80)
        self._author_scrollbar = ttk.Scrollbar(self.search_author_listbox_frame, orient="vertical", command=self._author_canvas.yview)
        self._author_inner_frame = ttk.Frame(self._author_canvas)
        self._author_inner_frame.bind("<Configure>", lambda e: self._author_canvas.configure(scrollregion=self._author_canvas.bbox("all")))
        self._author_canvas.create_window((0, 0), window=self._author_inner_frame, anchor="nw")
        self._author_canvas.configure(yscrollcommand=self._author_scrollbar.set)
        self._author_canvas.pack(side="left", fill="both", expand=True)
        self._author_scrollbar.pack(side="right", fill="y")

        self._author_check_vars = []  # 存储作者勾选变量

        # 下方区域：左边封面 + 右边进度
        bottom_frame = ttk.Frame(self.tab_search)
        bottom_frame.pack(fill="both", expand=True, padx=20, pady=(5, 10))

        # 左侧：封面预览
        cover_frame = ttk.LabelFrame(bottom_frame, text="当前视频", padding=5)
        cover_frame.pack(side="left", fill="y", padx=(0, 10))
        cover_frame.configure(width=220)
        cover_frame.pack_propagate(False)

        self.search_cover_label = tk.Label(cover_frame, text="等待搜索...", bg="#f0f0f0",
                                           width=22, height=13, anchor="center",
                                           fg="#999", font=("Arial", 10))
        self.search_cover_label.pack(fill="both", expand=True)

        self.search_preview_title_label = tk.Label(cover_frame, text="",
                                                   wraplength=200, justify="left",
                                                   font=("Arial", 9))
        self.search_preview_title_label.pack(fill="x", pady=(5, 0))

        # 右侧：进度
        right_frame = ttk.LabelFrame(bottom_frame, text="下载进度", padding=5)
        right_frame.pack(side="left", fill="both", expand=True)

        self.search_overall_label = tk.Label(right_frame, text="就绪",
                                             font=("Arial", 9), anchor="w")
        self.search_overall_label.pack(fill="x")

        self.search_progress = ttk.Progressbar(right_frame, mode="determinate")
        self.search_progress.pack(fill="x", pady=(3, 5))

        self.search_slice_label = tk.Label(right_frame, text="",
                                           font=("Consolas", 9), anchor="w", fg="#555")
        self.search_slice_label.pack(fill="x")

        # 合并进度
        self.search_merge_label = tk.Label(right_frame, text="",
                                           font=("Consolas", 9), anchor="w", fg="#888")
        self.search_merge_label.pack(fill="x")
        self.search_merge_progress = ttk.Progressbar(right_frame, mode="determinate")
        self.search_merge_progress.pack(fill="x", pady=(3, 5))

        self.search_status_text = scrolledtext.ScrolledText(right_frame, height=8, wrap="word",
                                                            font=("Consolas", 9))
        self.search_status_text.pack(fill="both", expand=True, pady=(5, 0))

        # 绑定搜索类型切换
        self.search_type_var.trace_add("write", lambda *_: self._toggle_search_mode())
        # 初始化显示状态
        self._toggle_search_mode()

    def _toggle_search_mode(self):
        """切换搜索模式（搜视频/搜作者）"""
        is_author = self.search_type_var.get() == "搜作者"
        if is_author:
            self.search_video_frame.pack_forget()
            self.search_author_frame.pack(fill="x", pady=3)
            self.search_author_list_frame.pack(fill="x", padx=20, pady=(0, 5))
        else:
            self.search_author_frame.pack_forget()
            self.search_author_list_frame.pack_forget()
            self.search_video_frame.pack(fill="x", pady=3)

    def _on_search_action(self):
        """回车键触发搜索"""
        if self.search_type_var.get() == "搜作者":
            self._search_authors()
        else:
            self._start_search()

    def _search_authors(self):
        """搜索作者，在列表中展示结果（含每个作者的总页数）"""
        keyword = self.search_keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("警告", "请输入搜索关键词")
            return

        # 清空旧列表
        for widget in self._author_inner_frame.winfo_children():
            widget.destroy()
        self._author_check_vars.clear()

        self.search_overall_label.config(text="正在搜索作者...")

        def run():
            try:
                crawler = CrawlerCore(
                    self.config,
                    log_callback=self._log_to_ui,
                    base_url=self.search_site_var.get(),
                )
                authors = crawler.search_authors(keyword)

                # 为每个作者获取总页数
                if authors:
                    self.root.after(0, lambda: self.search_overall_label.config(
                        text=f"找到 {len(authors)} 个作者，正在获取页数信息..."
                    ))
                    for author in authors:
                        try:
                            page_count = crawler.get_author_page_count(author["url"])
                            author["page_count"] = page_count
                        except Exception:
                            author["page_count"] = 1
            except Exception as e:
                self.root.after(0, lambda: self.search_overall_label.config(text=f"搜索失败: {e}"))
                return

            def show_results():
                if not authors:
                    self.search_overall_label.config(text=f"未找到匹配的作者: {keyword}")
                    return

                # 找出最大页数，用于设置 Spinbox 的上限
                max_pages = max(a.get("page_count", 1) for a in authors)
                self.search_author_page_start_var.set(1)
                self.search_author_page_end_var.set(max_pages)

                self.search_overall_label.config(
                    text=f"找到 {len(authors)} 个作者（最多 {max_pages} 页）"
                )

                for author in authors:
                    var = tk.BooleanVar(value=True)
                    self._author_check_vars.append((var, author))
                    page_info = f"{author['count']} 个视频，{author.get('page_count', '?')} 页"
                    cb = ttk.Checkbutton(
                        self._author_inner_frame,
                        text=f"{author['name']}  （{page_info}）",
                        variable=var
                    )
                    cb.pack(anchor="w", pady=1)

            self.root.after(0, show_results)

        threading.Thread(target=run, daemon=True).start()

    def _select_all_authors(self):
        """全选作者"""
        for var, _ in self._author_check_vars:
            var.set(True)

    def _deselect_all_authors(self):
        """取消全选"""
        for var, _ in self._author_check_vars:
            var.set(False)

    def _start_author_crawl(self):
        """下载选中作者的视频"""
        if self.crawl_thread and self.crawl_thread.is_alive():
            messagebox.showwarning("警告", "正在运行中，请先停止")
            return

        selected = [author for var, author in self._author_check_vars if var.get()]
        if not selected:
            messagebox.showwarning("警告", "请勾选至少一个作者")
            return

        names = ", ".join(a["name"] for a in selected)
        self._log_to_ui(f"准备爬取作者: {names}")

        def on_progress(current, total):
            pct = f"{current}/{total}" if total > 0 else "?"
            self._update_progress(
                self.search_progress, current, total,
                self.search_slice_label,
                f"切片: {pct}"
            )

        def on_merge_progress(percent, speed):
            self.root.after(0, lambda: self.search_merge_progress.configure(value=percent))
            speed_text = f"，速度: {speed}" if speed else ""
            self.root.after(0, lambda: self.search_merge_label.config(
                text=f"合并 MP4: {percent}%{speed_text}"
            ))

        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_ui,
            progress_callback=on_progress,
            info_callback=self._update_search_cover_preview,
            confirm_callback=self._confirm_dialog,
            base_url=self.search_site_var.get(),
            merge_progress_callback=on_merge_progress,
        )

        def run():
            try:
                self.root.after(0, lambda: self.search_overall_label.config(text="正在下载作者视频..."))
                self.root.after(0, lambda: self.search_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.search_merge_label.config(text=""))
                result = self.crawler.crawl_authors(
                    authors=selected,
                    page_start=self.search_author_page_start_var.get(),
                    page_end=self.search_author_page_end_var.get(),
                )
                success = result.get("success", 0)
                skipped = result.get("skipped", 0)
                self.root.after(0, lambda: self.search_overall_label.config(
                    text=f"完成 — 新下载: {success}，跳过: {skipped}"
                ))
                self._status_to_ui(self.search_status_text, f"── 作者下载完成（新下载: {success}，跳过: {skipped}） ──")
            except Exception as e:
                self._status_to_ui(self.search_status_text, f"错误: {e}")
                logger.exception("作者下载失败")

        self.crawl_thread = threading.Thread(target=run)
        self.crawl_thread.daemon = True
        self.crawl_thread.start()

    # ==================== 单视频 Tab ====================

    def _build_tab_single(self):
        """单视频 Tab"""
        # URL 输入
        url_frame = ttk.LabelFrame(self.tab_single, text="视频 URL", padding=10)
        url_frame.pack(fill="x", padx=20, pady=(20, 5))

        self.url_var = tk.StringVar()
        ttk.Entry(url_frame, textvariable=self.url_var, width=60).pack(fill="x", padx=5, pady=5)

        # 标题输入
        title_frame = ttk.LabelFrame(self.tab_single, text="视频标题（可选，留空自动获取）", padding=10)
        title_frame.pack(fill="x", padx=20, pady=5)

        self.title_var = tk.StringVar()
        ttk.Entry(title_frame, textvariable=self.title_var, width=60).pack(fill="x", padx=5, pady=5)

        # 按钮
        btn_frame = ttk.Frame(self.tab_single)
        btn_frame.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_frame, text="▶ 开始下载", command=self._start_single).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="■ 停止", command=self._stop_crawl).pack(side="left", padx=5)

        # 进度显示
        progress_frame = ttk.LabelFrame(self.tab_single, text="下载进度", padding=10)
        progress_frame.pack(fill="both", expand=True, padx=20, pady=(5, 20))

        self.single_overall_label = tk.Label(progress_frame, text="就绪",
                                              font=("Arial", 9), anchor="w")
        self.single_overall_label.pack(fill="x")

        self.single_progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.single_progress.pack(fill="x", pady=(3, 5))

        self.single_slice_label = tk.Label(progress_frame, text="",
                                            font=("Consolas", 9), anchor="w", fg="#555")
        self.single_slice_label.pack(fill="x")

        # 合并进度
        self.single_merge_label = tk.Label(progress_frame, text="",
                                            font=("Consolas", 9), anchor="w", fg="#888")
        self.single_merge_label.pack(fill="x")
        self.single_merge_progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.single_merge_progress.pack(fill="x", pady=(3, 5))

        self.single_status_text = scrolledtext.ScrolledText(progress_frame, height=8, wrap="word")
        self.single_status_text.pack(fill="both", expand=True, pady=(5, 0))

    # ==================== 设置 Tab ====================

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

        # 下载设置
        download_frame = ttk.LabelFrame(self.tab_settings, text="下载设置", padding=10)
        download_frame.pack(fill="x", padx=20, pady=10)

        self.title_with_author_var = tk.BooleanVar(value=self.config.get("title_with_author", True))
        ttk.Checkbutton(download_frame, text="标题包含上传者（标题 - 作者名）",
                        variable=self.title_with_author_var).pack(anchor="w", padx=5, pady=3)

        self.sort_by_upload_date_var = tk.BooleanVar(value=self.config.get("sort_by_upload_date", True))
        ttk.Checkbutton(download_frame, text="按视频上传日期分类（关闭则全部存到下载当天）",
                        variable=self.sort_by_upload_date_var).pack(anchor="w", padx=5, pady=3)

        # 代理设置
        proxy_frame = ttk.LabelFrame(self.tab_settings, text="SOCKS5 代理（可选）", padding=10)
        proxy_frame.pack(fill="x", padx=20, pady=10)

        self.proxy_enabled_var = tk.BooleanVar(value=self.config.get("proxy_enabled", False))
        ttk.Checkbutton(proxy_frame, text="启用代理", variable=self.proxy_enabled_var).pack(anchor="w", padx=5, pady=5)

        row1 = ttk.Frame(proxy_frame)
        row1.pack(fill="x", padx=5, pady=2)
        ttk.Label(row1, text="主机:").pack(side="left")
        self.proxy_host_var = tk.StringVar(value=self.config["proxy_host"])
        ttk.Entry(row1, textvariable=self.proxy_host_var).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Label(row1, text="端口:").pack(side="left")
        self.proxy_port_var = tk.StringVar(value=self.config["proxy_port"])
        ttk.Entry(row1, textvariable=self.proxy_port_var, width=8).pack(side="left", padx=5)

        row2 = ttk.Frame(proxy_frame)
        row2.pack(fill="x", padx=5, pady=2)
        ttk.Label(row2, text="账号:").pack(side="left")
        self.proxy_user_var = tk.StringVar(value=self.config["proxy_user"])
        ttk.Entry(row2, textvariable=self.proxy_user_var).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Label(row2, text="密码:").pack(side="left")
        self.proxy_pass_var = tk.StringVar(value=self.config["proxy_pass"])
        ttk.Entry(row2, textvariable=self.proxy_pass_var, show="*", width=12).pack(side="left", padx=5)

        # 代理测试按钮
        btn_row = ttk.Frame(proxy_frame)
        btn_row.pack(fill="x", padx=5, pady=5)
        ttk.Button(btn_row, text="测试代理连接", command=self._test_proxy).pack(side="left", padx=5)

        # 保存按钮
        btn_frame = ttk.Frame(self.tab_settings)
        btn_frame.pack(fill="x", padx=20, pady=10)
        ttk.Button(btn_frame, text="保存设置", command=self._save_settings).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="检查环境", command=self._manual_env_check).pack(side="left", padx=5)

    # ==================== 日志 Tab ====================

    def _build_tab_log(self):
        """日志 Tab"""
        log_frame = ttk.Frame(self.tab_log)
        log_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, wrap="word",
                                                   font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(self.tab_log)
        btn_frame.pack(fill="x", padx=20, pady=(0, 10))
        ttk.Button(btn_frame, text="清空日志", command=self._clear_log).pack(side="left", padx=5)

    # ==================== 环境检测 Tab ====================

    def _build_tab_env(self):
        """环境检测 Tab"""
        ttk.Label(self.tab_env, text="运行环境检查", font=("Arial", 14, "bold")).pack(pady=20)

        result_frame = ttk.LabelFrame(self.tab_env, text="检查结果", padding=10)
        result_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self.env_status_text = scrolledtext.ScrolledText(result_frame, height=15, wrap="word")
        self.env_status_text.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(self.tab_env)
        btn_frame.pack(fill="x", padx=20, pady=10)

        ttk.Button(btn_frame, text="重新检查", command=self._manual_env_check).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="安装 Python 依赖", command=self._install_deps).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="下载 ffmpeg", command=self._download_ffmpeg).pack(side="left", padx=5)

    # ==================== 封面预览 ====================

    def _update_cover_preview(self, info: dict):
        """在子线程下载封面，通过 root.after 更新 UI"""
        cover_url = info.get("cover", "")
        title = info.get("title", "")

        try:
            self.root.after(0, lambda: self.preview_title_label.config(text=title))
        except Exception:
            pass

        if not cover_url:
            return

        img_data = download_image(cover_url)
        if not img_data:
            return

        def show_image():
            try:
                if HAS_PIL:
                    img = Image.open(io.BytesIO(img_data))
                    img.thumbnail((200, 130), Image.LANCZOS)
                    self._cover_photo = ImageTk.PhotoImage(img)
                    self.cover_label.config(image=self._cover_photo, text="", bg="white")
                else:
                    self._cover_photo = tk.PhotoImage(data=img_data)
                    self.cover_label.config(image=self._cover_photo, text="", bg="white")
            except Exception:
                self.cover_label.config(image="", text="封面加载失败\n(需要 Pillow)", bg="#f0f0f0")

        try:
            self.root.after(0, show_image)
        except Exception:
            pass

    # ==================== 环境检测 ====================

    def _silent_env_check(self):
        """启动时静默检查，出错才跳转到环境检测 Tab"""
        errors = []

        # 检测 ffmpeg
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path.exists():
            errors.append(f"ffmpeg.exe 未找到（需放在: {ffmpeg_path.parent}）")

        # 检测 requests
        try:
            import requests
        except ImportError:
            errors.append("requests 未安装（运行: pip install requests）")

        # 检测 pycryptodome
        try:
            from Crypto.Cipher import AES
        except ImportError:
            errors.append("pycryptodome 未安装（加密视频无法下载）")

        if errors:
            # 跳转到环境检测 Tab
            env_tab_index = self.notebook.index(self.tab_env)
            self.notebook.select(env_tab_index)
            # 填充检查结果
            self._check_environment(errors)
        else:
            # 正常，显示环境检测内容但不跳转
            self._check_environment([])

    def _manual_env_check(self):
        """手动触发环境检查，跳转到该 Tab"""
        errors = []
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path.exists():
            errors.append(f"ffmpeg.exe 未找到（需放在: {ffmpeg_path.parent}）")
        try:
            import requests
        except ImportError:
            errors.append("requests 未安装")
        if not HAS_PIL:
            errors.append("Pillow 未安装（封面预览不可用）")
        try:
            from Crypto.Cipher import AES
        except ImportError:
            errors.append("pycryptodome 未安装")

        self._check_environment(errors)
        self.notebook.select(self.notebook.index(self.tab_env))

    def _check_environment(self, errors: list):
        """检查运行环境并显示结果"""
        self.env_status_text.delete(1.0, tk.END)

        # ffmpeg
        ffmpeg_path = get_ffmpeg_path()
        if ffmpeg_path.exists():
            self._append_status(f"ffmpeg: OK  ({ffmpeg_path})", "OK")
        else:
            self._append_status(f"ffmpeg: 未找到", "FAIL")
            self._append_status(f"  请放置于: {ffmpeg_path.parent}", "WARN")

        # requests
        try:
            import requests
            self._append_status(f"requests: OK (v{requests.__version__})", "OK")
        except ImportError:
            self._append_status(f"requests: 未安装 (pip install requests)", "FAIL")

        # Pillow
        if HAS_PIL:
            self._append_status(f"Pillow: OK (封面预览可用)", "OK")
        else:
            self._append_status(f"Pillow: 未安装 (pip install Pillow)", "WARN")

        # pycryptodome
        try:
            from Crypto.Cipher import AES
            self._append_status(f"pycryptodome: OK (AES 解密可用)", "OK")
        except ImportError:
            self._append_status(f"pycryptodome: 未安装 (pip install pycryptodome)", "WARN")

        if errors:
            self._append_status("", "")
            self._append_status("⚠ 以下问题需要解决:", "FAIL")
            for e in errors:
                self._append_status(f"  ✗ {e}", "FAIL")
        else:
            self._append_status("", "")
            self._append_status("✓ 环境检查通过，所有依赖就绪", "OK")

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
            self._check_environment([])
        except Exception as e:
            self.env_status_text.insert(tk.END, f"\n安装失败: {e}\n")

    def _download_ffmpeg(self):
        """下载 ffmpeg"""
        import webbrowser
        webbrowser.open("https://www.gyan.dev/ffmpeg/builds/")
        messagebox.showinfo("下载 ffmpeg", "请下载 ffmpeg-release-essentials.zip，解压后将 ffmpeg.exe 放到程序目录")

    # ==================== 通用功能 ====================

    def _browse_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.save_dir_var.set(path)

    def _save_settings(self):
        self.config["output_dir"] = self.save_dir_var.get()
        self.config["site"] = self.site_var.get()
        self.config["title_with_author"] = self.title_with_author_var.get()
        self.config["sort_by_upload_date"] = self.sort_by_upload_date_var.get()
        self.config["proxy_enabled"] = self.proxy_enabled_var.get()
        self.config["proxy_host"] = self.proxy_host_var.get()
        self.config["proxy_port"] = self.proxy_port_var.get()
        self.config["proxy_user"] = self.proxy_user_var.get()
        self.config["proxy_pass"] = self.proxy_pass_var.get()
        save_config(self.config)
        messagebox.showinfo("保存成功", "设置已保存")

    def _clear_log(self):
        self.log_text.delete(1.0, tk.END)

    # ==================== 日志/状态 UI 输出 ====================

    def _log_to_ui(self, text, level="info"):
        """记录日志到 UI（线程安全）"""
        timestamp = time.strftime("%H:%M:%S")
        prefix = {"error": "✗", "warn": "⚠", "info": "ℹ"}.get(level, "·")
        line = f"[{timestamp}] {prefix} {text}\n"
        try:
            self.root.after(0, lambda: self._append_log(line))
        except Exception:
            pass
        logger.info(text)

    def _append_log(self, line):
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
        widget.insert(tk.END, f"{text}\n")
        widget.see(tk.END)

    def _confirm_dialog(self, opts: dict) -> str:
        """倒计时确认弹窗（线程安全），返回用户选择的 value
        opts: {
            "title": str,
            "message": str,
            "choices": [(value, label), ...],
            "default": value,   # 默认选中
            "countdown": int     # 秒数
        }
        """
        import threading

        result = {"value": opts.get("default", opts["choices"][0][0])}
        ready = threading.Event()

        def _show():
            try:
                dialog = tk.Toplevel(self.root)
                dialog.title(opts.get("title", "提示"))
                dialog.geometry("480x220")
                dialog.resizable(False, False)
                dialog.attributes("-topmost", True)
                dialog.grab_set()

                # 居中
                dialog.update_idletasks()
                x = (dialog.winfo_screenwidth() // 2) - 240
                y = (dialog.winfo_screenheight() // 2) - 110
                dialog.geometry(f"480x220+{x}+{y}")

                # 消息
                msg_frame = tk.Frame(dialog, pady=10)
                msg_frame.pack(fill="both", expand=True)
                tk.Label(
                    msg_frame, text=opts.get("message", ""),
                    justify="left", wraplength=440,
                    font=("Microsoft YaHei", 10)
                ).pack(padx=20)

                countdown_label = tk.Label(
                    msg_frame, text="",
                    font=("Microsoft YaHei", 9), fg="#888"
                )
                countdown_label.pack(pady=(5, 0))

                btn_frame = tk.Frame(dialog, pady=10)
                btn_frame.pack()

                remaining = {"count": opts.get("countdown", 10)}
                selected = {"value": opts.get("default", opts["choices"][0][0])}
                timer_job = {"id": None}

                def update_countdown():
                    if remaining["count"] > 0:
                        default_label = next(l for v, l in opts['choices'] if v == selected['value'])
                        countdown_label.config(text=f"【{remaining['count']} 秒后自动选择「{default_label}」】")
                        remaining["count"] -= 1
                        timer_job["id"] = dialog.after(1000, update_countdown)
                    else:
                        # 超时，选默认值
                        dialog.destroy()

                def on_select(value, label):
                    if timer_job["id"]:
                        dialog.after_cancel(timer_job["id"])
                    selected["value"] = value
                    result["value"] = value
                    ready.set()
                    dialog.destroy()

                # 创建按钮
                for value, label in opts["choices"]:
                    color = "#4CAF50" if value == opts.get("default") else "#ccc"
                    fg = "white" if value == opts.get("default") else "#333"
                    btn = tk.Button(
                        btn_frame, text=label, font=("Microsoft YaHei", 10),
                        width=14, relief="flat", bd=2,
                        bg=color, fg=fg,
                        activebackground=color, activeforeground=fg,
                        cursor="hand2",
                        command=lambda v=value, l=label: on_select(v, l)
                    )
                    btn.pack(side="left", padx=8)

                # ESC 键默认选否
                def on_esc(e):
                    if opts["choices"]:
                        on_select(opts["choices"][-1][0], opts["choices"][-1][1])
                dialog.bind("<Escape>", on_esc)

                timer_job["id"] = dialog.after(1000, update_countdown)
                dialog.protocol("WM_DELETE_WINDOW", lambda: on_select(
                    opts["choices"][-1][0], opts["choices"][-1][1]
                ))
            except Exception:
                result["value"] = opts.get("default", opts["choices"][0][0])
                ready.set()

        self.root.after(0, _show)
        ready.wait(timeout=opts.get("countdown", 10) + 2)
        return result["value"]

    def _update_progress(self, progressbar, current, total, label_widget=None, label_text=None):
        """更新进度条（线程安全）"""
        if total > 0:
            percent = (current / total) * 100
            try:
                self.root.after(0, lambda: progressbar.configure(value=percent))
                if label_widget and label_text:
                    self.root.after(0, lambda: label_widget.config(text=label_text))
            except Exception:
                pass

    # ==================== 搜索下载 ====================

    def _update_search_cover_preview(self, info: dict):
        """搜索 Tab 的封面预览"""
        cover_url = info.get("cover", "")
        title = info.get("title", "")

        try:
            self.root.after(0, lambda: self.search_preview_title_label.config(text=title))
        except Exception:
            pass

        if not cover_url:
            return

        img_data = download_image(cover_url)
        if not img_data:
            return

        def show_image():
            try:
                if HAS_PIL:
                    img = Image.open(io.BytesIO(img_data))
                    img.thumbnail((200, 130), Image.LANCZOS)
                    self._search_cover_photo = ImageTk.PhotoImage(img)
                    self.search_cover_label.config(image=self._search_cover_photo, text="", bg="white")
                else:
                    self._search_cover_photo = tk.PhotoImage(data=img_data)
                    self.search_cover_label.config(image=self._search_cover_photo, text="", bg="white")
            except Exception:
                self.search_cover_label.config(image="", text="封面加载失败", bg="#f0f0f0")

        try:
            self.root.after(0, show_image)
        except Exception:
            pass

    def _start_search(self):
        """开始搜索并下载"""
        if self.crawl_thread and self.crawl_thread.is_alive():
            messagebox.showwarning("警告", "正在运行中，请先停止")
            return

        keyword = self.search_keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("警告", "请输入搜索关键词")
            return

        # 排序映射
        sort_map = {"最新": "new", "最热": "hot"}
        sort = sort_map.get(self.search_sort_var.get(), "new")

        def on_progress(current, total):
            pct = f"{current}/{total}" if total > 0 else "?"
            self._update_progress(
                self.search_progress, current, total,
                self.search_slice_label,
                f"切片: {pct}"
            )

        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_ui,
            progress_callback=on_progress,
            info_callback=self._update_search_cover_preview,
            base_url=self.search_site_var.get(),
            merge_progress_callback=lambda p, s: self.root.after(0, lambda: [
                self.search_merge_progress.configure(value=p),
                self.search_merge_label.config(text=f"合并 MP4: {p}%{f'，速度: {s}' if s else ''}")
            ]),
        )

        def run():
            try:
                self.root.after(0, lambda: self.search_overall_label.config(text="正在搜索下载..."))
                self.root.after(0, lambda: self.search_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.search_merge_label.config(text=""))
                result = self.crawler.crawl_search(
                    keyword=keyword,
                    page_start=self.search_page_start_var.get(),
                    page_end=self.search_page_end_var.get(),
                    sort=sort,
                )
                success = result.get("success", 0)
                skipped = result.get("skipped", 0)
                self.root.after(0, lambda: self.search_overall_label.config(
                    text=f"完成 — 新下载: {success}，跳过: {skipped}"
                ))
                self._status_to_ui(self.search_status_text, f"── 搜索下载完成（新下载: {success}，跳过: {skipped}） ──")
            except Exception as e:
                self._status_to_ui(self.search_status_text, f"错误: {e}")
                logger.exception("搜索下载失败")

        self.crawl_thread = threading.Thread(target=run)
        self.crawl_thread.daemon = True
        self.crawl_thread.start()

    # ==================== 批量爬取 ====================

    def _start_crawl(self):
        """开始批量爬取"""
        if self.crawl_thread and self.crawl_thread.is_alive():
            messagebox.showwarning("警告", "正在运行中，请先停止")
            return

        def on_progress(current, total):
            """当前视频的切片进度"""
            pct = f"{current}/{total}" if total > 0 else "?"
            self._update_progress(
                self.crawl_progress, current, total,
                self.crawl_slice_label,
                f"切片: {pct}"
            )

        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_ui,
            progress_callback=on_progress,
            info_callback=self._update_cover_preview,
            base_url=self.site_var.get(),
            merge_progress_callback=lambda p, s: self.root.after(0, lambda: [
                self.crawl_merge_progress.configure(value=p),
                self.crawl_merge_label.config(text=f"合并 MP4: {p}%{f'，速度: {s}' if s else ''}")
            ]),
        )

        def run():
            try:
                self.root.after(0, lambda: self.crawl_overall_label.config(text="正在爬取..."))
                self.root.after(0, lambda: self.crawl_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.crawl_merge_label.config(text=""))
                result = self.crawler.crawl_batch(
                    page_start=self.page_start_var.get(),
                    page_end=self.page_end_var.get(),
                    list_type=self.list_type_var.get()
                )
                success = result.get("success", 0)
                skipped = result.get("skipped", 0)
                self.root.after(0, lambda: self.crawl_overall_label.config(
                    text=f"完成 — 新下载: {success}，跳过: {skipped}"
                ))
                self._status_to_ui(self.crawl_status_text, f"── 批量爬取完成（新下载: {success}，跳过: {skipped}） ──")
            except Exception as e:
                self._status_to_ui(self.crawl_status_text, f"错误: {e}")
                logger.exception("批量爬取失败")

        self.crawl_thread = threading.Thread(target=run)
        self.crawl_thread.daemon = True
        self.crawl_thread.start()

    # ==================== 单视频下载 ====================

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

        def on_progress(current, total):
            pct = f"{current}/{total}" if total > 0 else "?"
            self._update_progress(
                self.single_progress, current, total,
                self.single_slice_label,
                f"切片: {pct}"
            )

        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_ui,
            progress_callback=on_progress,
            base_url=self.site_var.get(),
            merge_progress_callback=lambda p, s: self.root.after(0, lambda: [
                self.single_merge_progress.configure(value=p),
                self.single_merge_label.config(text=f"合并 MP4: {p}%{f'，速度: {s}' if s else ''}")
            ]),
        )

        def run():
            try:
                self.root.after(0, lambda: self.single_overall_label.config(text="正在下载..."))
                self.root.after(0, lambda: self.single_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.single_merge_label.config(text=""))
                self.crawler.download_single(url, title)
                self.root.after(0, lambda: self.single_overall_label.config(text="下载完成"))
                self._status_to_ui(self.single_status_text, "── 下载完成 ──")
            except Exception as e:
                self._status_to_ui(self.single_status_text, f"错误: {e}")
                logger.exception("单个下载失败")

        self.crawl_thread = threading.Thread(target=run)
        self.crawl_thread.daemon = True
        self.crawl_thread.start()

    # ==================== 停止 ====================

    def _stop_crawl(self):
        """停止任务"""
        if self.crawler:
            self.crawler.stop()
            # 不再在主线程 join，避免卡顿；线程设为 daemon 会自动清理
            self.crawl_thread = None
            self.crawler = None
            self._status_to_ui(self.crawl_status_text, "── 已停止 ──")
            self._status_to_ui(self.single_status_text, "── 已停止 ──")
            self._status_to_ui(self.search_status_text, "── 已停止 ──")
            try:
                self.root.after(0, lambda: self.crawl_overall_label.config(text="已停止"))
                self.root.after(0, lambda: self.single_overall_label.config(text="已停止"))
                self.root.after(0, lambda: self.search_overall_label.config(text="已停止"))
            except Exception:
                pass

    # ==================== 代理测试 ====================

    def _test_proxy(self):
        """测试代理连接是否可用（使用本地 socks.py，无需安装）"""
        host = self.proxy_host_var.get().strip()
        port = self.proxy_port_var.get().strip()
        user = self.proxy_user_var.get().strip()
        passwd = self.proxy_pass_var.get().strip()

        if not host or not port:
            messagebox.showwarning("提示", "请填写代理主机和端口")
            return

        # 弹出结果窗口
        result_win = tk.Toplevel(self.root)
        result_win.title("代理测试")
        result_win.geometry("450x320")
        result_win.resizable(False, False)
        result_win.grab_set()

        result_text = scrolledtext.ScrolledText(result_win, height=16, wrap="word", font=("Consolas", 9))
        result_text.pack(fill="both", expand=True, padx=10, pady=10)

        def append(text, tag=None):
            color_map = {"green": "#2e7d32", "red": "#c62828", "orange": "#e65100"}
            if tag and tag in color_map:
                result_text.tag_config(tag, foreground=color_map[tag])
                result_text.insert(tk.END, text + "\n", tag)
            else:
                result_text.insert(tk.END, text + "\n")
            result_text.see(tk.END)

        proxy_label = f"socks5h://{host}:{port}"
        append(f"代理: {proxy_label}\n")

        def run_test():
            import requests as req
            # 本地 socks.py 提供支持，无需 pip install
            if user and passwd:
                proxy_url = f"socks5h://{user}:{passwd}@{host}:{port}"
            else:
                proxy_url = f"socks5h://{host}:{port}"
            proxies = {"http": proxy_url, "https": proxy_url}

            targets = [
                ("Google", "https://www.google.com"),
                ("YouTube", "https://www.youtube.com"),
                ("Twitter/X", "https://x.com"),
                ("ipinfo.io (出口IP)", "https://ipinfo.io/json"),
            ]

            for name, url in targets:
                self.root.after(0, lambda n=name: append(f"正在测试 {n}...", "black"))
                try:
                    resp = req.get(url, proxies=proxies, timeout=10, allow_redirects=False)
                    status = resp.status_code
                    if name == "ipinfo.io (出口IP)":
                        self.root.after(0, lambda s=status: append(f"  ✓ {name} — HTTP {s}", "green"))
                        # 显示IP信息
                        try:
                            body = resp.json()
                            ip = body.get("ip", "?")
                            country = body.get("country", "?")
                            self.root.after(0, lambda i=ip, c=country: append(f"    出口IP: {i}，地区: {c}", "green"))
                        except Exception:
                            pass
                    elif 200 <= status < 400:
                        self.root.after(0, lambda n=name, s=status: append(f"  ✓ {n} — HTTP {s}", "green"))
                    else:
                        self.root.after(0, lambda n=name, s=status: append(f"  ✗ {n} — HTTP {s}", "orange"))
                except Exception as e:
                    self.root.after(0, lambda n=name, err=str(e)[:100]: append(f"  ✗ {n} — {err}", "red"))

            self.root.after(0, lambda: append("\n── 测试完成 ──"))

        threading.Thread(target=run_test, daemon=True).start()


def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
