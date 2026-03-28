#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ml0987 视频下载器 - 核心爬虫模块
纯 requests 实现，无需 Selenium/浏览器驱动
支持 m3u8 解析、AES-128 解密、并发下载 ts 切片、ffmpeg 转 MP4
"""

import os
import re
import time
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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

# 通用请求头
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://ml0987.xyz/",
}

# ==================== 工具函数 ====================

def sanitize_filename(name: str) -> str:
    """清理文件名，移除非法字符"""
    for c in r'\/:*?"<>|':
        name = name.replace(c, '_')
    return name.strip().rstrip('.')


def http_get(url: str, timeout: int = 15, headers: dict = None, allow_redirects: bool = True) -> Optional[requests.Response]:
    """统一的 HTTP GET，返回 Response 或 None"""
    if not requests:
        raise ImportError("requests 未安装，请运行: pip install requests")
    try:
        return requests.get(url, timeout=timeout, headers=headers or DEFAULT_HEADERS,
                            allow_redirects=allow_redirects)
    except Exception as e:
        logger.error(f"请求失败 {url}: {e}")
        return None


def http_get_text(url: str, timeout: int = 15, headers: dict = None) -> Optional[str]:
    """GET 并返回文本内容"""
    resp = http_get(url, timeout=timeout, headers=headers)
    if resp and resp.status_code == 200:
        return resp.text
    return None


# ==================== M3U8 解析器 ====================

class M3U8Parser:
    """M3U8 解析器"""

    def __init__(self, m3u8_url: str, headers: dict = None):
        self.m3u8_url = m3u8_url
        self.base_url = m3u8_url.rsplit('/', 1)[0] if '/' in m3u8_url else m3u8_url
        self.headers = headers or DEFAULT_HEADERS
        self.segments: List[Tuple[str, Optional[bytes]]] = []
        self.key_url: Optional[str] = None
        self.is_encrypted = False
        self.is_master_playlist = False

    def parse(self) -> bool:
        """解析 m3u8 文件"""
        content = http_get_text(self.m3u8_url, headers=self.headers)
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

        sub = M3U8Parser(best_url, self.headers)
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
                 progress_callback=None):
        self.segments = segments
        self.output_file = output_file
        self.headers = headers or DEFAULT_HEADERS
        self.threads = threads or min(32, (os.cpu_count() or 1) + 4)
        self.key_url = key_url
        self.progress_callback = progress_callback
        self._key_cache: Optional[bytes] = None

    def download(self) -> bool:
        """并发下载并合并"""
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.output_file.with_suffix('.ts.tmp')

        try:
            with open(temp_file, 'wb') as f:
                with ThreadPoolExecutor(max_workers=self.threads) as executor:
                    future_to_index = {
                        executor.submit(self._download_segment, idx, url, iv): idx
                        for idx, (url, iv) in enumerate(self.segments)
                    }

                    results = [None] * len(self.segments)
                    completed_count = 0

                    for future in as_completed(future_to_index):
                        idx = future_to_index[future]
                        try:
                            results[idx] = future.result()
                        except Exception as e:
                            logger.error(f"切片 {idx+1} 失败: {e}")

                        completed_count += 1
                        if self.progress_callback:
                            self.progress_callback(completed_count, len(self.segments))

                    # 按顺序写入文件
                    for data in results:
                        if data:
                            f.write(data)

            return self._convert_to_mp4(temp_file)

        except Exception as e:
            logger.error(f"下载失败: {e}")
            return False

    def _download_segment(self, idx: int, url: str, iv: Optional[bytes]) -> Optional[bytes]:
        """下载单个切片（含解密）"""
        resp = http_get(url, timeout=30, headers=self.headers)
        if not resp or resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code if resp else 'no response'}")

        data = resp.content

        if self.key_url and iv and AES:
            try:
                if self._key_cache is None:
                    key_text = http_get_text(self.key_url)
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
            # 尝试系统 PATH
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

    BASE_URL = "https://ml0987.xyz"

    def __init__(self, config: dict, log_callback=None, progress_callback=None):
        self.config = config
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self._stop_flag = False
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

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

    # ==================== 单视频下载 ====================

    def download_single(self, url: str, title: str = None, output_dir: Path = None) -> bool:
        """下载单个视频"""
        if self._stop_flag:
            return False

        # 提取 m3u8
        m3u8_url = self._extract_m3u8_from_html(url)
        if not m3u8_url:
            self._log("未找到 m3u8 地址", "error")
            return False

        # 解析 m3u8
        parser = M3U8Parser(m3u8_url)
        if not parser.parse():
            self._log("m3u8 解析失败", "error")
            return False

        if not title:
            title = self._extract_title_from_html(url) or f"视频_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        if not output_dir:
            output_dir = Path(self.config.get("output_dir", "downloads"))

        date_dir = output_dir / datetime.now().strftime("%Y-%m-%d")
        title_dir = date_dir / sanitize_filename(title)
        mp4_file = title_dir / f"{sanitize_filename(title)}.mp4"

        if mp4_file.exists():
            self._log(f"文件已存在，跳过: {mp4_file.name}", "warn")
            return True

        self._log(f"开始下载: {title} ({len(parser.segments)} 个切片)")
        downloader = TSDownloader(
            parser.segments,
            mp4_file,
            key_url=parser.key_url,
            progress_callback=lambda c, t: self._progress(c, t)
        )

        success = downloader.download()

        if success:
            self._log(f"下载完成: {title}")
        else:
            self._log(f"下载失败: {title}", "error")

        return success

    # ==================== 批量爬取 ====================

    def crawl_batch(self, page_start: int, page_end: int, list_type: str = "list") -> int:
        """批量爬取"""
        total_success = 0

        # 获取列表 URL 模板（支持中文名映射）
        list_key = LIST_TYPE_ALIASES.get(list_type, list_type)
        url_pattern = LIST_TYPES.get(list_key)
        if not url_pattern:
            self._log(f"不支持的列表类型: {list_type}，使用默认 list", "warn")
            url_pattern = LIST_TYPES["list"]

        self._log(f"列表类型: {list_type}，页码范围: {page_start}-{page_end}")

        for page in range(page_start, page_end + 1):
            if self._stop_flag:
                break

            self._log(f"正在爬取第 {page} 页...")

            list_url = f"{self.BASE_URL}/{url_pattern.format(page=page)}"
            video_urls = self._extract_video_urls(list_url)
            if not video_urls:
                self._log(f"第 {page} 页未发现视频", "warn")
                continue

            self._log(f"发现 {len(video_urls)} 个视频")

            for idx, url in enumerate(video_urls, 1):
                if self._stop_flag:
                    break

                self._log(f"[{idx}/{len(video_urls)}] 下载: {url}")

                title = f"第{page}页_第{idx}个"

                if self.download_single(url, title):
                    total_success += 1

                time.sleep(2)

        self._log(f"批量爬取完成，成功: {total_success} 个")
        return total_success

    def _extract_video_urls(self, list_url: str) -> List[str]:
        """提取视频链接"""
        try:
            resp = http_get(list_url, timeout=15)
            if not resp or resp.status_code != 200:
                self._log(f"获取列表页失败: HTTP {resp.status_code if resp else '无响应'}", "error")
                return []

            pattern = r'href="(video-\d+\.htm)"'
            matches = list(dict.fromkeys(re.findall(pattern, resp.text)))  # 去重保序

            urls = [f"{self.BASE_URL}/{m}" for m in matches]
            return urls

        except Exception as e:
            self._log(f"提取视频链接失败: {e}", "error")
            return []
