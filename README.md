# ml0987 视频下载器

纯 Python + tkinter GUI，无需浏览器驱动，批量下载 ml0987.xyz 视频。

## 功能

- **批量爬取** — 支持视频、周榜、月榜、5分钟+、10分钟+ 列表，自定义页码范围
- **单视频下载** — 输入 URL 直接下载
- **封面预览** — 批量爬取时实时显示当前视频封面和标题
- **并发下载** — 多线程并发下载 TS 切片，速度快
- **AES 解密** — 自动处理加密的 m3u8 流
- **SOCKS5 代理** — 可选代理配置
- **自动命名** — 按视频标题自动创建文件夹和命名文件

## 运行

### 需要 Python 环境

```bash
pip install -r requirements.txt
python app.py
```

### 下载 exe 直接运行（无需 Python）

从 GitHub Releases 下载最新版，解压后双击运行。

## 依赖

| 依赖 | 用途 |
|---|---|
| `requests` | HTTP 请求 |
| `pycryptodome` | AES-128 解密（加密视频） |
| `Pillow` | 封面图预览（webp 格式） |
| `ffmpeg.exe` | TS 转 MP4（需放在程序同目录） |

> ffmpeg.exe 不在 pip 里，需要手动下载放到程序目录。推荐从 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 下载 essentials 版。

## 输出结构

```
downloads/
└── 2026-03-29/
    └── 视频标题/
        └── 视频标题.mp4
```

## 构建

使用 PyInstaller 打包为 Windows exe：

```bash
pip install -r requirements.txt
pyinstaller --noconfirm --onedir --windowed --name "ml0987下载器" ^
    --add-data "crawler_core.py;." ^
    --hidden-import PIL --hidden-import PIL.ImageTk --hidden-import PIL.WebPImagePlugin ^
    --hidden-import Crypto.Cipher --collect-all Pillow --collect-all pycryptodome ^
    app.py
```

打包后将 `ffmpeg.exe` 拷贝到 `dist/ml0987下载器/` 目录即可分发。

## 文件说明

```
├── app.py              # GUI 主程序
├── crawler_core.py     # 爬虫核心（requests + m3u8 + ffmpeg）
└── requirements.txt    # Python 依赖
```

## 许可证

MIT License
