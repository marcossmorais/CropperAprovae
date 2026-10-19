@echo off
rem System 4.0 Image - abre a tela de revisao de recortes no navegador
rem Acervo (Documentos, logs): variavel SYSTEM3_ROOT; padrao Documents\CLAUDE\System3.0
if not defined SYSTEM3_ROOT set "SYSTEM3_ROOT=%USERPROFILE%\Documents\CLAUDE\System3.0"
cd /d "%~dp0"
python server.py
pause
