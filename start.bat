@echo off
chcp 65001 >nul
title 诚载网络智能选品系统 - 启动

echo ========================================
echo   诚载网络智能选品系统
echo   启动中...
echo ========================================
echo.

:: ── 1. 启动 Chrome CDP 调试模式 ──
echo [1/3] 正在启动 Chrome CDP 调试模式...

:: 检查 Chrome 是否已在调试端口运行
curl -s http://localhost:9222/json/version >nul 2>&1
if %errorlevel%==0 (
    echo       Chrome CDP 已在运行，跳过启动
) else (
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=C:\Users\hzf16\ChromeDebug
    echo       Chrome CDP 已启动 (端口 9222)
    :: 等待 Chrome 启动
    timeout /t 3 /nobreak >nul
)

:: 验证 CDP 连接
curl -s http://localhost:9222/json/version >nul 2>&1
if %errorlevel%==0 (
    echo       CDP 连接成功
) else (
    echo       警告: CDP 连接失败，请检查 Chrome 是否安装
)

echo.

:: ── 2. 启动 Flask 后端 ──
echo [2/3] 正在启动 Flask 后端...

:: 检查 Flask 是否已在运行
curl -s http://localhost:8006/ >nul 2>&1
if %errorlevel%==0 (
    echo       Flask 后端已在运行，跳过启动
) else (
    start /B python -X utf8 app.py
    echo       Flask 后端已启动 (端口 8006)
    :: 等待 Flask 启动
    timeout /t 3 /nobreak >nul
)

:: 验证 Flask
curl -s http://localhost:8006/ >nul 2>&1
if %errorlevel%==0 (
    echo       Flask 后端启动成功
) else (
    echo       警告: Flask 后端启动失败，请手动运行: python app.py
)

echo.

:: ── 3. 打开浏览器 ──
echo [3/3] 正在打开浏览器...
timeout /t 1 /nobreak >nul
start http://localhost:8006

echo.
echo ========================================
echo   启动完成!
echo   浏览器: http://localhost:8006
echo   CDP 端口: 9222
echo   Flask 端口: 8006
echo ========================================
echo.
pause
