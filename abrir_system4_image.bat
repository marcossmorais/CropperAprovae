@echo off
rem System 4.0 Image - abre o aplicativo (janela propria, sem terminal)
rem Acervo (Documentos, logs): variavel SYSTEM3_ROOT; padrao Documents\CLAUDE\System3.0
rem Para o modo antigo no navegador: python server.py
if not defined SYSTEM3_ROOT set "SYSTEM3_ROOT=%USERPROFILE%\Documents\CLAUDE\System3.0"
cd /d "%~dp0"
start "" pythonw app.py
