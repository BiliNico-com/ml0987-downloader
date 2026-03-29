#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ml0987 视频下载器 - 核心爬虫模块
纯 requests 实现，无需 Selenium/浏览器驱动
支持 m3u8 解析、AES-128 解密、并发下载 ts 切片、ffmpeg 转 MP4
"""

import os
import re
import json
import sys
import time
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, List, Dict, Tuple

try:
    import requests
except ImportError:
    requests = None

try:
    from Crypto.Cipher import AES
except ImportError:
    AES = None

# ==================== 日志 ====================

logger = logging.getLogger(__name__)

# ==================== 列表类型配置 ====================

LIST_TYPES = {
    "list":  "list-{page}.htm",       # 视频/Video list
    "top7":  "top7_list-{page}.htm",  # 周榜/Weekly top
    "top":   "top_list-{page}.htm",   # 月榜/Monthly top
    "5min":  "5min_list-{page}.htm",  # 5分钟+/5min+
    "long":  "long_list-{page}.htm",  # 10分钟+/10min+
}

# 中文名 -> 内部 key 映射
LIST_TYPE_ALIASES = {
    "视频":   "list",
    "周榜":   "top7",
    "月榜":   "top",
    "5分钟+": "5min",
    "10分钟+": "long",
}

# 可用域名列表
MIRROR_SITES = {
    "ml0987.xyz": "https://ml0987.xyz",
    "hsex.icu":   "https://hsex.icu",
    "hsex.men":   "https://hsex.men",
    "hsex.tv":    "https://hsex.tv",
}

# 通用请求头（Referer 使用占位符，运行时替换）
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "",  # 运行时根据域名设置
}

# ==================== 工具函数 ====================

def sanitize_filename(name: str) -> str:
    """清理文件名，移除非法字符"""
    for c in r'\/:*?"<>|':
        name = name.replace(c, '_')
    return name.strip().rstrip('.')


def parse_relative_time(text: str) -> Optional[datetime]:
    """将中文相对时间转换为日期（只取日期部分）"""
    now = datetime.now()
    text = text.strip()
    
    if not text:
        return None
    
    # 已经是日期格式: 2026-03-28
    m = re.match(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    
    # X分钟前
    m = re.match(r'(\d+)\s*分钟前', text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    
    # X小时前
    m = re.match(r'(\d+)\s*小时前', text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    
    # X天前
    m = re.match(r'(\d+)\s*天前', text)
    if m:
        return now - timedelta(days=int(m.group(1)))
    
    # X周前
    m = re.match(r'(\d+)\s*周前', text)
    if m:
        return now - timedelta(weeks=int(m.group(1)))
    
    # X个月前（粗略按30天算）
    m = re.match(r'(\d+)\s*个?月前', text)
    if m:
        return now - timedelta(days=int(m.group(1)) * 30)
    
    # X年前
    m = re.match(r'(\d+)\s*年?前', text)
    if m:
        return now - timedelta(days=int(m.group(1)) * 365)
    
    # 昨天 / 前天
    if '前天' in text:
        return now - timedelta(days=2)
    if '昨天' in text:
        return now - timedelta(days=1)
    if '今天' in text:
        return now
    
    return None


def http_get(url: str, timeout: int = 15, headers: dict = None, allow_redirects: bool = True,
             session: requests.Session = None) -> Optional[requests.Response]:
    """统一的 HTTP GET，返回 Response 或 None"""
    if not requests:
        raise ImportError("requests 未安装，请运行: pip install requests")
    try:
        http = session or requests
        return http.get(url, timeout=timeout, headers=headers or DEFAULT_HEADERS,
                        allow_redirects=allow_redirects)
    except Exception as e:
        logger.error(f"请求失败 {url}: {e}")
        return None


def http_get_text(url: str, timeout: int = 15, headers: dict = None,
                  session: requests.Session = None) -> Optional[str]:
    """GET 并返回文本内容"""
    resp = http_get(url, timeout=timeout, headers=headers, session=session)
    if resp and resp.status_code == 200:
        return resp.text
    return None


# ==================== SOCKS5 代理支持（本地 socks.py，无需 pip install） ====================

# 确保能导入项目目录下的 socks.py
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后
    _crawler_dir = Path(sys.executable).parent
else:
    _crawler_dir = Path(__file__).parent
if str(_crawler_dir) not in sys.path:
    sys.path.insert(0, str(_crawler_dir))

import socks as socks_module  # noqa: E402 — 本地 socks.py


def build_socks5_session(proxy_host: str, proxy_port: int,
                         proxy_user: str = None, proxy_pass: str = None) -> requests.Session:
    """创建使用 SOCKS5 代理的 requests Session"""
    import requests as req

    if proxy_user and proxy_pass:
        proxy_url = f"socks5h://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
    else:
        proxy_url = f"socks5h://{proxy_host}:{proxy_port}"

    session = req.Session()
    session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


# ==================== M3U8 解析器 ====================

class M3U8Parser:
    """M3U8 解析器"""

    def __init__(self, m3u8_url: str, headers: dict = None, session: requests.Session = None):
        self.m3u8_url = m3u8_url
        self.base_url = m3u8_url.rsplit('/', 1)[0] if '/' in m3u8_url else m3u8_url
        self.headers = headers or DEFAULT_HEADERS
        self.session = session
        self.segments: List[Tuple[str, Optional[bytes]]] = []
        self.key_url: Optional[str] = None
        self.is_encrypted = False
        self.is_master_playlist = False

    def parse(self) -> bool:
        """解析 m3u8 文件"""
        content = http_get_text(self.m3u8_url, headers=self.headers, session=self.session)
        if not content:
            logger.error(f"无法获取 m3u8: {self.m3u8_url}")
            return False

        if '#EXT-X-STREAM-INF:' in content:
            self.is_master_playlist = True
            return self._parse_master(content)
        else:
            self._parse_media(content)
            return bool(self.segments)

    def _parse_master(self, content: str) -> bool:
        """解析 master playlist，选择最高分辨率"""
        pattern = r'#EXT-X-STREAM-INF:.*?RESOLUTION=(\d+)x(\d+).*?\n(.*?)\n'
        matches = re.findall(pattern, content)
        if not matches:
            logger.error("master playlist 中未找到子流")
            return False

        best = max(matches, key=lambda m: int(m[0]) * int(m[1]))
        best_url = self._resolve_url(best[2].strip())
        logger.info(f"选择最高分辨率: {best[0]}x{best[1]} -> {best_url}")

        sub = M3U8Parser(best_url, self.headers, session=self.session)
        if sub.parse():
            self.segments = sub.segments
            self.key_url = sub.key_url
            self.is_encrypted = sub.is_encrypted
            return True
        return False

    def _parse_media(self, content: str):
        """解析 media playlist"""
        lines = content.strip().split('\n')
        current_iv = None

        for line in lines:
            line = line.strip()
            if line.startswith('#EXT-X-KEY:'):
                self.is_encrypted = True
                uri = re.search(r'URI="([^"]+)"', line)
                if uri:
                    self.key_url = self._resolve_url(uri.group(1))
                iv = re.search(r'IV=0x([0-9a-fA-F]+)', line)
                if iv:
                    current_iv = bytes.fromhex(iv.group(1))
            elif not line.startswith('#') and line:
                self.segments.append((self._resolve_url(line), current_iv))

    def _resolve_url(self, url: str) -> str:
        """解析相对 URL"""
        if url.startswith('http'):
            return url
        elif url.startswith('/'):
            parsed = urlparse(self.m3u8_url)
            return f"{parsed.scheme}://{parsed.netloc}{url}"
        else:
            return f"{self.base_url}/{url}"


# ==================== TS 下载器 ====================

class TSDownloader:
    """TS 切片下载器"""

    def __init__(self, segments: List[Tuple[str, Optional[bytes]]], output_file: Path,
                 headers: dict = None, threads: int = None, key_url: str = None,
                 progress_callback=None, stop_check=None, session: requests.Session = None):
        self.segments = segments
        self.output_file = output_file
        self.headers = headers or DEFAULT_HEADERS
        self.session = session
        self.threads = threads or min(32, (os.cpu_count() or 1) + 4)
        self.key_url = key_url
        self.progress_callback = progress_callback
        self.stop_check = stop_check  # 可调用对象，返回 True 时停止
        self._key_cache: Optional[bytes] = None
        self._stopped = False
        self._failed_indices: List[int] = []  # 记录最终仍失败的切片索引

    def download(self):
        """并发下载并合并，支持失败重试
        Returns:
            Tuple[bool, List[int]]: (是否成功, 仍失败的切片索引列表)
        """
        self._failed_indices = []
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.output_file.with_suffix('.ts.tmp')

        results = [None] * len(self.segments)
        failed_indices = set()

        try:
            # ------ 第一轮下载 ------
            with ThreadPoolExecutor(max_workers=self.threads) as executor:
                future_to_index = {
                    executor.submit(self._download_segment, idx, url, iv): idx
                    for idx, (url, iv) in enumerate(self.segments)
                }

                completed_count = 0
                for future in as_completed(future_to_index):
                    if self.stop_check and self.stop_check():
                        self._stopped = True
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    idx = future_to_index[future]
                    try:
                        results[idx] = future.result()
                    except Exception as e:
                        logger.error(f"切片 {idx+1} 失败: {e}")
                        failed_indices.add(idx)

                    completed_count += 1
                    if self.progress_callback:
                        self.progress_callback(completed_count, len(self.segments))

                if self._stopped:
                    logger.info("下载已中断")
                    return (False, [])

            # ------ 重试失败的切片（最多 3 轮）------
            for retry_round in range(1, 4):
                if not failed_indices:
                    break
                logger.info(f"重试第 {retry_round} 轮，{len(failed_indices)} 个切片失败")
                still_failed = set()

                with ThreadPoolExecutor(max_workers=self.threads) as executor:
                    future_to_index = {
                        executor.submit(self._download_segment, idx,
                                        self.segments[idx][0],
                                        self.segments[idx][1]): idx
                        for idx in failed_indices
                    }

                    for future in as_completed(future_to_index):
                        if self.stop_check and self.stop_check():
                            self._stopped = True
                            executor.shutdown(wait=False, cancel_futures=True)
                            break

                        idx = future_to_index[future]
                        try:
                            results[idx] = future.result()
                        except Exception as e:
                            logger.error(f"切片 {idx+1} 重试失败: {e}")
                            still_failed.add(idx)

                    failed_indices = still_failed

                if self._stopped:
                    return (False, list(failed_indices))

            # ------ 检查成功率 ------
            missing = [i for i, d in enumerate(results) if d is None]
            self._failed_indices = missing
            total = len(self.segments)
            success_count = total - len(missing)
            success_rate = success_count / total * 100

            if missing:
                logger.warning(f"有 {len(missing)} 个切片下载失败（{success_count}/{total}，成功率 {success_rate:.1f}%）")
                if success_rate < 50:
                    logger.error("成功率低于 50%，放弃转换")
                    try:
                        if temp_file.exists():
                            temp_file.unlink()
                    except Exception:
                        pass
                    return (False, missing)
                logger.warning("将写入可用切片继续尝试")

            # ------ 写入文件 ------
            with open(temp_file, 'wb') as f:
                for idx, data in enumerate(results):
                    if data:
                        f.write(data)
                    else:
                        logger.debug(f"跳过缺失切片 {idx+1}")

            ok = self._convert_to_mp4(temp_file)
            return (ok, missing if not ok else [])

        except Exception as e:
            logger.error(f"下载失败: {e}")
            try:
                if temp_file.exists():
                    temp_file.unlink()
                    logger.info(f"已清理临时文件: {temp_file}")
            except Exception:
                pass
            return (False, [])

    def _download_segment(self, idx: int, url: str, iv: Optional[bytes]) -> Optional[bytes]:
        """下载单个切片（含解密）"""
        resp = http_get(url, timeout=30, headers=self.headers, session=self.session)
        if not resp or resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code if resp else 'no response'}")

        data = resp.content

        if self.key_url and iv and AES:
            try:
                if self._key_cache is None:
                    key_text = http_get_text(self.key_url, session=self.session)
                    if key_text:
                        self._key_cache = bytes.fromhex(key_text.strip())

                if self._key_cache:
                    cipher = AES.new(self._key_cache, AES.MODE_CBC, iv)
                    data = cipher.decrypt(data)
            except Exception as e:
                logger.error(f"切片 {idx+1} 解密失败: {e}")

        return data

    def _convert_to_mp4(self, ts_file: Path) -> bool:
        """用 ffmpeg 转换为 MP4"""
        import sys as _sys

        if getattr(_sys, 'frozen', False):
            ffmpeg_bin = Path(_sys.executable).parent / "ffmpeg.exe"
        else:
            ffmpeg_bin = Path(__file__).parent / "ffmpeg.exe"

        if not ffmpeg_bin.exists():
            ffmpeg_bin = Path("ffmpeg")

        try:
            subprocess.run(
                [str(ffmpeg_bin), "-version"],
                capture_output=True, check=True, timeout=10
            )
        except Exception:
            logger.error(f"ffmpeg 未找到: {ffmpeg_bin}")
            return False

        cmd = [
            str(ffmpeg_bin), "-y",
            "-i", str(ts_file),
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            str(self.output_file)
        ]

        logger.info(f"执行: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            logger.info(f"转换成功: {self.output_file}")
            ts_file.unlink(missing_ok=True)
            return True
        else:
            logger.error(f"转换失败: {result.stderr[:500]}")
            return False


# ==================== 爬虫核心 ====================

class CrawlerCore:
    """爬虫核心类（纯 requests，无浏览器依赖）"""

    HISTORY_FILE = "download_history.json"

    def __init__(self, config: dict, log_callback=None, progress_callback=None,
                 info_callback=None, confirm_callback=None, base_url: str = None):
        self.config = config
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.info_callback = info_callback
        self.confirm_callback = confirm_callback  # 确认弹窗回调
        self._stop_flag = False
        self.base_url = (base_url or config.get("site", "https://ml0987.xyz")).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({**DEFAULT_HEADERS, "Referer": f"{self.base_url}/"})

        # SOCKS5 代理配置（纯 Python 实现，无需 PySocks）
        if config.get("proxy_enabled"):
            proxy_host = config.get("proxy_host", "").strip()
            proxy_port = config.get("proxy_port", "").strip()
            if proxy_host and proxy_port:
                proxy_user = config.get("proxy_user", "").strip() or None
                proxy_pass = config.get("proxy_pass", "").strip() or None
                self.session = build_socks5_session(proxy_host, proxy_port, proxy_user, proxy_pass)
                self._log(f"SOCKS5 代理已启用: {proxy_host}:{proxy_port}")
            else:
                self._log("代理已启用但未配置主机/端口", "warn")

        # 已下载记录（防重复）
        self._history = self._load_history()

    def _get_history_path(self) -> Path:
        """获取下载记录文件路径"""
        output_dir = Path(self.config.get("output_dir", "downloads"))
        return output_dir / self.HISTORY_FILE

    def _load_history(self) -> Dict[str, dict]:
        """加载已下载记录 {video_id: {title, date, url, ...}}"""
        path = self._get_history_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def _save_history(self):
        """保存已下载记录"""
        path = self._get_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._history, ensure_ascii=False, indent=2), encoding='utf-8')

    def _is_downloaded(self, video_id: str) -> bool:
        """检查视频是否已下载过"""
        return video_id in self._history

    def _mark_downloaded(self, video_id: str, title: str, url: str, upload_date: str = None):
        """标记视频已下载"""
        self._history[video_id] = {
            "title": title,
            "url": url,
            "upload_date": upload_date,
            "download_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_history()

    def _log(self, message: str, level: str = "info"):
        """日志回调"""
        logger.log(logging.getLevelName(level.upper()), message)
        if self.log_callback:
            self.log_callback(message, level)

    def _progress(self, current: int, total: int):
        """进度回调"""
        if self.progress_callback:
            self.progress_callback(current, total)

    def stop(self):
        """停止爬虫"""
        self._stop_flag = True

    # ==================== 从 HTML 提取 m3u8 ====================

    def _extract_m3u8_from_html(self, video_url: str) -> Optional[str]:
        """从视频页面 HTML 中直接提取 m3u8 地址（无需浏览器）"""
        self._log(f"正在获取: {video_url}")

        resp = http_get(video_url, timeout=15)
        if not resp or resp.status_code != 200:
            self._log(f"获取页面失败: HTTP {resp.status_code if resp else '无响应'}", "error")
            return None

        # 策略1: 从 <source> 标签的 src 属性提取
        match = re.search(r'<source[^>]+src="([^"]+\.m3u8[^"]*)"', resp.text)
        if match:
            m3u8_url = match.group(1)
            self._log(f"发现 m3u8: {m3u8_url}")
            return m3u8_url

        # 策略2: 搜索任何 .m3u8 URL
        match = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', resp.text)
        if match:
            m3u8_url = match.group(0)
            self._log(f"发现 m3u8: {m3u8_url}")
            return m3u8_url

        self._log("未在页面中找到 m3u8 地址", "warn")
        return None

    def _extract_title_from_html(self, video_url: str) -> Optional[str]:
        """从视频页面提取标题"""
        resp = http_get(video_url, timeout=15)
        if not resp or resp.status_code != 200:
            return None
        match = re.search(r'<title>([^<]+)</title>', resp.text)
        if match:
            title = match.group(1).strip()
            # 去掉网站后缀
            title = re.sub(r'\s*[-|]\s*好色.*?Tv\s*$', '', title)
            return title or None
        return None

    def _extract_upload_date_from_html(self, video_url: str) -> Optional[str]:
        """从视频详情页提取上传日期，返回 YYYY-MM-DD 格式"""
        resp = http_get(video_url, timeout=15)
        if not resp or resp.status_code != 200:
            return None
        # 匹配 "日期：xxx" 或 "日期:xxx"
        match = re.search(r'日期[：:]\s*([^<]+)', resp.text)
        if match:
            date_str = match.group(1).strip()
            dt = parse_relative_time(date_str)
            if dt:
                return dt.strftime("%Y-%m-%d")
        return None

    def _extract_author_from_html(self, video_url: str) -> Optional[str]:
        """从视频详情页提取上传者名称"""
        resp = http_get(video_url, timeout=15)
        if not resp or resp.status_code != 200:
            return None
        # 匹配 "作者：<a ...>名字</a>"
        match = re.search(r'作者[：:]\s*<a[^>]*>([^<]+)</a>', resp.text)
        if match:
            return match.group(1).strip()
        # 兜底：纯文本 "作者：xxx"（无链接）
        match = re.search(r'作者[：:]\s*([^\s<]+)', resp.text)
        if match:
            return match.group(1).strip()
        return None

    # ==================== 单视频下载 ====================

    def download_single(self, url: str, title: str = None, video_id: str = None, upload_date: str = None, output_dir: Path = None) -> bool:
        """下载单个视频
        
        Args:
            url: 视频页面 URL
            title: 视频标题
            video_id: 视频ID（用于防重复）
            upload_date: 上传日期 YYYY-MM-DD（用于分类存储）
            output_dir: 输出根目录
        """
        if self._stop_flag:
            return False

        # 防重复检查
        vid = video_id or self._extract_video_id(url)
        if vid and self._is_downloaded(vid):
            self._log(f"已下载过，跳过: {title or vid}", "warn")
            return True  # 返回 True 表示已处理（跳过也算成功）

        # 提取 m3u8
        m3u8_url = self._extract_m3u8_from_html(url)
        if not m3u8_url:
            self._log("未找到 m3u8 地址", "error")
            return False

        # 解析 m3u8
        parser = M3U8Parser(m3u8_url, session=self.session)
        if not parser.parse():
            self._log("m3u8 解析失败", "error")
            return False

        if not title:
            title = self._extract_title_from_html(url) or f"视频_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 标题加上传者（可选）
        if self.config.get("title_with_author"):
            author = self._extract_author_from_html(url)
            if author and author not in title:
                title = f"{title} - {author}"

        # 如果没有传入上传日期，尝试从详情页提取
        if not upload_date:
            upload_date = self._extract_upload_date_from_html(url)

        if not output_dir:
            output_dir = Path(self.config.get("output_dir", "downloads"))

        # 日期分类：启用按上传日期，否则全部存到下载当天
        if self.config.get("sort_by_upload_date", True):
            date_str = upload_date or datetime.now().strftime("%Y-%m-%d")
        else:
            date_str = datetime.now().strftime("%Y-%m-%d")

        date_dir = output_dir / date_str
        mp4_file = date_dir / f"{sanitize_filename(title)}.mp4"

        # 文件已存在也视为成功
        if mp4_file.exists():
            self._log(f"文件已存在，跳过: {mp4_file.name}", "warn")
            if vid:
                self._mark_downloaded(vid, title, url, upload_date)
            return True

        self._log(f"开始下载: {title} ({len(parser.segments)} 个切片)")
        if upload_date:
            self._log(f"  上传日期: {upload_date}")
        downloader = TSDownloader(
            parser.segments,
            mp4_file,
            key_url=parser.key_url,
            progress_callback=lambda c, t: self._progress(c, t),
            stop_check=lambda: self._stop_flag,
            session=self.session,
        )

        success, failed_segs = downloader.download()

        if success:
            if failed_segs:
                self._log(f"下载完成（有 {len(failed_segs)} 个切片缺失）: {title}", "warn")
            else:
                self._log(f"下载完成: {title}")
            if vid:
                self._mark_downloaded(vid, title, url, upload_date)
        else:
            self._log(f"下载失败: {title}", "error")

        return success

    def _extract_video_id(self, url: str) -> Optional[str]:
        """从 URL 中提取视频 ID"""
        m = re.search(r'video-(\d+)\.htm', url)
        if m:
            return m.group(1)
        return None

    # ==================== 批量爬取 ====================

    def crawl_batch(self, page_start: int, page_end: int, list_type: str = "list") -> dict:
        """批量爬取，返回 {success: int, skipped: int, total: int}"""
        total_success = 0
        total_skipped = 0

        # 获取列表 URL 模板（支持中文名映射）
        list_key = LIST_TYPE_ALIASES.get(list_type, list_type)
        url_pattern = LIST_TYPES.get(list_key)
        if not url_pattern:
            self._log(f"不支持的列表类型: {list_type}，使用默认 list", "warn")
            url_pattern = LIST_TYPES["list"]

        self._log(f"列表类型: {list_type}，页码范围: {page_start}-{page_end}")
        self._log(f"已下载记录: {len(self._history)} 个视频")

        for page in range(page_start, page_end + 1):
            if self._stop_flag:
                break

            self._log(f"正在爬取第 {page} 页...")

            list_url = f"{self.base_url}/{url_pattern.format(page=page)}"
            video_list = self._extract_video_urls(list_url)
            if not video_list:
                self._log(f"第 {page} 页未发现视频", "warn")
                continue

            self._log(f"发现 {len(video_list)} 个视频")

            for idx, video in enumerate(video_list, 1):
                if self._stop_flag:
                    break

                url = video["url"]
                vid = video.get("id")
                title = video.get("title") or f"第{page}页_第{idx}个"
                cover = video.get("cover") or ""

                # 防重复检查（提前检查，避免不必要的请求）
                if vid and self._is_downloaded(vid):
                    self._log(f"[{idx}/{len(video_list)}] 已下载过，跳过: {title}")
                    total_skipped += 1
                    continue

                self._log(f"[{idx}/{len(video_list)}] {title}")
                self._log(f"  {url}")

                # 通过 info_callback 传递封面信息给 UI
                if hasattr(self, 'info_callback') and self.info_callback and cover:
                    try:
                        self.info_callback({"title": title, "cover": cover})
                    except Exception:
                        pass

                try:
                    ok = self.download_single(url, title, video_id=vid)
                    if ok:
                        # 判断是真正下载了还是跳过了
                        if vid and self._history.get(vid, {}).get("download_time"):
                            total_success += 1
                        else:
                            total_skipped += 1
                except Exception as e:
                    self._log(f"下载过程出错，跳过继续: {title} ({e})", "error")

                time.sleep(2)

        self._log(f"批量爬取完成 — 新下载: {total_success}，跳过: {total_skipped}")
        return {"success": total_success, "skipped": total_skipped}

    # ==================== 搜索爬取 ====================

    def crawl_search(self, keyword: str, page_start: int = 1, page_end: int = 1,
                     sort: str = "new") -> dict:
        """按关键词搜索并批量下载，返回 {success: int, skipped: int}"""
        total_success = 0
        total_skipped = 0

        from urllib.parse import quote

        self._log(f"搜索关键词: {keyword}，排序: {sort}，页码: {page_start}-{page_end}")
        self._log(f"已下载记录: {len(self._history)} 个视频")

        for page in range(page_start, page_end + 1):
            if self._stop_flag:
                break

            self._log(f"正在搜索第 {page} 页...")

            search_url = f"{self.base_url}/search.htm?search={quote(keyword)}&sort={sort}&page={page}"
            video_list = self._extract_search_results(search_url)
            if not video_list:
                self._log(f"第 {page} 页未发现视频", "warn")
                continue

            self._log(f"发现 {len(video_list)} 个视频")

            for idx, video in enumerate(video_list, 1):
                if self._stop_flag:
                    break

                url = video["url"]
                vid = video.get("id")
                title = video.get("title") or f"搜索_第{page}页_第{idx}个"
                cover = video.get("cover") or ""

                # 防重复检查
                if vid and self._is_downloaded(vid):
                    self._log(f"[{idx}/{len(video_list)}] 已下载过，跳过: {title}")
                    total_skipped += 1
                    continue

                self._log(f"[{idx}/{len(video_list)}] {title}")
                self._log(f"  {url}")

                if hasattr(self, 'info_callback') and self.info_callback and cover:
                    try:
                        self.info_callback({"title": title, "cover": cover})
                    except Exception:
                        pass

                try:
                    ok = self.download_single(url, title, video_id=vid)
                    if ok:
                        if vid and self._history.get(vid, {}).get("download_time"):
                            total_success += 1
                        else:
                            total_skipped += 1
                except Exception as e:
                    self._log(f"下载过程出错，跳过继续: {title} ({e})", "error")

                time.sleep(2)

        self._log(f"搜索爬取完成 — 新下载: {total_success}，跳过: {total_skipped}")
        return {"success": total_success, "skipped": total_skipped}

    def _extract_search_results(self, search_url: str) -> List[dict]:
        """从搜索结果页提取视频链接，返回 [{'url', 'id', 'title', 'cover'}, ...]"""
        try:
            resp = http_get(search_url, timeout=15)
            if not resp or resp.status_code != 200:
                self._log(f"获取搜索页失败: HTTP {resp.status_code if resp else '无响应'}", "error")
                return []

            videos = []
            seen_ids = set()

            # 策略1: 列表页格式（带封面图的 <a> 标签）
            for m in re.finditer(
                r'<a[^>]*href="(video-(\d+)\.htm)"[^>]*>\s*<div[^>]*style="[^"]*background-image:\s*url\(["\']([^"\']+)["\']\)[^"]*"\s*title=\s*"([^"]*)"',
                resp.text
            ):
                href, vid, cover, title = m.group(1), m.group(2), m.group(3), m.group(4).strip()
                if not cover.startswith('http'):
                    cover = f"https://img.ml0987.com{cover}"
                if vid not in seen_ids:
                    seen_ids.add(vid)
                    videos.append({
                        "url": f"{self.base_url}/{href}",
                        "id": vid,
                        "title": title,
                        "cover": cover,
                    })

            # 策略2: 搜索结果格式（<h4><a href="video-xxx.htm">标题</a></h4>）
            if not videos:
                for m in re.finditer(r'<h4>\s*<a[^>]*href="(video-(\d+)\.htm)"[^>]*>([^<]+)</a>', resp.text):
                    href, vid, title = m.group(1), m.group(2), m.group(3).strip()
                    # 尝试找封面图：在同一个容器内搜索 background-image
                    cover = ""
                    pos = m.start()
                    block = resp.text[max(0, pos - 500):pos]
                    cover_m = re.search(r'background-image:\s*url\(["\']([^"\']+)["\']\)', block)
                    if cover_m:
                        cover = cover_m.group(1)
                        if not cover.startswith('http'):
                            cover = f"https://img.ml0987.com{cover}"
                    if vid not in seen_ids:
                        seen_ids.add(vid)
                        videos.append({
                            "url": f"{self.base_url}/{href}",
                            "id": vid,
                            "title": title,
                            "cover": cover,
                        })

            return videos

        except Exception as e:
            self._log(f"提取搜索结果失败: {e}", "error")
            return []

    def search_authors(self, keyword: str) -> List[dict]:
        """搜索作者，返回 [{'name', 'url', 'count'}, ...]"""
        from urllib.parse import quote
        search_url = f"{self.base_url}/search.htm?search={quote(keyword)}"
        try:
            resp = http_get(search_url, timeout=15)
            if not resp or resp.status_code != 200:
                self._log(f"搜索作者失败: HTTP {resp.status_code if resp else '无响应'}", "error")
                return []
        except Exception as e:
            self._log(f"搜索作者失败: {e}", "error")
            return []

        authors = []
        seen_names = set()
        # 匹配作者链接（带 badge 的，即搜索结果区的按钮样式）
        # <a class="btn btn-default" href="user.htm?author=xxx" role="button">&nbsp;名字&nbsp;<span class="badge">数量</span></a>
        for m in re.finditer(
            r'<a[^>]*class="[^"]*btn[^"]*"[^>]*href="user\.htm\?author=([^"]+)"[^>]*>\s*(&nbsp;)*([^<&]+)\s*(&nbsp;)*\s*<span[^>]*class="[^"]*badge[^"]*"[^>]*>\s*(\d+)\s*</span>\s*</a>',
            resp.text
        ):
            author_param = m.group(1)
            name_part = m.group(3).strip()
            count = int(m.group(5))
            if name_part not in seen_names:
                seen_names.add(name_part)
                authors.append({
                    "name": name_part or author_param,
                    "param": author_param,
                    "url": f"{self.base_url}/user.htm?author={author_param}",
                    "count": count,
                })

        return authors

    def get_author_page_count(self, author_url: str) -> int:
        """获取作者视频的总页数（从分页导航中提取最大数字页码）"""
        try:
            resp = http_get(author_url, timeout=15)
            if not resp or resp.status_code != 200:
                return 1
            # 匹配分页链接: <a href="user-{数字}.htm?author=xxx">数字</a>
            # 提取所有纯数字页码（排除 ▶ 等非数字内容）
            page_nums = []
            for m in re.finditer(
                r'<a[^>]*href="user-(\d+)\.htm\?author=[^"]*"[^>]*>\s*(\d+)\s*</a>',
                resp.text
            ):
                page_num = int(m.group(1))
                link_text = m.group(2).strip()
                # 确保链接文本也是数字（排除 ▶ 等）
                if link_text.isdigit():
                    page_nums.append(page_num)
            return max(page_nums) if page_nums else 1
        except Exception as e:
            self._log(f"获取作者页数失败: {e}", "warn")
            return 1

    def crawl_authors(self, authors: List[dict], page_start: int = 1, page_end: int = 1) -> dict:
        """爬取指定作者的视频列表并下载，返回 {success: int, skipped: int}
        
        下载的视频会放到 downloads/{日期}/{作者ID}/ 目录下方便归档
        每个作者完成后会弹出提示（若有失败视频则询问是否重试），
        全部作者完成后询问是否继续下一作者，支持 10 秒倒计时自动确认。
        """
        total_success = 0
        total_skipped = 0
        # 按作者记录失败视频: { author_name: [(url, title, vid), ...] }
        failed_by_author: Dict[str, List[tuple]] = {}

        for author_info in authors:
            if self._stop_flag:
                break

            author_name = author_info.get("name", "未知作者")
            author_param = author_info.get("param", author_name)
            author_url = author_info.get("url", "")
            self._log(f"===== 开始爬取作者: {author_name} =====")

            failed_by_author[author_name] = []
            author_success = 0
            author_skipped = 0
            # 作者子目录名：使用作者 param（ID），清理非法字符
            author_dir_name = sanitize_filename(author_param)

            for page in range(page_start, page_end + 1):
                if self._stop_flag:
                    break

                self._log(f"  第 {page} 页...")

                # 作者分页 URL 格式: user-{page}.htm?author=xxx
                if page == 1:
                    list_url = author_url
                else:
                    from urllib.parse import urlparse, parse_qs, urlencode
                    parsed = urlparse(author_url)
                    params = parse_qs(parsed.query)
                    list_url = f"{self.base_url}/user-{page}.htm?{urlencode(params, doseq=True)}"

                video_list = self._extract_video_urls(list_url)
                if not video_list:
                    self._log(f"  第 {page} 页未发现视频", "warn")
                    continue

                self._log(f"  发现 {len(video_list)} 个视频")

                for idx, video in enumerate(video_list, 1):
                    if self._stop_flag:
                        break

                    url = video["url"]
                    vid = video.get("id")
                    title = video.get("title") or f"{author_name}_第{page}页_第{idx}个"
                    cover = video.get("cover") or ""

                    if vid and self._is_downloaded(vid):
                        self._log(f"  [{idx}/{len(video_list)}] 已下载过，跳过: {title}")
                        total_skipped += 1
                        author_skipped += 1
                        continue

                    self._log(f"  [{idx}/{len(video_list)}] {title}")

                    if hasattr(self, 'info_callback') and self.info_callback and cover:
                        try:
                            self.info_callback({"title": title, "cover": cover})
                        except Exception:
                            pass

                    # 构造作者专属输出目录: downloads/{日期}/{作者ID}/
                    output_root = Path(self.config.get("output_dir", "downloads"))
                    if self.config.get("sort_by_upload_date", True):
                        date_str = datetime.now().strftime("%Y-%m-%d")
                    else:
                        date_str = datetime.now().strftime("%Y-%m-%d")
                    author_output_dir = output_root / date_str / author_dir_name

                    try:
                        ok = self.download_single(url, title, video_id=vid, output_dir=author_output_dir)
                        if ok:
                            if vid and self._history.get(vid, {}).get("download_time"):
                                total_success += 1
                                author_success += 1
                            else:
                                total_skipped += 1
                                author_skipped += 1
                        else:
                            # 下载失败（含切片缺失），记录到失败列表
                            failed_by_author[author_name].append((url, title, vid))
                    except Exception as e:
                        self._log(f"下载过程出错，跳过继续: {title} ({e})", "error")
                        failed_by_author[author_name].append((url, title, vid))

                    time.sleep(2)

            # ===== 作者下载完毕，弹出提示 =====
            total_videos = author_success + author_skipped + len(failed_by_author[author_name])
            self._log(f"===== 作者 {author_name} 完成：已下载 {author_success + author_skipped}/{total_videos} =====")

            if self.confirm_callback:
                failed = failed_by_author[author_name]
                if failed:
                    # 有失败视频，询问是否重试
                    msg = (
                        f"作者「{author_name}」视频下载完成\n"
                        f"已下载: {author_success + author_skipped} / 共 {total_videos}\n"
                        f"未完成: {len(failed)} 个\n\n"
                        f"是否重新下载未完成的 {len(failed)} 个视频？"
                    )
                    choice = self.confirm_callback({
                        "title": "作者视频下载完成",
                        "message": msg,
                        "choices": [("retry", f"是，重新下载 ({len(failed)} 个)"), ("skip", "否，跳过")],
                        "default": "retry",
                        "countdown": 10,
                    })
                    if choice == "retry" and not self._stop_flag:
                        self._log(f"开始重试 {len(failed)} 个未完成视频...")
                        for url, title, vid in failed:
                            if self._stop_flag:
                                break
                            try:
                                ok = self.download_single(url, title, video_id=vid, output_dir=author_output_dir)
                                if ok:
                                    if vid and self._history.get(vid, {}).get("download_time"):
                                        total_success += 1
                                        author_success += 1
                                    else:
                                        total_skipped += 1
                                        author_skipped += 1
                                    # 重试成功的从失败列表移除
                                    failed_by_author[author_name].remove((url, title, vid))
                            except Exception as e:
                                self._log(f"重试失败: {title} ({e})", "error")
                            time.sleep(2)
                        self._log(f"重试完成，剩余 {len(failed_by_author[author_name])} 个未完成")

            # ===== 询问是否继续下一作者 =====
            remaining = authors.index(author_info) + 1
            if remaining < len(authors) and self.confirm_callback and not self._stop_flag:
                next_author = authors[remaining].get("name", "未知作者")
                still_failed = len(failed_by_author[author_name])
                msg = (
                    f"作者「{author_name}」全部处理完毕\n"
                    f"已下载: {author_success + author_skipped}/{total_videos}"
                    + (f"\n未完成: {still_failed} 个" if still_failed else "")
                    + f"\n\n是否继续下载下一作者「{next_author}」？"
                )
                choice = self.confirm_callback({
                    "title": "是否继续下一作者",
                    "message": msg,
                    "choices": [("yes", "是，继续下载"), ("no", "否，停止")],
                    "default": "yes",
                    "countdown": 10,
                })
                if choice != "yes":
                    self._log("用户停止，退出批量下载")
                    break

        self._log(f"作者爬取完成 — 新下载: {total_success}，跳过: {total_skipped}")
        return {"success": total_success, "skipped": total_skipped}

    def _extract_video_urls(self, list_url: str) -> List[dict]:
        """提取视频链接，返回 [{'url', 'id', 'title', 'cover'}, ...]"""
        try:
            resp = http_get(list_url, timeout=15)
            if not resp or resp.status_code != 200:
                self._log(f"获取列表页失败: HTTP {resp.status_code if resp else '无响应'}", "error")
                return []

            videos = []
            seen_ids = set()

            # 提取带封面图的 <a> 标签中的信息（支持单引号和双引号包裹 URL）
            for m in re.finditer(
                r'<a[^>]*href="(video-(\d+)\.htm)"[^>]*>\s*<div[^>]*style="[^"]*background-image:\s*url\(["\']([^"\']+)["\']\)[^"]*"\s*title=\s*"([^"]*)"',
                resp.text
            ):
                href, vid, cover, title = m.group(1), m.group(2), m.group(3), m.group(4).strip()
                # 补全完整 URL（如果是相对路径）
                if not cover.startswith('http'):
                    cover = f"https://img.ml0987.com{cover}"
                if vid not in seen_ids:
                    seen_ids.add(vid)
                    videos.append({
                        "url": f"{self.base_url}/{href}",
                        "id": vid,
                        "title": title,
                        "cover": cover,
                    })

            return videos

        except Exception as e:
            self._log(f"提取视频链接失败: {e}", "error")
            return []
