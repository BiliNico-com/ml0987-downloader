# hsex 视频下载器

纯 Python + tkinter GUI，无需浏览器驱动，批量下载视频。AI写的。

## 功能

- **批量爬取** — 支持视频、周榜、月榜、5分钟+、10分钟+ 列表，自定义页码范围
- **搜索下载** — 输入关键词搜索，支持最新/最热排序，搜索结果直接批量下载
- **搜索作者** — 按关键词搜索作者，勾选后批量下载作者全部视频
- **作者页数显示** — 搜索作者后自动检测总页数，方便设置下载范围
- **作者归档目录** — 下载作者视频时自动按作者创建子目录，方便分类管理
- **单视频下载** — 输入 URL 直接下载
- **标题含作者** — 可选在文件名中附加上传者名称
- **封面预览** — 批量爬取时实时显示当前视频封面和标题
- **切片进度** — 实时显示当前视频的 TS 切片下载进度
- **按上传日期分类** — 自动识别视频上传日期，按日期归类存储（可关闭）
- **防重复下载** — 已下载视频自动跳过，跨次运行共享下载记录
- **SOCKS5 代理** — 内置 SOCKS5 代理支持，无需额外安装 PySocks，可测试代理连接
- **并发下载** — 多线程并发下载 TS 切片，速度快
- **AES 解密** — 自动处理加密的 m3u8 流
- **随时停止** — 下载过程中可随时停止，不卡顿

## 快速开始

### 方式一：Python 环境运行

```bash
pip install -r requirements.txt
python app.py
```

### 方式二：下载 exe 直接运行（无需 Python）

从 GitHub Releases 下载最新版，解压后双击运行。

## 依赖

| 依赖 | 用途 | 必须 |
|---|---|---|
| `requests` | HTTP 请求 | ✅ |
| `pycryptodome` | AES-128 解密（加密视频） | 可选 |
| `Pillow` | 封面图预览（webp 格式） | 可选 |
| `ffmpeg.exe` | TS 切片合并为 MP4 | ✅ |

> `pycryptodome` 和 `Pillow` 缺失时程序仍可运行，仅对应功能不可用。

> ffmpeg.exe 不在 pip 里，需要手动下载放到程序同目录。推荐从 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 下载 essentials 版。exe 打包版已内置。

## 输出结构

按视频上传日期自动分类，作者下载时自动归档到作者子目录：

```
downloads/
├── 2026-03-28/
│   └── 视频标题A.mp4
├── 2026-03-29/
│   ├── XXX/              ← 作者下载自动创建子目录
│   │   ├── 视频标题B.mp4
│   │   └── 视频标题C.mp4
│   └── 视频标题D.mp4
└── download_history.json        ← 已下载记录（防重复）
```

## 文件说明

```
├── app.py              # GUI 主程序
├── crawler_core.py     # 爬虫核心（requests + m3u8 + ffmpeg）
├── socks.py            # PySocks 本地模块（SOCKS5 代理支持，BSD 协议）
├── requirements.txt    # Python 依赖
└── .gitignore          # Git 忽略规则
```

> `socks.py` 来源于 [PySocks](https://github.com/Anorov/PySocks)（BSD 协议），作为本地模块集成，无需 pip 安装。

## 构建

使用 PyInstaller 打包为 Windows exe：

```bash
pip install -r requirements.txt
pyinstaller --noconfirm --onedir --windowed --contents-directory Core ^
    --name "ml0987下载器" ^
    --add-data "crawler_core.py;." ^
    --add-data "socks.py;." ^
    --hidden-import PIL --hidden-import PIL.ImageTk --hidden-import PIL.WebPImagePlugin ^
    --hidden-import Crypto.Cipher --collect-all Pillow --collect-all pycryptodome ^
    app.py
```

打包后将 `ffmpeg.exe` 拷贝到 `dist/ml0987下载器/` 目录即可分发。

## 许可证

MIT License
