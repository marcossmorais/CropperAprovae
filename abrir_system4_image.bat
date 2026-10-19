@echo off
rem System 4.0 Image - abre a tela de revisao de recortes no navegador
cd /d "%~dp0\..\.."
python scripts\system4_image\server.py
pause
