# Windows 移植計畫

## 現狀分析

當前專案在 macOS/Linux 開發，核心程式碼已具備跨平台基礎：

| 元件 | Windows 相容性 | 狀態 |
|------|---------------|------|
| `pathlib.Path` 路徑操作 | ✅ 原生跨平台 | OK |
| `_WIN` 偵測 (`jury.py`) | ✅ 已有 `sys.platform == "win32"` 分支 | OK |
| `Scripts/python.exe` vs `bin/python` | ✅ 已處理 | OK |
| `sys.stdout.reconfigure(encoding="utf-8")` | ✅ 已處理 | OK |
| 中文檔名（已改英文） | ✅ jury.py / emotion_arc.py | OK |
| `subprocess` venv 隔離 | ⚠️ 需確認 Windows venv 啟用 | 見下文 |

---

## 異動清單

### 1. 新增 `WINDOWS.md`（本文件）

### 2. 新增安裝腳本

#### `setup_windows.bat`

一鍵建立雙 venv + 安裝依賴。使用者以「系統管理員」執行。

```bat
@echo off
chcp 65001 >nul
title Song Jury — Windows Setup
echo 🎼 歌曲評審團 Windows 安裝程式
echo ====================================

:: 檢查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 找不到 Python。請先安裝 Python 3.10+
    pause
    exit /b 1
)

:: 建立 .venv（song_scorer 用）
echo [1/4] 建立 .venv（物理量測環境）...
if not exist ".venv" (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install --upgrade pip -q
pip install librosa numpy scipy soundfile pyloudnorm praat-parselmouth matplotlib -q
deactivate

:: 建立 .venv-ml（ML 模型用）
echo [2/4] 建立 .venv-ml（美學模型環境）...
if not exist ".venv-ml" (
    python -m venv .venv-ml
)
call .venv-ml\Scripts\activate.bat
pip install --upgrade pip -q
pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu -q
pip install muq==0.1.0 audiobox-aesthetics==0.0.4 transformers einops hydra-core omegaconf -q
deactivate

:: 安裝共同依賴
echo [3/4] 安裝共同依賴...
pip install yt-dlp requests huggingface_hub gradio -q

:: 確認 ffmpeg
echo [4/4] 檢查 ffmpeg...
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️ 找不到 ffmpeg。請手動安裝：
    echo   1. 下載 https://ffmpeg.org/download.html
    echo   2. 解壓縮後將 bin/ 加入 PATH
    echo   3. 或使用 winget install ffmpeg
) else (
    echo ✅ ffmpeg 已就緒
)

echo.
echo ✅ 安裝完成！執行 python app.py 啟動。
pause
```

#### `run.bat`（啟動腳本）

```bat
@echo off
chcp 65001 >nul
title Song Jury

:: 檢查 .venv 是否存在
if not exist ".venv" (
    echo ❌ 請先執行 setup_windows.bat
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python app.py
pause
```

### 3. 修改 `app.py` — 子程序執行路徑

當前 `app.py` 呼叫子程序時使用：

```python
r = _run([sys.executable, str(BASE / "jury.py"), str(src)])
```

在 Windows 上 `sys.executable` 指向 `.venv\Scripts\python.exe`（如果從 venv 啟動），這是正確的。但如果從系統 Python 啟動，而 `jury.py` 需要 `.venv` 的套件，就會找不到。

**解決方案**：在 `app.py` 的 `_run()` 中，當 `sys.platform == "win32"` 時，自動改用 `.venv\Scripts\python.exe`：

```python
def _run(cmd):
    if sys.platform == "win32":
        # Windows 下確保使用 .venv 的 python 執行子程序
        venv_py = str(BASE / ".venv" / "Scripts" / "python.exe")
        if Path(venv_py).exists():
            cmd = [venv_py] + cmd[1:]
    return subprocess.run(cmd, cwd=str(BASE), env=ENV, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
```

但 `jury.py` 內部有自己的 `_venv_py()` 邏輯會偵測 `.venv` 和 `.venv-ml`。關鍵問題是：`app.py` 呼叫 `jury.py` 時，`sys.executable` 不能是系統 Python（會缺少依賴）。

