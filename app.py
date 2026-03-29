#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
hsex 视频下载器 - GUI 版本
"""

import os
import sys
import json
import logging
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

from crawler_core import CrawlerCore, MIRROR_SITES, LIST_TYPES, LIST_TYPE_ALIASES, DEFAULT_HEADERS

# ==================== 配置 ====================

APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "config.json"

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


# ==================== 日志 Handler ====================

class _UITextHandler(logging.Handler):
    """将 Python logging 输出到 tkinter ScrolledText"""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)
        try:
            self.text_widget.after(0, lambda: self._append(msg))
        except Exception:
            pass

    def _append(self, msg):
        self.text_widget.insert("end", msg + "\n")
        self.text_widget.see("end")


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
        self.notebook.add(self.tab_log, text="  运行日志  ")
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

        # 日志折叠按钮 + 日志框
        self._crawl_log_visible = False
        crawl_log_btn_frame = ttk.Frame(right_frame)
        crawl_log_btn_frame.pack(fill="x", pady=(5, 0))
        self._crawl_log_toggle_btn = ttk.Button(crawl_log_btn_frame, text="📋 日志 ▸",
                                                  command=self._toggle_crawl_log)
        self._crawl_log_toggle_btn.pack(side="left")
        ttk.Button(crawl_log_btn_frame, text="📁 导出", width=6,
                   command=lambda: self._export_tab_log("批量爬取")).pack(side="right")
        self._crawl_log_frame = ttk.Frame(right_frame)
        self.crawl_status_text = scrolledtext.ScrolledText(self._crawl_log_frame, height=8, wrap="word",
                                                            font=("Consolas", 9))
        self.crawl_status_text.pack(fill="both", expand=True)

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

        # 日志折叠按钮 + 日志框
        self._search_log_visible = False
        search_log_btn_frame = ttk.Frame(right_frame)
        search_log_btn_frame.pack(fill="x", pady=(5, 0))
        self._search_log_toggle_btn = ttk.Button(search_log_btn_frame, text="📋 日志 ▸",
                                                   command=self._toggle_search_log)
        self._search_log_toggle_btn.pack(side="left")
        ttk.Button(search_log_btn_frame, text="📁 导出", width=6,
                   command=lambda: self._export_tab_log("搜索")).pack(side="right")
        self._search_log_frame = ttk.Frame(right_frame)
        self.search_status_text = scrolledtext.ScrolledText(self._search_log_frame, height=8, wrap="word",
                                                            font=("Consolas", 9))
        self.search_status_text.pack(fill="both", expand=True)

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
                    log_callback=self._log_to_search_ui,
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
        self._log_to_search_ui(f"准备爬取作者: {names}")

        # 自动展开日志
        if not self._search_log_visible:
            self._toggle_search_log()

        def on_progress(current, total):
            pct = f"{current}/{total}" if total > 0 else "?"
            self._update_progress(
                self.search_progress, current, total,
                self.search_slice_label,
                f"切片: {pct}"
            )
            # 新视频切片开始下载时，重置合并进度条
            if current <= 1:
                self.root.after(0, lambda: self.search_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.search_merge_label.config(text="切片下载中..."))

        def on_merge_progress(percent, speed):
            self.root.after(0, lambda: self.search_merge_progress.configure(value=percent))
            speed_text = f"，速度: {speed}" if speed else ""
            self.root.after(0, lambda: self.search_merge_label.config(
                text=f"合并 MP4: {percent}%{speed_text}"
            ))

        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_search_ui,
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

    # ==================== 单视频 Tab（视频浏览） ====================

    def _build_tab_single(self):
        """单视频 Tab - 视频浏览、勾选、下载"""
        # ---- 顶部控制栏 ----
        top_frame = ttk.Frame(self.tab_single)
        top_frame.pack(fill="x", padx=10, pady=(10, 5))

        # 站点选择
        ttk.Label(top_frame, text="站点:").pack(side="left")
        self.single_site_var = tk.StringVar(value=self.config.get("site", "https://ml0987.xyz"))
        site_combo = ttk.Combobox(top_frame, textvariable=self.single_site_var,
                                  values=list(MIRROR_SITES.values()), width=14, state="readonly")
        site_combo.pack(side="left", padx=(2, 10))

        # 列表类型
        ttk.Label(top_frame, text="类型:").pack(side="left")
        self.single_type_var = tk.StringVar(value="list")
        type_combo = ttk.Combobox(top_frame, textvariable=self.single_type_var,
                                  values=list(LIST_TYPE_ALIASES.keys()), width=8, state="readonly")
        type_combo.pack(side="left", padx=(2, 10))

        # 翻页控制
        page_frame = ttk.Frame(top_frame)
        page_frame.pack(side="left")

        self.single_page_var = tk.IntVar(value=1)
        ttk.Button(page_frame, text="◀", width=3, command=self._single_prev_page).pack(side="left")
        ttk.Label(page_frame, text=" 第").pack(side="left")
        self.single_page_entry = ttk.Spinbox(page_frame, from_=1, to=9999, width=4,
                                              textvariable=self.single_page_var)
        self.single_page_entry.pack(side="left", padx=2)
        self.single_page_entry.bind("<Return>", lambda e: self._load_single_page())
        ttk.Label(page_frame, text="页 ").pack(side="left")
        ttk.Button(page_frame, text="▶", width=3, command=self._single_next_page).pack(side="left")

        # 加载按钮
        ttk.Button(top_frame, text="📋 加载", command=self._load_single_page).pack(side="left", padx=(10, 5))

        # ---- 操作栏 ----
        action_frame = ttk.Frame(self.tab_single)
        action_frame.pack(fill="x", padx=10, pady=3)

        self.single_status_label = ttk.Label(action_frame, text="点击「加载」获取视频列表")
        self.single_status_label.pack(side="left", padx=5)

        self.single_select_all_var = tk.BooleanVar()
        ttk.Checkbutton(action_frame, text="全选", variable=self.single_select_all_var,
                        command=self._single_toggle_all).pack(side="right", padx=5)

        ttk.Button(action_frame, text="▶ 下载选中", command=self._start_single_batch).pack(side="right", padx=5)

        # ---- 进度区（放在操作栏下方，随时可见） ----
        progress_frame = ttk.LabelFrame(self.tab_single, text="下载进度", padding=8)
        progress_frame.pack(fill="x", padx=10, pady=(0, 5))

        self.single_overall_label = tk.Label(progress_frame, text="就绪",
                                              font=("Arial", 9), anchor="w")
        self.single_overall_label.pack(fill="x")

        prog_row = ttk.Frame(progress_frame)
        prog_row.pack(fill="x", pady=(3, 0))
        ttk.Label(prog_row, text="切片:", width=5).pack(side="left")
        self.single_progress = ttk.Progressbar(prog_row, mode="determinate")
        self.single_progress.pack(side="left", fill="x", expand=True)
        self.single_slice_label = tk.Label(prog_row, text="", font=("Consolas", 9), fg="#555", width=15)
        self.single_slice_label.pack(side="left")

        merge_row = ttk.Frame(progress_frame)
        merge_row.pack(fill="x", pady=(3, 0))
        ttk.Label(merge_row, text="合并:", width=5).pack(side="left")
        self.single_merge_progress = ttk.Progressbar(merge_row, mode="determinate")
        self.single_merge_progress.pack(side="left", fill="x", expand=True)
        self.single_merge_label = tk.Label(merge_row, text="", font=("Consolas", 9), fg="#888", width=15)
        self.single_merge_label.pack(side="left")

        # 日志（折叠在进度区下方，默认收起）
        self._single_log_visible = False
        single_log_btn_frame = ttk.Frame(progress_frame)
        single_log_btn_frame.pack(fill="x", pady=(5, 0))
        log_toggle_btn = ttk.Button(single_log_btn_frame, text="📋 日志 ▸",
                                    command=self._toggle_single_log)
        log_toggle_btn.pack(side="left")
        ttk.Button(single_log_btn_frame, text="📁 导出", width=6,
                   command=lambda: self._export_tab_log("单视频")).pack(side="right")
        log_frame = ttk.Frame(progress_frame)
        # 不 pack，由 _toggle_single_log 控制显示
        self._single_log_frame = log_frame
        self._single_log_toggle_btn = log_toggle_btn
        self.single_log_text = scrolledtext.ScrolledText(log_frame, height=5, wrap="word",
                                                         font=("Consolas", 9))
        self.single_log_text.pack(fill="x")

        # ---- 视频网格（可滚动） ----
        grid_container = ttk.Frame(self.tab_single)
        grid_container.pack(fill="both", expand=True, padx=10, pady=5)

        # Canvas + 滚动条实现可滚动区域
        self.single_canvas = tk.Canvas(grid_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(grid_container, orient="vertical", command=self.single_canvas.yview)
        self.single_inner_frame = ttk.Frame(self.single_canvas)

        self.single_inner_frame.bind("<Configure>",
            lambda e: self.single_canvas.configure(scrollregion=self.single_canvas.bbox("all")))

        self.single_canvas.create_window((0, 0), window=self.single_inner_frame, anchor="nw")
        self.single_canvas.configure(yscrollcommand=scrollbar.set)

        self.single_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 鼠标滚轮绑定
        self.single_canvas.bind_all("<MouseWheel>",
            lambda e: self.single_canvas.yview_scroll(int(-e.delta / 120), "units"))

        # 视频卡片数据
        self._single_videos = []       # 当前页视频列表
        self._single_check_vars = []   # [(BooleanVar, video_dict)]
        self._single_thumb_refs = []   # 保持图片引用防止 GC

        # ---- URL 输入（折叠式，可选） ----
        manual_frame = ttk.LabelFrame(self.tab_single, text="手动输入 URL（可选）", padding=5)
        manual_frame.pack(fill="x", padx=10, pady=(0, 5))

        row = ttk.Frame(manual_frame)
        row.pack(fill="x")
        self.url_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.url_var).pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.title_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.title_var, width=25).pack(side="left", padx=(0, 5))
        ttk.Button(row, text="下载", width=6, command=self._start_single_manual).pack(side="left")

    def _toggle_single_log(self):
        """展开/收起单视频日志"""
        self._single_log_visible = not self._single_log_visible
        if self._single_log_visible:
            self._single_log_frame.pack(fill="x", pady=(5, 0))
            self._single_log_toggle_btn.config(text="📋 日志 ▾")
        else:
            self._single_log_frame.pack_forget()
            self._single_log_toggle_btn.config(text="📋 日志 ▸")

    def _toggle_crawl_log(self):
        """展开/收起批量爬取日志"""
        self._crawl_log_visible = not self._crawl_log_visible
        if self._crawl_log_visible:
            self._crawl_log_frame.pack(fill="both", expand=True, pady=(5, 0))
            self._crawl_log_toggle_btn.config(text="📋 日志 ▾")
        else:
            self._crawl_log_frame.pack_forget()
            self._crawl_log_toggle_btn.config(text="📋 日志 ▸")

    def _toggle_search_log(self):
        """展开/收起搜索日志"""
        self._search_log_visible = not self._search_log_visible
        if self._search_log_visible:
            self._search_log_frame.pack(fill="both", expand=True, pady=(5, 0))
            self._search_log_toggle_btn.config(text="📋 日志 ▾")
        else:
            self._search_log_frame.pack_forget()
            self._search_log_toggle_btn.config(text="📋 日志 ▸")

    def _load_single_page(self):
        """加载当前页的视频列表"""
        page = self.single_page_var.get()
        site = self.single_site_var.get()
        type_name = self.single_type_var.get()
        list_key = LIST_TYPE_ALIASES.get(type_name, type_name)
        url_pattern = LIST_TYPES.get(list_key, "list-{page}.htm")

        self.single_status_label.config(text=f"正在加载第 {page} 页...")
        # 清空旧内容
        for w in self.single_inner_frame.winfo_children():
            w.destroy()
        self._single_videos.clear()
        self._single_check_vars.clear()
        self._single_thumb_refs.clear()

        def run():
            try:
                crawler = CrawlerCore(config={}, base_url=site)
                list_url = f"{site}/{url_pattern.format(page=page)}"
                videos = crawler._extract_video_urls(list_url)
            except Exception as e:
                self.root.after(0, lambda: self.single_status_label.config(text=f"加载失败: {e}"))
                return

            self.root.after(0, lambda: self._show_single_videos(videos))

        threading.Thread(target=run, daemon=True).start()

    # 封面缩略图尺寸（像素）
    THUMB_W = 160
    THUMB_H = 100

    def _show_single_videos(self, videos):
        """在网格中显示视频列表"""
        self._single_videos = videos

        if not videos:
            self.single_status_label.config(text="当前页没有视频")
            return

        self.single_status_label.config(text=f"第 {self.single_page_var.get()} 页 — 共 {len(videos)} 个视频")

        # 计算列数（根据窗口宽度自适应，默认 3 列）
        cols = 3
        tw, th = self.THUMB_W, self.THUMB_H
        for idx, video in enumerate(videos):
            row_idx = idx // cols
            col_idx = idx % cols

            # 卡片 Frame
            card = ttk.Frame(self.single_inner_frame, relief="groove", borderwidth=1)
            card.grid(row=row_idx, column=col_idx, padx=8, pady=8, sticky="nsew")
            self.single_inner_frame.columnconfigure(col_idx, weight=1)

            # 勾选框变量
            var = tk.BooleanVar(value=True)
            self._single_check_vars.append((var, video))
            cb = ttk.Checkbutton(card, variable=var)
            cb.grid(row=0, column=0, sticky="ne", padx=3, pady=3)

            # 封面图占位 Label（用 placeholder 图片撑出 160x100 像素）
            placeholder = tk.PhotoImage(width=tw, height=th)
            self._single_thumb_refs.append(placeholder)
            cover_label = tk.Label(card, image=placeholder, bg="#e0e0e0",
                                   text="加载中...", compound="center",
                                   font=("Arial", 9), fg="#999",
                                   cursor="hand2")
            cover_label.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=3, pady=(0, 3))

            # 标题
            title_label = tk.Label(card, text=video.get("title", "")[:50],
                                   font=("Arial", 9), wraplength=240, justify="left",
                                   anchor="w", cursor="hand2")
            title_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 5))

            # 异步加载封面
            cover_url = video.get("cover", "")
            if cover_url:
                threading.Thread(target=self._load_single_cover,
                                 args=(cover_url, cover_label, tw, th), daemon=True).start()

            # 绑定整个卡片的点击事件来切换勾选
            def toggle_check(event, v=var):
                v.set(not v.get())

            cover_label.bind("<Button-1>", toggle_check)
            title_label.bind("<Button-1>", toggle_check)
            card.bind("<Button-1>", toggle_check)

        self.single_select_all_var.set(True)

    def _load_single_cover(self, url, label, tw=160, th=100):
        """异步加载封面图，缩放到 tw x th 像素"""
        try:
            import urllib.request
            from io import BytesIO
            req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = BytesIO(resp.read())
            from PIL import Image, ImageTk
            img = Image.open(data)
            img = img.resize((tw, th), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._single_thumb_refs.append(photo)  # 防止 GC
            self.root.after(0, lambda: label.configure(image=photo, text=""))
        except Exception:
            self.root.after(0, lambda: label.configure(text="封面\n加载失败", bg="#f0f0f0"))

    def _single_toggle_all(self):
        """全选/取消全选"""
        select_all = self.single_select_all_var.get()
        for var, _ in self._single_check_vars:
            var.set(select_all)

    def _single_prev_page(self):
        page = self.single_page_var.get()
        if page > 1:
            self.single_page_var.set(page - 1)
            self._load_single_page()

    def _single_next_page(self):
        self.single_page_var.set(self.single_page_var.get() + 1)
        self._load_single_page()

    def _start_single_batch(self):
        """批量下载勾选的视频"""
        selected = [(var, video) for var, video in self._single_check_vars if var.get()]
        if not selected:
            messagebox.showwarning("警告", "请至少勾选一个视频")
            return

        if self.crawl_thread and self.crawl_thread.is_alive():
            messagebox.showwarning("警告", "正在运行中，请先停止")
            return

        # 强制清理旧线程引用
        self.crawl_thread = None
        self.crawler = None

        self._log_to_single_ui(f"准备下载 {len(selected)} 个视频")

        # 下载开始时自动展开日志
        if not self._single_log_visible:
            self._toggle_single_log()

        def on_progress(current, total):
            pct = f"{current}/{total}" if total > 0 else "?"
            self.root.after(0, lambda: self.single_progress.configure(value=current * 100 // max(total, 1)))
            self.root.after(0, lambda: self.single_slice_label.config(text=pct))
            if current <= 1:
                self.root.after(0, lambda: self.single_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.single_merge_label.config(text="切片下载中..."))

        try:
            self.crawler = CrawlerCore(
                self.config,
                log_callback=self._log_to_single_ui,
                progress_callback=on_progress,
                base_url=self.single_site_var.get(),
                merge_progress_callback=lambda p, s: self.root.after(0, lambda: [
                    self.single_merge_progress.configure(value=p),
                    self.single_merge_label.config(text=f"{p}%{f' {s}' if s else ''}")
                ]),
            )
        except Exception as e:
            self._log_to_single_ui(f"创建 CrawlerCore 失败: {e}")
            return

        def run():
            self._log_to_single_ui(f"下载线程已启动")
            success = 0
            skipped = 0
            total = len(selected)
            for i, (var, video) in enumerate(selected):
                if self.crawler._stop_flag:
                    self._log_to_single_ui("已停止")
                    break
                vid = video.get("id")
                title = video.get("title", "")
                url = video.get("url", "")
                self.root.after(0, lambda t=title, n=i+1, tn=total:
                    self.single_overall_label.config(text=f"[{n}/{tn}] {t[:40]}"))
                self._log_to_single_ui(f"开始处理: {title[:30]} (url={url[:60]})")
                try:
                    result = self.crawler.download_single(url, video_id=vid)
                    self._log_to_single_ui(f"  download_single 返回: {result}")
                    if result:
                        if vid and self.crawler._history.get(vid, {}).get("download_time"):
                            success += 1
                        else:
                            skipped += 1
                except Exception as e:
                    import traceback
                    self._log_to_single_ui(f"✗ 下载失败 [{title}]: {e}\n{traceback.format_exc()}")

            self.root.after(0, lambda: self.single_overall_label.config(
                text=f"完成 — 新下载: {success}，跳过: {skipped}"))
            self.root.after(0, lambda: self._log_to_single_ui(
                f"── 下载完成（新下载: {success}，跳过: {skipped}） ──"))
            # 刷新列表（更新已下载状态）

        self.crawl_thread = threading.Thread(target=run, daemon=True)
        self.crawl_thread.start()

    def _start_single_manual(self):
        """手动输入 URL 下载（保留原有功能）"""
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("警告", "请输入视频 URL")
            return
        if self.crawl_thread and self.crawl_thread.is_alive():
            messagebox.showwarning("警告", "正在运行中，请先停止")
            return

        title = self.title_var.get().strip() or None

        def on_progress(current, total):
            pct = f"{current}/{total}" if total > 0 else "?"
            self.root.after(0, lambda: self.single_progress.configure(value=current * 100 // max(total, 1)))
            self.root.after(0, lambda: self.single_slice_label.config(text=pct))
            if current <= 1:
                self.root.after(0, lambda: self.single_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.single_merge_label.config(text="切片下载中..."))

        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_single_ui,
            progress_callback=on_progress,
            base_url=self.single_site_var.get(),
            merge_progress_callback=lambda p, s: self.root.after(0, lambda: [
                self.single_merge_progress.configure(value=p),
                self.single_merge_label.config(text=f"{p}%{f' {s}' if s else ''}")
            ]),
        )

        def run():
            try:
                self.root.after(0, lambda: self.single_overall_label.config(text="正在下载..."))
                self.root.after(0, lambda: self.single_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.single_merge_label.config(text=""))
                self.crawler.download_single(url, title)
                self.root.after(0, lambda: self.single_overall_label.config(text="下载完成"))
                self._log_to_single_ui("── 下载完成 ──")
            except Exception as e:
                self._log_to_single_ui(f"错误: {e}")

        self.crawl_thread = threading.Thread(target=run, daemon=True)
        self.crawl_thread.start()

    def _log_to_crawl_ui(self, message, level="info"):
        """写入批量爬取 Tab 的日志框"""
        def _append():
            timestamp = time.strftime("%H:%M:%S")
            prefix = {"error": "✗", "warn": "⚠", "info": "ℹ"}.get(level, "·")
            self.crawl_status_text.insert("end", f"[{timestamp}] {prefix} {message}\n")
            self.crawl_status_text.see("end")
        try:
            self.root.after(0, _append)
        except Exception:
            pass

    def _log_to_search_ui(self, message, level="info"):
        """写入搜索 Tab 的日志框"""
        def _append():
            timestamp = time.strftime("%H:%M:%S")
            prefix = {"error": "✗", "warn": "⚠", "info": "ℹ"}.get(level, "·")
            self.search_status_text.insert("end", f"[{timestamp}] {prefix} {message}\n")
            self.search_status_text.see("end")
        try:
            self.root.after(0, _append)
        except Exception:
            pass

    def _log_to_single_ui(self, message, level="info"):
        """写入单视频 Tab 的日志框"""
        def _append():
            timestamp = time.strftime("%H:%M:%S")
            prefix = {"error": "✗", "warn": "⚠", "info": "ℹ"}.get(level, "·")
            self.single_log_text.insert("end", f"[{timestamp}] {prefix} {message}\n")
            self.single_log_text.see("end")
        try:
            self.root.after(0, _append)
        except Exception:
            pass

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

    # ==================== 运行日志 Tab ====================

    def _build_tab_log(self):
        """运行日志 Tab — 程序级日志，关闭即清空"""
        log_frame = ttk.Frame(self.tab_log)
        log_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # 说明文字
        ttk.Label(log_frame, text="程序运行日志（关闭程序后自动清空）",
                  font=("Arial", 9), foreground="#888").pack(anchor="w")

        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, wrap="word",
                                                   font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, pady=(5, 0))

        # 按钮行
        btn_frame = ttk.Frame(self.tab_log)
        btn_frame.pack(fill="x", padx=20, pady=(10, 10))
        ttk.Button(btn_frame, text="清空日志", command=self._clear_log).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="📁 导出日志...", command=self._export_log).pack(side="left", padx=5)

        # 重定向 Python logging 到此日志框
        self._log_handler = _UITextHandler(self.log_text)
        self._log_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self._log_handler)

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

    def _export_log(self):
        """导出运行日志到文件"""
        filepath = filedialog.asksaveasfilename(
            title="导出日志",
            defaultextension=".log",
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile=f"app_log_{time.strftime('%Y%m%d_%H%M%S')}.log"
        )
        if filepath:
            try:
                content = self.log_text.get("1.0", tk.END)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                messagebox.showinfo("导出成功", f"日志已保存到:\n{filepath}")
            except Exception as e:
                messagebox.showerror("导出失败", str(e))

    def _export_tab_log(self, tab_name: str):
        """导出指定 Tab 的日志到文件"""
        text_widget_map = {
            "批量爬取": self.crawl_status_text,
            "搜索": self.search_status_text,
            "单视频": self.single_log_text,
        }
        text_widget = text_widget_map.get(tab_name)
        if not text_widget:
            return

        filepath = filedialog.asksaveasfilename(
            title=f"导出{tab_name}日志",
            defaultextension=".log",
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile=f"{tab_name}_log_{time.strftime('%Y%m%d_%H%M%S')}.log"
        )
        if filepath:
            try:
                content = text_widget.get("1.0", tk.END)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                messagebox.showinfo("导出成功", f"{tab_name}日志已保存到:\n{filepath}")
            except Exception as e:
                messagebox.showerror("导出失败", str(e))

    # ==================== 日志/状态 UI 输出 ====================

    def _log_to_ui(self, text, level="info"):
        """记录日志到运行日志 Tab（通过 Python logging）"""
        log_level = {"error": logging.ERROR, "warn": logging.WARNING, "info": logging.INFO}.get(level, logging.INFO)
        logger.log(log_level, text)

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

        # 自动展开日志
        if not self._search_log_visible:
            self._toggle_search_log()

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
            # 新视频切片开始下载时，重置合并进度条
            if current <= 1:
                self.root.after(0, lambda: self.search_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.search_merge_label.config(text="切片下载中..."))

        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_search_ui,
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

        # 自动展开日志
        if not self._crawl_log_visible:
            self._toggle_crawl_log()


        def on_progress(current, total):
            """当前视频的切片进度"""
            pct = f"{current}/{total}" if total > 0 else "?"
            self._update_progress(
                self.crawl_progress, current, total,
                self.crawl_slice_label,
                f"切片: {pct}"
            )
            # 新视频切片开始下载时，重置合并进度条
            if current <= 1:
                self.root.after(0, lambda: self.crawl_merge_progress.configure(value=0))
                self.root.after(0, lambda: self.crawl_merge_label.config(text="切片下载中..."))

        self.crawler = CrawlerCore(
            self.config,
            log_callback=self._log_to_crawl_ui,
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

    # ==================== 停止 ====================

    def _stop_crawl(self):
        """停止任务"""
        if self.crawler:
            self.crawler.stop()
            # 不再在主线程 join，避免卡顿；线程设为 daemon 会自动清理
            self.crawl_thread = None
            self.crawler = None
            self._status_to_ui(self.crawl_status_text, "── 已停止 ──")
            self._status_to_ui(self.single_log_text, "── 已停止 ──")
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
