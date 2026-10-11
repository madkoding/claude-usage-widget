@echo off
cd /d "%~dp0"
set "RUN="
set "PYC="
where pythonw >nul 2>nul
if not errorlevel 1 (
  set "RUN=pythonw"
  set "PYC=python"
)
if not defined RUN (
  where pyw >nul 2>nul
  if not errorlevel 1 (
    set "RUN=pyw"
    set "PYC=py"
  )
)
if not defined RUN (
  echo Python 3 no encontrado. Instalalo desde python.org, incluye tkinter.
  pause
  exit /b 1
)
%PYC% -c "import webview" >nul 2>nul
if errorlevel 1 (
  echo Instalando pywebview, solo la primera vez...
  %PYC% -m pip install --user pywebview
)
start "" %RUN% "%~dp0claude_usage_widget.py"
