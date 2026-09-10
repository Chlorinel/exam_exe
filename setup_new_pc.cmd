@echo off
setlocal EnableExtensions
chcp 65001 >nul
title 考试自动化系统 - 新电脑环境配置

set "REPO_URL=https://github.com/Chlorinel/exam_exe.git"
for %%I in ("%~dp0.") do set "SCRIPT_DIR=%%~fI"
set "TARGET_DIR=%USERPROFILE%\Documents\exam_exe"
if exist "%SCRIPT_DIR%\.git" set "TARGET_DIR=%SCRIPT_DIR%"

if /I "%~1"=="--check" goto CHECK_ONLY

echo.
echo ============================================================
echo   考试自动化系统 - 新电脑一键配置
echo ============================================================
echo   GitHub：%REPO_URL%
echo   安装目录：%TARGET_DIR%
echo.

call :ENSURE_WINGET
if errorlevel 1 goto FAILED

call :ENSURE_GIT
if errorlevel 1 goto FAILED

call :ENSURE_PYTHON
if errorlevel 1 goto FAILED

call :ENSURE_EDGE
if errorlevel 1 goto FAILED

call :GET_SOURCE
if errorlevel 1 goto FAILED

call :SETUP_VENV
if errorlevel 1 goto FAILED

call :VERIFY_PROJECT
if errorlevel 1 goto FAILED

echo.
echo ============================================================
echo   配置完成
echo ============================================================
echo   项目目录：%TARGET_DIR%
echo   Python：%TARGET_DIR%\.venv\Scripts\python.exe
echo.
echo   下次运行可进入项目目录，双击 exam_control_gui.pyw；
echo   如果仓库版本尚无图形界面，请运行：
echo   .venv\Scripts\python.exe create_signal_exam.py --help
echo.
pause
exit /b 0

:ENSURE_WINGET
where winget >nul 2>nul
if not errorlevel 1 exit /b 0
echo [错误] 系统缺少 winget，无法自动安装基础软件。
echo 请先从 Microsoft Store 安装“应用安装程序”，再重新双击本脚本。
start "" "ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1"
exit /b 1

:ENSURE_GIT
set "PATH=%PATH%;C:\Program Files\Git\cmd;%LOCALAPPDATA%\Programs\Git\cmd"
where git >nul 2>nul
if not errorlevel 1 (
    echo [已存在] Git
    exit /b 0
)
echo [安装] Git
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1
set "PATH=%PATH%;C:\Program Files\Git\cmd;%LOCALAPPDATA%\Programs\Git\cmd"
where git >nul 2>nul
if errorlevel 1 (
    echo [错误] Git 已安装，但当前窗口尚未识别。请关闭窗口后重新双击脚本。
    exit /b 1
)
exit /b 0

:FIND_PYTHON
set "PYTHON_EXE="
for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if defined PYTHON_EXE goto PYTHON_FOUND
for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if defined PYTHON_EXE goto PYTHON_FOUND
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PYTHON_EXE goto PYTHON_FOUND
if exist "C:\Program Files\Python312\python.exe" set "PYTHON_EXE=C:\Program Files\Python312\python.exe"
:PYTHON_FOUND
if not defined PYTHON_EXE exit /b 1
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
exit /b %errorlevel%

:ENSURE_PYTHON
call :FIND_PYTHON
if not errorlevel 1 (
    echo [已存在] Python 3.10 或更高版本：%PYTHON_EXE%
    exit /b 0
)
echo [安装] Python 3.12
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1
set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312;C:\Program Files\Python312"
call :FIND_PYTHON
if errorlevel 1 (
    echo [错误] Python 已安装，但当前窗口尚未识别。请关闭窗口后重新双击脚本。
    exit /b 1
)
exit /b 0

:ENSURE_EDGE
if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (
    echo [已存在] Microsoft Edge
    exit /b 0
)
if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" (
    echo [已存在] Microsoft Edge
    exit /b 0
)
echo [安装] Microsoft Edge
winget install --id Microsoft.Edge -e --source winget --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1
exit /b 0

:GET_SOURCE
if exist "%TARGET_DIR%\.git" (
    echo [更新] 正在从 GitHub 获取最新代码
    git -C "%TARGET_DIR%" pull --ff-only
    exit /b %errorlevel%
)
if exist "%TARGET_DIR%" (
    dir /b "%TARGET_DIR%" 2>nul | findstr . >nul
    if not errorlevel 1 (
        echo [错误] 目标目录已经存在且不是 Git 仓库：%TARGET_DIR%
        echo 请将该目录改名，或把本脚本放进已有 exam_exe 仓库后再运行。
        exit /b 1
    )
)
echo [下载] 正在从 GitHub 克隆项目
git clone "%REPO_URL%" "%TARGET_DIR%"
exit /b %errorlevel%

:SETUP_VENV
echo [环境] 创建项目专用 Python 环境
if not exist "%TARGET_DIR%\.venv\Scripts\python.exe" (
    "%PYTHON_EXE%" -m venv "%TARGET_DIR%\.venv"
    if errorlevel 1 exit /b 1
)
"%TARGET_DIR%\.venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%TARGET_DIR%\.venv\Scripts\python.exe" -m pip install -r "%TARGET_DIR%\requirements.txt"
exit /b %errorlevel%

:VERIFY_PROJECT
echo [检查] 验证主要程序可以加载
"%TARGET_DIR%\.venv\Scripts\python.exe" -m py_compile ^
    "%TARGET_DIR%\create_signal_exam.py" ^
    "%TARGET_DIR%\deepseek_question_generator.py" ^
    "%TARGET_DIR%\platform_question_uploader.py"
exit /b %errorlevel%

:CHECK_ONLY
echo 项目安装检查
echo GitHub：%REPO_URL%
echo 目标目录：%TARGET_DIR%
where winget >nul 2>nul && (echo [正常] winget) || (echo [缺少] winget)
set "PATH=%PATH%;C:\Program Files\Git\cmd;%LOCALAPPDATA%\Programs\Git\cmd"
where git >nul 2>nul && (echo [正常] Git) || (echo [缺少] Git)
call :FIND_PYTHON
if errorlevel 1 (echo [缺少] Python 3.10+) else (echo [正常] Python：%PYTHON_EXE%)
if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (echo [正常] Microsoft Edge) else if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" (echo [正常] Microsoft Edge) else (echo [缺少] Microsoft Edge)
if exist "%TARGET_DIR%\.git" (echo [正常] 项目仓库) else (echo [尚未安装] 项目仓库)
if exist "%TARGET_DIR%\.venv\Scripts\python.exe" (echo [正常] 项目虚拟环境) else (echo [尚未配置] 项目虚拟环境)
exit /b 0

:FAILED
echo.
echo ============================================================
echo   配置未完成，请查看上方错误信息。
echo ============================================================
echo.
pause
exit /b 1
