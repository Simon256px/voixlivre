@echo off
rem Double-cliquez pour lancer VoixLivre (sans fenêtre de console)
cd /d "%~dp0"
start "" pythonw voixlivre.py %*
