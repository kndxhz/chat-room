@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
pushd "%ROOT%"

echo 正在运行测试...
call "%ROOT%python-3.13.2-embed-amd64\python.exe" -m pytest tests -q
if errorlevel 1 (
    echo 测试失败，未执行清理。
    popd
    exit /b 1
)

echo 测试通过，正在清理生成文件...
call :clear_dir "%ROOT%.pytest_cache"
call :clear_dir "%ROOT%__pycache__"
call :clear_dir "%ROOT%tests\__pycache__"
call :clear_dir "%ROOT%html\__pycache__"
del "%ROOT%chat.db"
del "%ROOT%chat.db-journal"
del "%ROOT%chat.db-shm"
del "%ROOT%chat.db-wal"
call :truncate_file "%ROOT%key.txt"
call :truncate_file "%ROOT%chat-room.log"
call :truncate_file "%ROOT%ban.txt"
call :clear_dir "%ROOT%files"

echo 清理完成。
pause

:clear_dir
if exist "%~1\" (
    rd /s /q "%~1" >nul 2>nul
)
exit /b 0

:truncate_file
if exist "%~1" (
    break> "%~1"
)
exit /b 0