@echo off
chcp 65001 > nul
cd /d "%~dp0"
if defined GEO_PYTHON (
  "%GEO_PYTHON%" pipeline.py %*
) else (
  call conda run --no-capture-output -n sat python pipeline.py %*
)
exit /b %errorlevel%
