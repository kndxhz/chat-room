@echo off
title 启动前端
echo 已经启动在 http://127.0.0.1
cd nginx-1.27.4
.\nginx.exe -g "daemon off;"
taskkill /f /im nginx.exe