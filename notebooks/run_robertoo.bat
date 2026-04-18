@echo off
setlocal
set "PY=C:\agenteIA\Winpython64-3.12.9.0dot\WPy64-31290\python\python.exe"
set "SCRIPT=%~dp0agente_inteligente.py"
"%PY%" "%SCRIPT%"
pause
endlocal
