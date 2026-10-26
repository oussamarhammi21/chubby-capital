@echo off
rem Ouvre le tableau de bord (et le direct pendant la bourse) sur http://localhost:8000
cd /d "%~dp0.."
start "" http://localhost:8000
".venv\Scripts\python.exe" -m chubby.cli web
