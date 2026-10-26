@echo off
rem Joue la seance du jour. A lancer apres 16 h 20 (heure de Toronto) par le Planificateur de taches.
cd /d "%~dp0.."
if not exist data mkdir data
".venv\Scripts\python.exe" -m chubby.cli tick >> data\tick.log 2>&1
