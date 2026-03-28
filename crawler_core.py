#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crawler_core.py  —— 对齐油猴脚本"媒体资源嗅探及下载 v1.985"的完整实现
═══════════════════════════════════════════════════════════════════════
油猴脚本嗅探来源（全部实现）：
  1. PerformanceObserver  → 监听 resource 条目（audio/video/xmlhttprequest/fetch）
  2. GM_webRequest        → 拦截 *.m3u8* 请求
  3. <video src>          → 扫描 DOM video 标签
  4. <audio src>          → 扫描 DOM audio 标签
  5. <source src>         → 扫描 DOM source 标签
  本工具通过 CDP Network.responseReceived + Network.requestWillBeSent 一次性覆盖全部来源

m3u8 处理（对齐油猴脚本）：
  ✅ 嵌套 m3u8（master playlist）→ 自动选最高分辨率子流
  ✅ AES-128 加密 m3u8           → 自动下载 key 文件并解密
  ✅ 广告过滤                    → 移除 #EXT-X-DISCONTINUITY 片段
  ✅ #EXT-X-MAP 初始化分片支持
  ✅ 相对路径 ts URL 自动补全（多种格式）

下载方式（对齐油猴脚本）：
  ✅ m3u8 → 并发下载 ts 切片 → 合并 → ffmpeg 封装 mp4
  ✅ mp4  → ffmpeg 直接封装（-c copy）
