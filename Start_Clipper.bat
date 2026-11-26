@echo off
title GTAG Viral Clipper Pro - Web Dashboard
echo Starting GTAG Viral Clipper Pro...
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m streamlit run app.py --server.port 8501
pause
