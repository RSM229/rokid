@echo off
chcp 65001 >nul
REM ============================================================
REM  监理通 本地联调一键启动（隧道版：本地代码 + 服务器共享库 gdjl）
REM  后端跑在 8000（隧道版：本地代码 + 服务器共享库 gdjl）
REM  关闭本窗口即停止隧道与后端。
REM ============================================================
cd /d D:\rokid\rokid\cloud-backend

echo [1/3] 检查端口占用...
netstat -ano | findstr ":8000 " | findstr LISTENING >nul && (
  echo   !! 8000 已被占用，先关掉旧进程再启动。
  pause & exit /b 1
)

echo [2/3] 启动 SSH 数据库隧道 13308 -^> 服务器 3308 ...
netstat -ano | findstr ":13308 " | findstr LISTENING >nul || (
  start "rokid-tunnel" /min ssh -N -p 7090 -i %USERPROFILE%\.ssh\rokid_deploy -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:13308:127.0.0.1:3308 baize@8.163.115.127
)
timeout /t 3 /nobreak >nul

echo [3/3] 启动本地后端 http://127.0.0.1:8000 （--reload 热重载）...
echo   小程序请在开发者工具「重新编译」；接口文档 http://127.0.0.1:8000/docs
set PYTHONUTF8=1
.venv\Scripts\python.exe -c "from scripts.run_local import configure_local, BACKEND_DIR; configure_local(); import uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port=8000, reload=True, reload_dirs=[str(BACKEND_DIR / 'app')])"
pause
