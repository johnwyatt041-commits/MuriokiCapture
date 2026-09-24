# 🖼️ Capture

高效截图工具，集成 OCR 文字识别、多语言翻译与 AI 智能翻译功能。

## ✨ 功能特性

- 🖥️ **屏幕截图** — 区域截图、全屏截图，支持高分屏与窗口/控件智能高亮吸附（Snipaste 级体验）
- 📌 **贴图** — 截图结果贴在屏幕上，支持缩放、拖拽与置顶参考
- 🎥 **屏幕录制** — 区域录屏与全屏录制，支持音频录制、摄像头画中画与 MP4/GIF 高清导出
- 📜 **滚动长截图** — 自动滚动并拼接长页面
- ✏️ **全功能标注** — 矩形、椭圆/正圆、直线/箭头、自由涂鸦、荧光笔高亮、序号步骤标注（①②③）、文本输入、马赛克模糊、橡皮擦（支持右键/ESC快速取消退出）
- 🗃️ **截图历史记录** — 独立历史管理面板，支持查看、搜索、重新编辑、复制与保存
- ⚙️ **偏好设置面板** — 自定义快捷键绑定、默认存储路径、多格式保存（PNG / JPG / WebP）与质量调节
- 🔤 **OCR 文字识别** — RapidOCR + Tesseract 双引擎融合，支持中/英/泰/菲多语言离线识别
- 🤖 **AI 视觉与翻译** — OpenRouter 视觉语言模型，多模型自动故障转移（429 降级兜底）
- 🌐 **在线翻译** — Google Translate + MyMemory 双通道翻译
- ⌨️ **全局快捷键** — 默认 F1 截图 / F2 文字识别 / F3 贴图 / F4 录屏（可在设置中自定义）

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

### 模型自定义与高可用自动降级
在 `murioki_settings.json` 中可自定义首选模型：
```json
{
  "openrouter_api_key": "你的 OpenRouter 密钥",
  "openrouter_model": "qwen/qwen3.8-27b:free"
}
```
> 💡 当首选模型由于公共调用量大触发 **HTTP 429** 限流或临时故障时，程序会自动依次降级回退至 `inclusionai/ling-3.0-flash-vl:free`、`nex-agi/nex-n2.5-mini:free` 等备用免费多模态视觉模型，确保翻译与文字识别稳定可用。

## 📄 License

MIT License
