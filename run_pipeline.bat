@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ============================================================
echo   卫星影像高精度自动化下载压制与网盘备份一键运行脚本
echo ============================================================
echo.

:: 检查参数
if "%~1"=="" (
    echo [提示] 请输入目标城市名（如 威海 或 深圳市）或省份名（如 辽宁）
    echo.
    set /p INPUT_NAME="请输入目标区域名称: "
    if "!INPUT_NAME!"=="" (
        echo [错误] 输入不能为空，程序退出。
        pause
        exit /b 1
    )
) else (
    set INPUT_NAME=%~1
)

:: 获取脚本所在目录并切换
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

:: 设置 Python 环境路径
set PYTHON_EXE=C:\Users\81052\anaconda3\envs\sat\python.exe

if not exist "%PYTHON_EXE%" (
    echo [警告] 未在默认路径找到 Python 环境:
    echo %PYTHON_EXE%
    echo 正在尝试调用系统默认 conda 环境...
    
    :: 尝试通过 conda 激活
    call conda activate sat > nul 2>&1
    if errorlevel 1 (
        echo [错误] 无法激活 conda 环境 'sat'，请检查环境是否存在！
        pause
        exit /b 1
    )
    set PYTHON_CMD=python
) else (
    set PYTHON_CMD="%PYTHON_EXE%"
)

echo [信息] 环境准备就绪，正在启动分流检测...

:: 动态读取省份 Shapefile 数据来判定是省份运行还是单城市运行
%PYTHON_CMD% -c "import sys, os, geopandas as gpd; shp='sheng_detailed/sheng_wgs84.shp'; sys.exit(0 if os.path.exists(shp) and sys.argv[1].replace('省','') in gpd.read_file(shp)['pr_name'].str.replace('省','').tolist() else 1)" "!INPUT_NAME!" > nul 2>&1

if errorlevel 1 (
    echo.
    echo [分流] 判定为 [城市级] 影像处理，启动单城市流水线...
    echo ------------------------------------------------------------
    %PYTHON_CMD% run.py "!INPUT_NAME!"
) else (
    echo.
    echo [分流] 判定为 [省份级] 影像处理，启动多城市异步双引擎流水线...
    echo ------------------------------------------------------------
    %PYTHON_CMD% run_province.py "!INPUT_NAME!"
)

echo.
echo ============================================================
echo   流水线运行结束。
echo ============================================================
pause
