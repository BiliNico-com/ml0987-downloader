#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ml0987 视频下载器 - 核心爬虫模块
支持 Selenium + CDP 嗅探 m3u8、AES-128 解密、并发下载 ts 切片
"""

import os
import sys
import json
import re
import time
import base64
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Optional, List, Dict, Tuple

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.edge.options import Options as EdgeOptions
except ImportError:
    webdriver = None

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
except ImportError:
    AES = None

try:
    import requests
except ImportError:
    requests = None

# ==================== 日志 ====================

logger = logging.getLogger(__name__)

# ==================== 工具函数 ====================

def sanitize_filename(name: str) -> str:
    """清理文件名，移除非法字符"""
    for c in r'\/:*?"<>|':
        name = name.replace(c, '_')
    return name.strip()

def get_content(url: str, timeout: int = 10, headers: dict = None) -> Optional[str]:
    """获取 URL 内容"""
    if not requests:
        return None
    
    try:
        resp = requests.get(url, timeout=timeout, headers=headers or {})
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.error(f"获取 {url} 失败: {e}")
    return None

# ==================== M3U8 解析器 ====================

class M3U8Parser:
    """M3U8 解析器"""
    
    def __init__(self, m3u8_url: str, headers: dict = None):
        self.m3u8_url = m3u8_url
        self.base_url = m3u8_url.rsplit('/', 1)[0] if '/' in m3u8_url else m3u8_url
        self.headers = headers or {}
        self.segments: List[Tuple[str, Optional[bytes]]] = []
        self.key_url: Optional[str] = None
        self.iv: Optional[bytes] = None
        self.is_encrypted = False
        self.is_master_playlist = False
        self.sub_streams: Dict[int, str] = {}
    
    def parse(self) -> bool:
        """解析 m3u8 文件"""
        content = get_content(self.m3u8_url, headers=self.headers)
        if not content:
            logger.error(f"无法获取 m3u8: {self.m3u8_url}")
            return False
        
        if '#EXT-X-STREAM-INF:' in content:
            self.is_master_playlist = True
            self._parse_master(content)
        else:
            self._parse_media(content)
        
        return True
    
    def _parse_master(self, content: str):
        """解析 master playlist"""
        pattern = r'#EXT-X-STREAM-INF:.*?RESOLUTION=(\d+)x(\d+).*?\n(.*?)\n'
        matches = re.findall(pattern, content)
        
        for width, height, url in matches:
            resolution = int(width) * int(height)
            self.sub_streams[resolution] = url
            logger.info(f"发现子流: {width}x{height} -> {url}")
        
        if self.sub_streams:
            best_resolution = max(self.sub_streams.keys())
            best_url = self.sub_streams[best_resolution]
            logger.info(f"选择最高分辨率: {best_resolution} -> {best_url}")
            
            sub_parser = M3U8Parser(self._resolve_url(best_url), self.headers)
            if sub_parser.parse():
                self.segments = sub_parser.segments
                self.key_url = sub_parser.key_url
                self.iv = sub_parser.iv
                self.is_encrypted = sub_parser.is_encrypted
    
    def _parse_media(self, content: str):
        """解析 media playlist"""
        lines = content.strip().split('\n')
        current_iv = None
        
        for line in lines:
            line = line.strip()
            
            if line.startswith('#EXT-X-KEY:'):
                self.is_encrypted = True
                uri_match = re.search(r'URI="([^"]+)"', line)
                if uri_match:
                    self.key_url = self._resolve_url(uri_match.group(1))
                iv_match = re.search(r'IV=0x([0-9a-fA-F]+)', line)
                if iv_match:
                    current_iv = bytes.fromhex(iv_match.group(1))
            
            elif not line.startswith('#') and line:
                segment_url = self._resolve_url(line)
                self.segments.append((segment_url, current_iv))
    
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
        self.headers = headers or {}
        # 动态线程数：基于 CPU 核心数，最多 32
        import os
        self.threads = threads or min(32, (os.cpu_count() or 1) + 4)
        self.key_url = key_url
        self.progress_callback = progress_callback
    
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
                    for future in as_completed(future_to_index):
                        idx = future_to_index[future]
                        try:
                            results[idx] = future.result()
                        except Exception as e:
                            logger.error(f"切片 {idx+1} 失败: {e}")
                        
                        completed = sum(1 for r in results if r is not None)
                        if self.progress_callback:
                            self.progress_callback(completed, len(self.segments))
                    
                    for data in results:
                        if data:
                            f.write(data)
            
            return self._convert_to_mp4(temp_file)
        
        except Exception as e:
            logger.error(f"下载失败: {e}")
            return False
    
    def _download_segment(self, idx: int, url: str, iv: Optional[bytes]) -> Optional[bytes]:
        """下载单个切片"""
        if not requests:
            raise ImportError("requests 未安装")
        
        resp = requests.get(url, headers=self.headers, timeout=30)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")
        
        data = resp.content
        
        if self.key_url and iv and AES:
            try:
                key = bytes.fromhex(get_content(self.key_url) or "")
                cipher = AES.new(key, AES.MODE_CBC, iv)
                data = cipher.decrypt(data)
            except Exception as e:
                logger.error(f"解密失败: {e}")
        
        return data
    
    def _convert_to_mp4(self, ts_file: Path) -> bool:
        """用 ffmpeg 转换为 MP4"""
        try:
            subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                check=True
            )
        except:
            logger.error("ffmpeg 未安装")
            return False
        
        cmd = [
            "ffmpeg", "-y",
            "-i", str(ts_file),
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            str(self.output_file)
        ]
        
        logger.info(f"执行: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info(f"转换成功: {self.output_file}")
            ts_file.unlink()
            return True
        else:
            logger.error(f"转换失败: {result.stderr}")
            return False

# ==================== 爬虫核心 ====================

class CrawlerCore:
    """爬虫核心类"""
    
    BASE_URL = "https://ml0987.xyz"
    
    def __init__(self, config: dict, log_callback=None, progress_callback=None):
        self.config = config
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.driver = None
        self._stop_flag = False
    
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
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
    
    # ==================== 浏览器控制 ====================
    
    def _create_driver(self):
        """创建浏览器驱动"""
        if not webdriver:
            raise ImportError("selenium 未安装")
        
        browser = self.config.get("browser", "chrome").lower()
        
        if browser == "edge":
            from selenium.webdriver.edge.options import Options as EdgeOptions
            options = EdgeOptions()
        else:
            options = Options()
        
        # 无头模式
        if self.config.get("headless", True):
            options.add_argument("--headless=new")
        
        # 代理
        if self.config.get("proxy_enabled"):
            host = self.config.get("proxy_host", "127.0.0.1")
            port = self.config.get("proxy_port", "1080")
            user = self.config.get("proxy_user", "")
            password = self.config.get("proxy_pass", "")
            
            if user and password:
                options.add_argument(f"--proxy-server=socks5://{user}:{password}@{host}:{port}")
            else:
                options.add_argument(f"--proxy-server=socks5://{host}:{port}")
        
        # 其他选项
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        # 创建驱动
        try:
            if browser == "edge":
                self.driver = webdriver.Edge(options=options)
            else:
                self.driver = webdriver.Chrome(options=options)
            
            self.driver.set_page_load_timeout(30)
            self._log(f"{browser.capitalize()} 启动成功")
        except Exception as e:
            self._log(f"{browser.capitalize()} 启动失败: {e}", "error")
            raise
    
    def _quit_driver(self):
        """关闭浏览器"""
        if self.driver:
            try:
                self.driver.quit()
                self.driver = None
            except:
                pass
    
    # ==================== 网络嗅探 ====================
    
    def _sniff_m3u8(self, url: str, wait_time: int = 15) -> Optional[str]:
        """嗅探 m3u8 URL"""
        self._log(f"正在嗅探: {url}")
        
        self._create_driver()
        
        try:
            self.driver.get(url)
            time.sleep(2)
            
            start_time = time.time()
            while time.time() - start_time < wait_time and not self._stop_flag:
                time.sleep(0.5)
                logs = self.driver.get_log("performance")
                
                for log in logs:
                    try:
                        message = json.loads(log["message"])["message"]
                        if message["method"] == "Network.responseReceived":
                            m3u8_url = message["params"]["response"]["url"]
                            if ".m3u8" in m3u8_url:
                                self._log(f"发现 m3u8: {m3u8_url}")
                                return m3u8_url
                    except:
                        pass
            
            self._log("未发现 m3u8 请求", "warn")
            return None
        
        except Exception as e:
            self._log(f"嗅探失败: {e}", "error")
            return None
        finally:
            self._quit_driver()
    
    # ==================== 单视频下载 ====================
    
    def download_single(self, url: str, title: str = None, output_dir: Path = None) -> bool:
        """下载单个视频"""
        if self._stop_flag:
            return False
        
        m3u8_url = self._sniff_m3u8(url)
        if not m3u8_url:
            self._log("未找到 m3u8 地址", "error")
            return False
        
        parser = M3U8Parser(m3u8_url)
        if not parser.parse():
            self._log("m3u8 解析失败", "error")
            return False
        
        if not title:
            title = f"视频_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        if not output_dir:
            output_dir = Path(self.config.get("output_dir", "downloads"))
        
        date_dir = output_dir / datetime.now().strftime("%Y-%m-%d")
        title_dir = date_dir / sanitize_filename(title)
        mp4_file = title_dir / f"{sanitize_filename(title)}.mp4"
        
        if mp4_file.exists():
            self._log(f"文件已存在: {mp4_file}", "warn")
            return True
        
        self._log(f"开始下载: {len(parser.segments)} 个切片")
        downloader = TSDownloader(
            parser.segments,
            mp4_file,
            headers={},
            threads=15,
            key_url=parser.key_url,
            progress_callback=lambda c, t: self._progress(c, t)
        )
        
        success = downloader.download()
        
        if success:
            self._log(f"下载完成: {mp4_file}")
        else:
            self._log("下载失败", "error")
        
        return success
    
    # ==================== 批量爬取 ====================
    
    def crawl_batch(self, page_start: int, page_end: int) -> int:
        """批量爬取"""
        total_success = 0
        
        for page in range(page_start, page_end + 1):
            if self._stop_flag:
                break
            
            self._log(f"正在爬取第 {page} 页...")
            
            list_url = f"{self.BASE_URL}/list_{page}.htm"
            
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
        if not requests:
            return []
        
        try:
            resp = requests.get(list_url, timeout=10)
            if resp.status_code != 200:
                self._log(f"获取列表页失败: {resp.status_code}", "error")
                return []
            
            pattern = r'href="(video-\d+\.htm)"'
            matches = re.findall(pattern, resp.text)
            
            urls = [f"{self.BASE_URL}/{m}" for m in matches]
            return urls
        
        except Exception as e:
            self._log(f"提取视频链接失败: {e}", "error")
            return []