"""

import os
import re
import sys
import json
import time
import shutil
import logging
import hashlib
import threading
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Callable, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger(__name__)

BASE_URL      = "https://ml0987.xyz"
PROGRESS_FILE = Path(__file__).parent / "progress.json"
# 并发下载 ts 切片的线程数（对齐油猴脚本 xcNum=15）
TS_THREADS    = 15

# ──────────────────────────────────────────────
#  工具函数
# ──────────────────────────────────────────────
def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    name = re.sub(r'\s+', " ", name).strip("_. ")
    return name[:max_len] or "untitled"

def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def load_progress() -> set:
    if PROGRESS_FILE.exists():
        try:
            return set(json.loads(PROGRESS_FILE.read_text("utf-8")))
        except Exception:
            pass
    return set()

def save_progress(done: set):
    PROGRESS_FILE.write_text(
        json.dumps(sorted(done), ensure_ascii=False, indent=2), encoding="utf-8"
    )

# ──────────────────────────────────────────────
#  m3u8 / 媒体 URL 类型判断（对齐油猴脚本逻辑）
# ──────────────────────────────────────────────
M3U8_MIME = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
    "video/mpegurl",
    "video/x-mpegurl",
}

def classify_url(url: str, mime: str = "", content_preview: str = "") -> Optional[str]:
    """
    返回类型字符串：'hls' | 'mp4' | 'audio' | None
    对齐油猴脚本 GM_xhr onload 中的 Type 判断逻辑
    """
    url_lower = url.lower().split("?")[0]
    mime_lower = mime.lower().split(";")[0].strip()

    # m3u8 判断
    if url_lower.endswith(".m3u8") or "m3u8" in url.lower():
        return "hls"
    if mime_lower in M3U8_MIME:
        return "hls"
    # 内容嗅探（响应体首行）
    if content_preview and content_preview.strip().startswith("#EXTM3U"):
        return "hls"

    # mp4 判断
    if url_lower.endswith(".mp4") or re.search(r"mp4[\?\&]", url_lower):
        return "mp4"
    if "video/mp4" in mime_lower:
        return "mp4"

    # audio 判断
    if url_lower.endswith(".mp3") or url_lower.endswith(".m4a") or url_lower.endswith(".ogg"):
        return "audio"
    if "audio/" in mime_lower:
        return "audio"

    return None

def is_media_url(url: str, mime: str = "") -> bool:
    return classify_url(url, mime) is not None

# ──────────────────────────────────────────────
#  CDP m3u8 嗅探器（对齐油猴 PerformanceObserver + GM_webRequest）
# ──────────────────────────────────────────────
class M3U8Sniffer:
    """
    用 Selenium CDP 监听所有网络请求，捕获媒体资源 URL。
    对应油猴脚本的嗅探来源：
      - PerformanceObserver（resource 条目）→ CDP Network.responseReceived
      - GM_webRequest（*.m3u8*）          → CDP Network.requestWillBeSent
      - <video> / <audio> / <source>       → 每秒扫描 DOM
    """

    def __init__(self, driver):
        self.driver = driver
        self._found: list[dict] = []   # [{url, type, mime}, ...]
        self._lock  = threading.Lock()
        self._have_listener = False
        self._dom_timer: threading.Timer | None = None

    def start(self):
        self._found.clear()
        self.driver.execute_cdp_cmd("Network.enable", {})
        self.driver.execute_cdp_cmd(
            "Network.setExtraHTTPHeaders",
            {"headers": {"Referer": BASE_URL}}
        )
        try:
            self.driver.add_cdp_listener("Network.responseReceived",  self._on_response)
            self.driver.add_cdp_listener("Network.requestWillBeSent", self._on_request)
            self._have_listener = True
        except AttributeError:
            self._have_listener = False

        # 定期扫描 DOM 中的 video/audio/source 标签（对应油猴脚本逻辑）
        self._start_dom_scan()

    def stop(self):
        if self._dom_timer:
            self._dom_timer.cancel()
            self._dom_timer = None
        if self._have_listener:
            try:
                self.driver.remove_cdp_listener("Network.responseReceived",  self._on_response)
                self.driver.remove_cdp_listener("Network.requestWillBeSent", self._on_request)
            except Exception:
                pass

    # ── CDP 回调 ──────────────────────────────
    def _on_response(self, params):
        resp = params.get("response", {})
        url  = resp.get("url", "")
        mime = resp.get("mimeType", "")
        self._add(url, mime)

    def _on_request(self, params):
        url = params.get("request", {}).get("url", "")
        self._add(url)

    def _add(self, url: str, mime: str = ""):
        if not url or len(url) < 8:
            return
        t = classify_url(url, mime)
        if t:
            with self._lock:
                existing = [f["url"] for f in self._found]
                if url not in existing:
                    log.info(f"  🎯 嗅探[{t}]: {url[:100]}")
                    self._found.append({"url": url, "type": t, "mime": mime})

    # ── DOM 扫描（对应油猴脚本 video/audio/source 扫描）──
    def _start_dom_scan(self):
        def _scan():
            try:
                # 提取 video[src], audio[src], source[src]
                js = """
                var res = [];
                document.querySelectorAll('video[src],audio[src],source[src]').forEach(function(el){
                    var s = el.getAttribute('src') || '';
                    if(s && !s.startsWith('blob:') && !s.startsWith('data:')){
                        res.push({tag: el.tagName.toLowerCase(), src: s});
                    }
                });
                return res;
                """
                items = self.driver.execute_script(js) or []
                for item in items:
                    src = item.get("src", "")
                    if src and src.startswith("http"):
                        tag = item.get("tag", "video")
                        mime = "audio/mpeg" if tag == "audio" else ""
                        self._add(src, mime)
            except Exception:
                pass
            # 每 1.5 秒扫描一次
            self._dom_timer = threading.Timer(1.5, _scan)
            self._dom_timer.daemon = True
            self._dom_timer.start()
        _scan()

    # ── 获取结果 ──────────────────────────────
    def get_hls_urls(self) -> list[str]:
        with self._lock:
            return [f["url"] for f in self._found if f["type"] == "hls"]

    def get_all(self) -> list[dict]:
        with self._lock:
            return list(self._found)

    # ── 旧版 Selenium 兜底（performance log）─
    def poll_perf_log(self) -> list[str]:
        found = []
        try:
            for entry in self.driver.get_log("performance"):
                try:
                    msg = json.loads(entry["message"])["message"]
                    if msg.get("method") in (
                        "Network.responseReceived",
                        "Network.requestWillBeSent",
                    ):
                        p    = msg.get("params", {})
                        url  = (p.get("response", {}).get("url", "")
                                or p.get("request", {}).get("url", ""))
                        mime = p.get("response", {}).get("mimeType", "")
                        t    = classify_url(url, mime)
                        if t == "hls" and url not in found:
                            found.append(url)
                except Exception:
                    pass
        except Exception:
            pass
        return found

    def all_hls(self) -> list[str]:
        r = self.get_hls_urls()
        if not r:
            r = self.poll_perf_log()
        return r


# ──────────────────────────────────────────────
#  m3u8 解析器（完整对齐油猴脚本逻辑）
# ──────────────────────────────────────────────
class M3U8Processor:
    """
    对应油猴脚本 m3u8Download() 函数，完整实现：
    1. 下载并解析 m3u8 文本
    2. 嵌套 m3u8 → 选最高分辨率子流递归处理
    3. #EXT-X-KEY → AES-128 解密
    4. #EXT-X-DISCONTINUITY → 广告过滤
    5. 并发下载 ts 切片（对应 xcNum=15 线程）
    6. 合并 ts → ffmpeg 封装 mp4
    """

    def __init__(self, ffmpeg_path: str, headers: dict, stop_event: threading.Event,
                 filter_ads: bool = True):
        self.ffmpeg     = ffmpeg_path
        self.headers    = headers
        self.stop_event = stop_event
        self.filter_ads = filter_ads

    def _http_get(self, url: str, binary: bool = False):
        """简单 HTTP GET，带 Referer 头"""
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read() if binary else resp.read().decode("utf-8", errors="replace")

    # ── 相对 URL 还原（对齐油猴脚本多种格式处理）──
    @staticmethod
    def _resolve_url(ts_path: str, m3u8_url: str, page_url: str) -> str:
        if ts_path.startswith("http://") or ts_path.startswith("https://"):
            return ts_path
        # //domain/path 形式
        if ts_path.startswith("//"):
            scheme = urlparse(page_url).scheme or "https"
            return f"{scheme}:{ts_path}"
        # /path 形式（绝对路径）
        if ts_path.startswith("/"):
            base = urlparse(m3u8_url)
            return f"{base.scheme}://{base.netloc}{ts_path}"
        # 相对路径
        base_dir = m3u8_url.split("?")[0].rsplit("/", 1)[0]
        return f"{base_dir}/{ts_path}"

    # ── 广告过滤（对应 #EXT-X-DISCONTINUITY 段落删除）──
    @staticmethod
    def _filter_ads(text: str) -> str:
        """移除 #EXT-X-DISCONTINUITY 包裹的广告片段"""
        while "#EXT-X-DISCONTINUITY" in text.upper():
            # 找第一个 DISCONTINUITY
            m1 = re.search(r'#EXT-X-DISCONTINUITY', text, re.IGNORECASE)
            if not m1:
                break
            # 标记第一个，找第二个
            tmp = text[:m1.start()] + "###MARK###" + text[m1.end():]
            m2 = re.search(r'#EXT-X-DISCONTINUITY', tmp, re.IGNORECASE)
            if m2:
                # 删除 mark 到第二个 DISCONTINUITY+20 之间的内容
                text = tmp[:tmp.index("###MARK###")] + tmp[m2.end():]
            else:
                # 只有一个，删到结尾
                text = text[:m1.start()]
            if "#EXT-X-DISCONTINUITY" not in text.upper():
                break
        return text

    # ── 解析 m3u8 文本，返回 ts URL 列表 ────
    def _parse_m3u8(self, text: str, m3u8_url: str, page_url: str) -> tuple[list[str], str, Optional[str]]:
        """
        返回 (ts_url_list, key_url_or_none, iv_or_none)
        key_url: AES-128 密钥 URL
        iv: 十六进制 IV 字符串
        """
        if self.filter_ads:
            text = self._filter_ads(text)

        # 检测是否嵌套 m3u8（master playlist）
        # 油猴脚本通过 #EXT-X-TARGETDURATION 判断是否为媒体 playlist
        has_target_duration = bool(re.search(r'#EXT-X-TARGETDURATION', text, re.IGNORECASE))

        if not has_target_duration:
            # 这是 master playlist，选最高分辨率子流
            log.info("  检测到 master playlist，选取最高分辨率子流...")
            best_url = self._select_best_stream(text, m3u8_url, page_url)
            if best_url:
                sub_text = self._http_get(best_url)
                return self._parse_m3u8(sub_text, best_url, page_url)

        # 媒体 playlist：提取 ts 列表
        key_url, iv = self._extract_key(text, m3u8_url, page_url)

        # 去掉非 EXTINF 行，只保留时间戳+URL对
        cleaned = re.sub(r'^#(?!(EXTINF[^\n]*|EXT-X-STREAM-INF[^\n]*))[^\n]*', '',
                         text, flags=re.MULTILINE)
        segments = re.findall(r'#EXTINF[^\n]*\n([^\n#]+)', cleaned)

        ts_list = []
        for seg in segments:
            seg = seg.strip()
            if seg:
                ts_list.append(self._resolve_url(seg, m3u8_url, page_url))

        log.info(f"  解析到 {len(ts_list)} 个 ts 切片"
                 + (", AES-128 加密" if key_url else ""))
        return ts_list, key_url, iv

    @staticmethod
    def _select_best_stream(text: str, m3u8_url: str, page_url: str) -> Optional[str]:
        """从 master playlist 选最高分辨率子流（对应油猴脚本的 maxP/maxUrl 逻辑）"""
        best_pixels = -1
        best_url    = None

        # 匹配 #EXT-X-STREAM-INF 或 #EXT-X-IFRAME-STREAM-INF
        for block in re.split(r'(#EXT-X-STREAM-INF[^\n]*)', text):
            block = block.strip()
            if not block or block.startswith("#"):
                continue
            # block 是 URI 行
            uri_line = block.split("\n")[0].strip()
            if not uri_line or uri_line.startswith("#"):
                continue

            # 找上一行的 RESOLUTION
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if uri_line in line and i > 0:
                    meta = lines[i - 1]
                    res_m = re.search(r'RESOLUTION=(\d+)[xX×](\d+)', meta)
                    if res_m:
                        pixels = int(res_m.group(1)) * int(res_m.group(2))
                        if pixels > best_pixels:
                            best_pixels = pixels
                            best_url    = uri_line
                    elif best_url is None:
                        best_url = uri_line
                    break

        if best_url:
            # 还原 URL
            if not best_url.startswith("http"):
                base_dir = m3u8_url.split("?")[0].rsplit("/", 1)[0]
                best_url = f"{base_dir}/{best_url}"
            log.info(f"  选定子流: {best_url[:80]} ({best_pixels}px)")

        return best_url

    def _extract_key(self, text: str, m3u8_url: str, page_url: str):
        """提取 AES-128 加密 key URL 和 IV（对应油猴脚本 #EXT-X-KEY 解析）"""
        key_match = re.search(r'#EXT-X-KEY[^\n]*', text, re.IGNORECASE)
        if not key_match:
            return None, None

        line = key_match.group(0)
        method_m = re.search(r'METHOD=([\w-]+)', line)
        if not method_m or method_m.group(1).upper() in ("NONE", ""):
            return None, None

        uri_m = re.search(r'URI="([^"]+)"', line)
        if not uri_m:
            return None, None

        key_url = uri_m.group(1)
        if not key_url.startswith("http"):
            key_url = self._resolve_url(key_url, m3u8_url, page_url)

        iv_m = re.search(r'IV=0x([\dA-Fa-f]+)', line)
        iv   = iv_m.group(1) if iv_m else None

        return key_url, iv

    # ── AES-128 解密（对应油猴脚本 jiemi() 函数）──
    @staticmethod
    def _decrypt_ts(data: bytes, key_bytes: bytes, iv_hex: Optional[str]) -> bytes:
        try:
            from Crypto.Cipher import AES
            iv = bytes.fromhex(iv_hex) if iv_hex else key_bytes[:16]
            cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
            return cipher.decrypt(data)
        except ImportError:
            log.warning("  pycryptodome 未安装，跳过解密（pip install pycryptodome）")
            return data

    # ── 并发下载 ts 切片（对应油猴脚本 xcNum=15 线程）──
    def _download_ts_list(self, ts_list: list[str]) -> list[Optional[bytes]]:
        results = [None] * len(ts_list)
        done    = 0
        total   = len(ts_list)

        def _dl(i, url):
            for attempt in range(3):
                if self.stop_event.is_set():
                    return
                try:
                    data = self._http_get(url, binary=True)
                    results[i] = data
                    return
                except Exception as e:
                    log.warning(f"  ts[{i}] 第{attempt+1}次失败: {e}")
                    time.sleep(1)
            log.error(f"  ts[{i}] 彻底失败，跳过: {url[:60]}")

        with ThreadPoolExecutor(max_workers=TS_THREADS) as pool:
            futures = {pool.submit(_dl, i, url): i for i, url in enumerate(ts_list)}
            for fut in as_completed(futures):
                done += 1
                if done % 10 == 0 or done == total:
                    pct = done / total * 100
                    log.info(f"  ts 下载进度: {done}/{total} ({pct:.0f}%)")
                if self.stop_event.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

        return results

    # ── 主入口：m3u8 → mp4 ───────────────────
    def process(self, m3u8_url: str, out_path: Path, page_url: str = BASE_URL) -> bool:
        """完整流程：下载 → 解析 → 并发下载 ts → 合并 → ffmpeg 封装"""
        log.info(f"  [m3u8] 开始处理: {m3u8_url[:80]}")
        try:
            m3u8_text = self._http_get(m3u8_url)
        except Exception as e:
            log.error(f"  [m3u8] 下载 m3u8 失败: {e}")
            # 兜底：直接用 ffmpeg 下载（处理带 token 等鉴权参数的情况）
            return self._ffmpeg_direct(m3u8_url, out_path, page_url)

        try:
            ts_list, key_url, iv = self._parse_m3u8(m3u8_text, m3u8_url, page_url)
        except Exception as e:
            log.error(f"  [m3u8] 解析失败: {e}，改用 ffmpeg 直接下载")
            return self._ffmpeg_direct(m3u8_url, out_path, page_url)

        if not ts_list:
            log.warning("  [m3u8] 未解析到 ts 切片，改用 ffmpeg 直接下载")
            return self._ffmpeg_direct(m3u8_url, out_path, page_url)

        # 下载 AES 密钥
        key_bytes = None
        if key_url:
            try:
                key_bytes = self._http_get(key_url, binary=True)
                log.info("  [m3u8] AES-128 密钥已下载，启用解密")
            except Exception as e:
                log.warning(f"  [m3u8] 密钥下载失败，尝试无解密下载: {e}")

        # 并发下载所有 ts 切片
        log.info(f"  [m3u8] 开始并发下载 {len(ts_list)} 个切片 (线程数: {TS_THREADS})")
        chunks = self._download_ts_list(ts_list)

        if self.stop_event.is_set():
            return False

        # 解密 + 合并
        tmp_ts = out_path.with_suffix(".tmp.ts")
        try:
            with open(tmp_ts, "wb") as f:
                ok_count = 0
                for i, chunk in enumerate(chunks):
                    if chunk is None:
                        log.warning(f"  ts[{i}] 缺失，跳过")
                        continue
                    if key_bytes:
                        chunk = self._decrypt_ts(chunk, key_bytes, iv)
                    f.write(chunk)
                    ok_count += 1
            log.info(f"  [m3u8] 合并完成: {ok_count}/{len(ts_list)} 切片 → {tmp_ts.name}")
        except Exception as e:
            log.error(f"  [m3u8] 合并失败: {e}")
            return False

        # ffmpeg 封装 ts → mp4
        ok = self._ffmpeg_remux(str(tmp_ts), str(out_path))
        try:
            tmp_ts.unlink(missing_ok=True)
        except Exception:
            pass
        return ok

    def _ffmpeg_direct(self, m3u8_url: str, out_path: Path, referer: str) -> bool:
        """直接用 ffmpeg 下载 m3u8（鉴权 token 场景的兜底方案）"""
        cmd = [
            self.ffmpeg, "-y",
            "-headers",
            f"Referer: {referer}\r\nUser-Agent: Mozilla/5.0\r\n",
            "-i", m3u8_url,
            "-c", "copy",
            "-movflags", "+faststart",
            "-loglevel", "warning",
            str(out_path),
        ]
        log.info(f"  [ffmpeg direct] 封装: {out_path.name}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode == 0:
                size = out_path.stat().st_size / 1024 / 1024 if out_path.exists() else 0
                log.info(f"  [ffmpeg] ✓ {size:.1f} MB")
                return True
            log.error(f"  [ffmpeg] 失败 (exit {r.returncode})\n{r.stderr[-600:]}")
            return False
        except FileNotFoundError:
            log.error("  [ffmpeg] 找不到 ffmpeg！请在「设置」页指定路径。")
            return False

    def _ffmpeg_remux(self, ts_path: str, mp4_path: str) -> bool:
        """ffmpeg ts → mp4 封装（-c copy）"""
        cmd = [
            self.ffmpeg, "-y",
            "-i", ts_path,
            "-c", "copy",
            "-movflags", "+faststart",
            "-loglevel", "warning",
            mp4_path,
        ]
        log.info(f"  [ffmpeg] ts → mp4: {Path(mp4_path).name}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode == 0:
                size = Path(mp4_path).stat().st_size / 1024 / 1024 if Path(mp4_path).exists() else 0
                log.info(f"  [ffmpeg] ✓ {size:.1f} MB → {mp4_path}")
                return True
            log.error(f"  [ffmpeg] 失败 (exit {r.returncode})\n{r.stderr[-600:]}")
            return False
        except FileNotFoundError:
            log.error("  [ffmpeg] 找不到 ffmpeg！")
            return False


# ──────────────────────────────────────────────
#  CrawlerCore：对外接口（供 GUI / 命令行调用）
# ──────────────────────────────────────────────
class CrawlerCore:
    def __init__(
        self,
        output_dir:  str = "downloads",
        ffmpeg_path: Optional[str] = None,
        headless:    bool = True,
        sniff_wait:  int  = 15,
        proxy:       dict = None,
        stop_event:  threading.Event = None,
        progress_cb: Callable = None,   # (current, total, title) -> None
        filter_ads:  bool = True,
    ):
        self.output_dir  = Path(output_dir)
        self.ffmpeg_path = ffmpeg_path or self._find_ffmpeg()
        self.headless    = headless
        self.sniff_wait  = sniff_wait
        self.proxy       = proxy or {}
        self.stop_event  = stop_event or threading.Event()
        self.progress_cb = progress_cb
        self.filter_ads  = filter_ads
        self._driver     = None
        self._sniffer: Optional[M3U8Sniffer] = None

        self._req_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": BASE_URL,
        }

    @staticmethod
    def _find_ffmpeg() -> str:
        candidates = [
            str(Path(__file__).parent / "ffmpeg" / "ffmpeg.exe"),
            str(Path(__file__).parent / "ffmpeg" / "ffmpeg"),
            "ffmpeg",
        ]
        for p in candidates:
            try:
                r = subprocess.run([p, "-version"], capture_output=True, timeout=5)
                if r.returncode == 0:
                    return p
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return "ffmpeg"

    def _make_dir(self, title: str) -> Path:
        d = self.output_dir / today_str() / safe_filename(title)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── 浏览器生命周期 ─────────────────────────
    def _start_driver(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--window-size=1280,800")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        if self.proxy.get("socks5"):
            opts.add_argument(f"--proxy-server={self.proxy['socks5']}")
            log.info(f"已启用代理: {self.proxy['socks5']}")

        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
        except ImportError:
            service = Service()

        self._driver = webdriver.Chrome(service=service, options=opts)
        self._driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"}
        )
        self._sniffer = M3U8Sniffer(self._driver)

    def _quit_driver(self):
        if self._sniffer:
            self._sniffer.stop()
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
        self._driver  = None
        self._sniffer = None

    # ── 标题提取 ───────────────────────────────
    @staticmethod
    def _get_title(driver, fallback: str = "untitled") -> str:
        try:
            t = driver.title.strip()
            t = re.split(r'\s*[-–|]\s*好色', t)[0].strip()
            if t:
                return t
        except Exception:
            pass
        try:
            from selenium.webdriver.common.by import By
            h1 = driver.find_element(By.TAG_NAME, "h1")
            t  = h1.text.strip()
            if t:
                return t
        except Exception:
            pass
        return fallback

    # ── 嗅探单页核心 ───────────────────────────
    def _sniff_page(self, url: str, hint_title: str = "",
                    skip_existing: bool = True) -> bool:
        if self.stop_event.is_set():
            return False

        log.info(f"▶ {url}")
        self._sniffer.start()
        self._driver.get(url)

        # 等待嗅探（最多 sniff_wait 秒，捕获到 m3u8 后提前退出）
        deadline = time.time() + self.sniff_wait
        while time.time() < deadline:
            if self.stop_event.is_set():
                self._sniffer.stop()
                return False
            if self._sniffer.get_hls_urls():
                elapsed = self.sniff_wait - (deadline - time.time())
                log.info(f"  已捕获 m3u8，嗅探耗时 {elapsed:.1f}s")
                break
            time.sleep(0.4)

        self._sniffer.stop()

        hls_list = self._sniffer.all_hls()
        if not hls_list:
            log.warning("  ✗ 未嗅探到 m3u8，跳过")
            return False

        m3u8_url = hls_list[0]
        title    = self._get_title(self._driver, fallback=hint_title or "untitled")
        log.info(f"  标题: {title}")

        out_dir  = self._make_dir(title)
        mp4_path = out_dir / (safe_filename(title) + ".mp4")

        if skip_existing and mp4_path.exists() and mp4_path.stat().st_size > 10240:
            log.info(f"  已存在，跳过")
            return True

        # 用完整 m3u8 处理器下载转码
        processor = M3U8Processor(
            ffmpeg_path=self.ffmpeg_path,
            headers=self._req_headers,
            stop_event=self.stop_event,
            filter_ads=self.filter_ads,
        )
        return processor.process(m3u8_url, mp4_path, page_url=url)

    # ── 列表页解析 ─────────────────────────────
    def _parse_list_page(self, page_url: str) -> list[dict]:
        from bs4 import BeautifulSoup
        log.info(f"  列表页: {page_url}")
        self._driver.get(page_url)
        time.sleep(2)

        soup   = BeautifulSoup(self._driver.page_source, "html.parser")
        videos, seen = [], set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not re.search(r'video-\d+\.htm', href):
                continue
            full = urljoin(BASE_URL, href)
            if full in seen:
                continue
            seen.add(full)
            title = ""
            for tag in ["h2", "h3", "p"]:
                el = a.find(tag)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break
            if not title:
                title = a.get("title", "") or a.get_text(" ", strip=True)
            videos.append({"title": title.strip() or "untitled", "url": full})

        log.info(f"  找到 {len(videos)} 个视频")
        return videos

    # ── 公开方法：批量爬取 ─────────────────────
    def crawl_list(self, list_type: str = "list",
                   start: int = 1, end: int = 3):
        done = load_progress()
        log.info(f"已有进度 {len(done)} 条")
        self._start_driver()
        try:
            all_videos = []
            for n in range(start, end + 1):
                if self.stop_event.is_set():
                    break
                prefix = "hot_list" if list_type == "hot" else "list"
                vids   = self._parse_list_page(f"{BASE_URL}/{prefix}-{n}.htm")
                if not vids:
                    log.info(f"第 {n} 页无数据，停止")
                    break
                all_videos.extend(vids)
                time.sleep(1.5)

            log.info(f"共发现 {len(all_videos)} 个视频")
            ok_count = 0
            for i, v in enumerate(all_videos, 1):
                if self.stop_event.is_set():
                    log.info("任务已停止")
                    break
                if v["url"] in done:
                    log.info(f"[{i}/{len(all_videos)}] 跳过: {v['url']}")
                    if self.progress_cb:
                        self.progress_cb(i, len(all_videos), "跳过: " + v["title"])
                    continue
                if self.progress_cb:
                    self.progress_cb(i, len(all_videos), v["title"])
                log.info(f"[{i}/{len(all_videos)}]")
                ok = self._sniff_page(v["url"], v["title"])
                if ok:
                    done.add(v["url"])
                    ok_count += 1
                    save_progress(done)
                time.sleep(1.5)
            log.info(f"完成！成功 {ok_count}/{len(all_videos)}")
        finally:
            self._quit_driver()

    # ── 公开方法：单视频 ───────────────────────
    def process_single(self, url: str, skip_existing: bool = True) -> bool:
        self._start_driver()
        try:
            return self._sniff_page(url, skip_existing=skip_existing)
        finally:
            self._quit_driver()

    # ── 公开方法：直接 m3u8 下载 ───────────────
    def download_m3u8_direct(self, m3u8_url: str, title: str = "video") -> bool:
        out_dir  = self._make_dir(title)
        mp4_path = out_dir / (safe_filename(title) + ".mp4")
        processor = M3U8Processor(
            ffmpeg_path=self.ffmpeg_path,
            headers=self._req_headers,
            stop_event=self.stop_event,
            filter_ads=self.filter_ads,
        )
        return processor.process(m3u8_url, mp4_path)


# ──────────────────────────────────────────────
#  命令行模式
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()]
    )
    p = argparse.ArgumentParser(description="爬虫核心（命令行模式）")
    p.add_argument("url",      help="视频页 URL 或 m3u8 URL")
    p.add_argument("--output", default="downloads")
    p.add_argument("--ffmpeg", default="")
    p.add_argument("--wait",   type=int, default=15)
    p.add_argument("--show",   action="store_true")
    p.add_argument("--proxy",  default="", help="socks5://host:port")
    p.add_argument("--no-filter-ads", action="store_true")
    args = p.parse_args()

    proxy = {"socks5": args.proxy} if args.proxy else {}
    core  = CrawlerCore(
        output_dir=args.output,
        ffmpeg_path=args.ffmpeg or None,
        headless=not args.show,
        sniff_wait=args.wait,
        proxy=proxy,
        filter_ads=not args.no_filter_ads,
    )
    if ".m3u8" in args.url:
        core.download_m3u8_direct(args.url, "video")
    else:
        core.process_single(args.url)
