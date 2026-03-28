# ml0987.xyz 视频下载器 - GUI 版

## 文件说明

```
crawler/
├── app.py            ← GUI 主程序（直接运行此文件）
├── crawler_core.py   ← 爬虫核心（CDP嗅探 + ffmpeg转码）
├── requirements.txt  ← Python 依赖
├── build.bat         ← 一键打包为 .exe（Windows）
└── README.md         ← 本文件
```

---

## 🚀 快速开始（两种方式）

### 方式一：直接运行（需要 Python 环境）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动 GUI
python app.py
```

### 方式二：打包为 .exe（用户无需安装 Python）

```bash
# 双击运行（Windows）
build.bat

# 完成后进入 dist/ml0987下载器/ 目录
# 双击 ml0987下载器.exe 即可
```

---

## 界面功能说明

### ✅ 环境检测
- 自动检测所有依赖是否就绪
- **一键安装 Python 依赖**（selenium / webdriver-manager / beautifulsoup4 等）
- **下载 ffmpeg** 引导按钮，自动打开下载页

### 📋 批量爬取
- 选择爬取类型：最新 / 最热
- 设置起止页码、嗅探等待时间
- 显示实时进度

### 🔗 单视频
- 粘贴一个或多个视频页 URL（每行一个）
- 支持直接粘贴 m3u8 URL 转码

### ⚙️ 设置
| 功能 | 说明 |
|------|------|
| 📁 保存目录 | 自定义视频保存位置，按「转码日期/视频标题」自动分类 |
| 🔧 ffmpeg 路径 | 指定 ffmpeg.exe 位置，留空自动探测 |
| 🌐 SOCKS5 代理 | 网站无法直接访问时启用，支持 v2ray/Clash/SS 本地代理 |

### 📄 日志
- 实时滚动日志
- 颜色区分：成功（绿）/ 警告（黄）/ 错误（红）
- 支持复制全部日志

---

## SOCKS5 代理设置

进入「设置」页，启用代理并填写：

| 参数 | 示例 |
|------|------|
| 主机 | `127.0.0.1` |
| 端口 | `1080`（v2ray/Clash 默认）|
| 用户名/密码 | 无需认证则留空 |

> 本工具通过 `--proxy-server=socks5://...` 参数将代理注入 Chrome，  
> 因此浏览器所有流量（包括视频请求）都会走代理。

---

## 输出目录结构

```
downloads/
└── 2026-03-29/                     ← 转码日期
    ├── 视频标题A/
    │   └── 视频标题A.mp4
    └── 视频标题B/
        └── 视频标题B.mp4
```

---

## ffmpeg 安装（Windows 便携版）

1. 下载：https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
2. 解压后，将 `bin/ffmpeg.exe` 复制到程序目录下的 `ffmpeg/` 文件夹：
   ```
   ml0987下载器/
   └── ffmpeg/
       └── ffmpeg.exe    ← 放这里
   ```
3. 程序启动时会自动检测此路径

---

## 常见问题

**Q: 嗅探不到 m3u8？**  
→ 在「批量爬取」或「单视频」页将「嗅探等待」时间调大（如 30 秒），  
  或取消勾选「无头模式」观察浏览器实际打开情况。

**Q: 提示 Chrome not found？**  
→ 安装 Google Chrome：https://www.google.cn/intl/zh-CN/chrome/

**Q: 代理不生效？**  
→ 确认本地代理软件已启动，端口匹配（Clash 默认 7891，v2ray 默认 1080）。  
  注意：部分代理软件默认只提供 HTTP 代理，需在软件内开启 SOCKS5。

**Q: 打包后 .exe 杀毒软件报警？**  
→ PyInstaller 打包的程序常被误报，可添加白名单或在虚拟机中运行。
