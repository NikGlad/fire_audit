@echo off
setlocal
set "APP_ROOT=%~dp0"
set "BUNDLED_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%BUNDLED_PYTHON%" (
  "%BUNDLED_PYTHON%" -u "%APP_ROOT%mcp_server.py" --root "%APP_ROOT%"
  exit /b %errorlevel%
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -u "%APP_ROOT%mcp_server.py" --root "%APP_ROOT%"
  exit /b %errorlevel%
)
where python >nul 2>nul
if %errorlevel%==0 (
  python -u "%APP_ROOT%mcp_server.py" --root "%APP_ROOT%"
  exit /b %errorlevel%
)
echo Python 3.11 or newer was not found. 1>&2
exit /b 1
