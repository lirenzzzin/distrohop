@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m distrohop.bootstrap %*
  exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
  python -m distrohop.bootstrap %*
  exit /b
)
where python3 >nul 2>nul
if %errorlevel%==0 (
  python3 -m distrohop.bootstrap %*
  exit /b
)
powershell.exe -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Python 3.9 ou superior nao foi encontrado. Instale com: winget install Python.Python.3.12','Distrohop')"
exit /b 127
