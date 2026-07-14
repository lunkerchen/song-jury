@echo off
chcp 65001 >nul
title 歌曲評審團

:: 檢查 .venv
if not exist ".venv" (
    echo ❌ 找不到 .venv。
    echo 請先執行 setup_windows.bat 安裝依賴。
    pause
    exit /b 1
)

echo 🎼 啟動歌曲評審團...
echo 瀏覽器打開後請稍等約 30 秒（首次載入套件）
echo.
echo 評分完成後，瀏覽器會自動顯示成績單
echo 按 Ctrl+C 可停止伺服器
echo.

:: 啟動 Gradio
call .venv\Scripts\activate.bat
python app.py

:: 如果 app.py 不在 .venv 路徑，嘗試系統 Python
if %errorlevel% neq 0 (
    echo.
    echo ⚠️  啟動失敗。嘗試系統 Python...
    python app.py
)

pause
