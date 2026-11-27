@echo off
title GTA6 Viral Clipper Pro - CLI Login
set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=python"
"%PY_EXE%" login_cli.py
pause
