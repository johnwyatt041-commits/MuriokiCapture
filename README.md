# 🖼️ MuriokiCapture

高效截图工具，集成 OCR 文字识别、多语言翻译与 AI 智能翻译功能。

## ✨ 功能特性

- 🖥️ **屏幕截图** — 区域截图、全屏截图，支持高分屏
- 📌 **贴图** — 截图结果贴在屏幕上，方便参考
- 🎥 **屏幕录制** — 区域录屏，多画质档位
- 📜 **滚动截图** — 自动拼接长页面
- ✏️ **图片编辑** — 矩形、箭头、文字标注、马赛克、橡皮擦
- 🔤 **OCR 文字识别** — RapidOCR + Tesseract 双引擎融合，支持中/英/泰/菲多语言
- 🤖 **AI 文字识别** — 基于视觉语言模型 (VL) 的智能 OCR
- 🌐 **在线翻译** — Google Translate + MyMemory 双通道翻译
- 🤖 **AI 翻译** — 基于 OpenRouter AI 模型的高质量翻译
- ⌨️ **全局快捷键** — F1 截图 / F2 文字识别 / F3 贴图 / F4 录屏

## 🚀 快速开始

### 直接使用
从 [Releases](../../releases) 页面下载最新的 `MuriokiCapture.exe`，双击运行即可。

### 从源码运行
```bash
# 安装依赖
pip install PyQt5 mss opencv-python-headless pillow numpy keyboard psutil pytesseract rapidocr-onnxruntime

# 运行
python capture.py
```

### 打包 exe
```bash
pyinstaller MuriokiCapture.spec
```

## ⌨️ 快捷键

| 快捷键 | 功能 |
|--------|------|
| F1 | 截图 |
| F2 | OCR 文字识别与翻译 |
| F3 | 贴图 |
| F4 | 屏幕录制 |

## 🤖 AI 功能配置

应用内置 AI 翻译和 AI 文字识别功能，通过 [OpenRouter](https://openrouter.ai/) 调用视觉语言模型。在 OCR 弹窗中可以切换：

- **识别模式**: `📷 OCR 识别` / `🤖 AI 识别`
- **翻译模式**: `🌐 普通翻译` / `🤖 AI 翻译`

设置会自动保存，下次使用时自动恢复。

## 📄 License

MIT License
