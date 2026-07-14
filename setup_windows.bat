@echo off
chcp 65001 >nul
title Song Jury — Windows Setup
echo 🎼 歌曲評審團 Windows 安裝程式
echo ====================================

:: 檢查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 找不到 Python。
    echo 請先安裝 Python 3.10+：https://www.python.org/downloads/
    echo 安裝時請勾選「Add Python to PATH」
    pause
    exit /b 1
)

echo ✅ Python 版本：
python --version

:: 建立 .venv（song_scorer 用）
echo.
echo [1/4] 建立 .venv（物理量測環境）...
if exist ".venv" (
    echo ⏭️  .venv 已存在，跳過
) else (
    python -m venv .venv
    echo ✅ .venv 建立完成
)

echo 安裝物理量測套件...
call .venv\Scripts\activate.bat
pip install --upgrade pip -q
pip install librosa numpy scipy soundfile pyloudnorm praat-parselmouth matplotlib -q
deactivate

:: 建立 .venv-ml（ML 模型用）
echo.
echo [2/4] 建立 .venv-ml（美學模型環境）...
if exist ".venv-ml" (
    echo ⏭️  .venv-ml 已存在，跳過
) else (
    python -m venv .venv-ml
    echo ✅ .venv-ml 建立完成
)

echo 安裝美學模型套件（CPU 版，約 3–5 分鐘）...
call .venv-ml\Scripts\activate.bat
pip install --upgrade pip -q
pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu -q
pip install muq==0.1.0 audiobox-aesthetics==0.0.4 transformers einops hydra-core omegaconf -q
deactivate

:: 安裝共同依賴
echo.
echo [3/4] 安裝共同依賴...
pip install yt-dlp requests huggingface_hub gradio -q

:: 確認 ffmpeg
echo.
echo [4/4] 檢查 ffmpeg...
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️  找不到 ffmpeg。
    echo 部分功能（YouTube 下載）需要它。
    echo.
    echo 安裝方式：
    echo   winget install ffmpeg
    echo   或手動下載：https://ffmpeg.org/download.html
) else (
    echo ✅ ffmpeg 已就緒
    ffmpeg -version | findstr "ffmpeg"
)

echo.
echo ====================================
echo ✅ 安裝完成！
echo.
echo 執行 run.bat 啟動評審團
echo ====================================
pause
