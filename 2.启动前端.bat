@echo off
title Æô¶¯Ç°¶Ë
cd nginx-1.27.4
.\nginx.exe -g "daemon off;"
taskkill /f /im nginx.exe