**改法**：在 `app.py` 的 `evaluate()` 中，直接指定 `jury.py` 用 `.venv\Scripts\python.exe` 執行。

### 4. 修改 `jury.py` — venv 偵測邏輯

`jury.py` 中的 `_venv_py()` 已有 Windows 路徑：

```python
def _venv_py(venv):
    p = BASE / venv / ("Scripts/python.exe" if _WIN else "bin/python")
    return str(p) if p.exists() else sys.executable
```

✅ 已相容，不需修改。

### 5. 修改 `song_scorer.py` — 字型路徑

在 Windows 上，matplotlib 需要中文字型。`emotion_arc.py` 已有 `C:/Windows/Fonts/msjh.ttc` 備援路徑。✅

但 `song_scorer.py` 不使用 matplotlib（只在 `emotion_arc.py` 中用）。✅

### 6. 編碼處理

Windows 主控台預設編碼 cp950（正體中文 Big5），無法正確顯示 UTF-8 輸出。

所有 `.py` 檔案已有 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`。
`setup_windows.bat` 和 `run.bat` 使用 `chcp 65001` 將主控台切換至 UTF-8。

---

## 安裝步驟（使用者視角）

```
1. 安裝 Python 3.10+
   - https://www.python.org/downloads/
   - 安裝時勾選「Add Python to PATH」

2. 安裝 Git
   - https://git-scm.com/download/win
   - 或 winget install git

3. 安裝 ffmpeg
   - winget install ffmpeg
   - 或下載 https://ffmpeg.org/download.html → 解壓 → 加入 PATH

4. 克隆專案
   git clone https://github.com/lunkerchen/song-jury.git
   cd song-jury

5. 執行安裝腳本
   setup_windows.bat
   （約 5–10 分鐘，自動建立雙 venv + 安裝所有依賴）

6. 啟動
   run.bat
   → 瀏覽器打開 http://localhost:7860
```

---

## 已知風險與解決方案

| 風險 | 影響 | 解法 |
|------|------|------|
| Windows 長路徑限制（260 chars） | pip install 在深層 venv 可能失敗 | 註冊表啟用長路徑或將專案放在 `C:\song-jury\` 等短路徑 |
| cp950 編碼無法顯示 UTF-8 | 主控台中文亂碼 | `chcp 65001` + `sys.stdout.reconfigure(encoding="utf-8")` |
| praat-parselmouth 在 Windows 上編譯失敗 | 嗓音品質分析不可用 | 已有 `try/except ImportError` → 自動降級，不影響其他關卡 |
| torch CPU-only 比 GPU 慢 5–10x | 音訊處理時間較長 | 預設安裝 CPU 版；有 NVIDIA 顯卡者可改裝 CUDA 版 |
| yt-dlp 找不到 ffmpeg | YouTube 下載失敗 | 安裝 ffmpeg 並確認在 PATH 中 |
| Gradio 在 Windows 上端口被佔用 | 啟動失敗 | `app.py` 改端口：`demo.launch(server_port=7861)` |

---

## 測試清單

部署後驗證：

- [ ] `python song_scorer.py 測試音檔.mp3 --json test.json` → 產出 JSON
- [ ] `python app.py` → Gradio 介面可訪問
- [ ] 貼 SUNO 連結 → 下載 + 三關評分正常
- [ ] 上傳 mp3 檔 → 評分正常
- [ ] 詞評有 LLM 金鑰時正常產出
- [ ] 無 LLM 金鑰時退回 prompt
- [ ] 主控台輸出無亂碼（UTF-8）

---

## 不納入 Windows 版的功能

- **HF Space 部署**：Windows 不用來部署 Space（那是 HF 伺服器端的事）
- **demucs 人聲分離**：Windows 上安裝 demucs 複雜度高，需要額外的 PyTorch 相依。標示為選配，使用者可自行安裝
- **排行榜與快取**：依賴 HF token 與 HF dataset API，Windows 本機執行時不影響評分，只是無法寫入排行榜
