@echo off
chcp 65001 >nul
echo ============================================
echo   注册本地后端常驻服务（只需双击一次）
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\rokid\rokid\tmp\setup_local_backend.ps1"
echo.
pause
