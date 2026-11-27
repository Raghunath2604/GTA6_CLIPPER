@echo off
title GTA6 Viral Clipper Pro - Web Dashboard
echo Starting GTA6 Viral Clipper Pro...
set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=python"
"%PY_EXE%" -m streamlit run app.py --server.port 8501
pause
