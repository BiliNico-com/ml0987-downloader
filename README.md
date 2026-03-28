# ml0987 视频下载器 - GUI 版本

## 功能特点

- **自动 ffmpeg 检查**：程序启动时自动检查 ffmpeg.exe 是否存在，缺失时提示用户前往官网下载
- **一键依赖安装**：集成 Python 依赖安装功能，支持一键安装所有必要依赖
- **批量爬取功能**：支持批量爬取视频，可设置页码范围和列表类型
- **单个视频下载**：支持单个视频 URL 下载，可自定义标题
- **实时进度显示**：提供实时进度显示和日志输出
- **代理支持**：支持 SOCKS5 代理配置
- **GitHub Actions 构建**：通过 GitHub Actions 自动编译生成 .exe 文件

## 环境要求

- Python 3.8+
- tkinter（GUI 界面）
- ffmpeg.exe（必需，用于视频处理）

## 快速开始

### 方式一：直接运行（需要 Python 环境）

1. 安装依赖
   ```bash
   pip install -r requirements.txt
   ```

2. 下载 ffmpeg
   - 访问 [ffmpeg 官网](https://ffmpeg.org/download.html) 下载 Windows 版本
   - 解压后，将 `ffmpeg.exe` 复制到程序根目录

3. 启动 GUI
   ```bash
   python app.py
   ```

### 方式二：下载编译好的 .exe（推荐）

1. 访问 [GitHub Releases](https://github.com/yourusername/your-repo/releases) 页面
2. 下载最新的 .exe 安装包
3. 双击运行即可使用（无需安装 Python）

## 界面功能说明

### ✅ 环境检测

- 自动检测 ffmpeg.exe 是否存在
- 缺失时显示提示框，询问是否跳转到官网下载
- 一键安装 Python 依赖

### 📋 批量爬取

- 选择爬取类型：最新 / 最热
- 设置起止页码
- 显示实时进度

### 🔗 单视频

- 支持单个视频 URL 下载
- 可自定义视频标题

### ⚙️ 设置

- 配置保存目录
- 选择浏览器
- 配置 SOCKS5 代理

### 📄 日志

- 实时显示运行日志
- 支持清空日志

## GitHub Actions 构建

项目已配置 GitHub Actions，可通过以下步骤自动构建 .exe 文件：

1. 将代码推送到 GitHub 仓库
2. GitHub Actions 会自动触发构建流程
3. 构建完成后，.exe 文件将上传到 Releases 页面

## 常见问题

**Q: 如何下载 ffmpeg？**
- 访问 [ffmpeg 官网](https://ffmpeg.org/download.html) 下载 Windows 版本
- 解压后，将 `ffmpeg.exe` 复制到程序根目录

**Q: 程序启动时提示缺少 ffmpeg.exe 怎么办？**
- 点击提示框中的 "是" 按钮，会自动打开 ffmpeg 官网
- 下载完成后，将 `ffmpeg.exe` 放置在程序根目录

**Q: 如何构建 .exe 文件？**
- 将代码推送到 GitHub 仓库
- GitHub Actions 会自动构建
- 构建完成后，可在 Releases 页面下载

**Q: 代理设置不生效怎么办？**
- 确认本地代理软件已启动
- 端口设置正确（Clash 默认 7891，v2ray 默认 1080）
- 注意：部分代理软件默认只提供 HTTP 代理，需在软件内开启 SOCKS5

**Q: 打包后的 .exe 文件被杀毒软件误报怎么办？**
- 这是 PyInstaller 打包程序的常见问题
- 可以将 .exe 文件添加到杀毒软件白名单
- 或在虚拟机中运行

## 文件说明

```
crawler/
├── app.py            ← GUI 主程序（直接运行此文件）
├── crawler_core.py   ← 爬虫核心（CDP 嗅探 + ffmpeg 转码）
├── requirements.txt  ← Python 依赖
├── build.bat         ← 一键打包为 .exe（Windows）
└── README.md         ← 本文件
```

## 许可证

MIT License
