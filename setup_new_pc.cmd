@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title 考试自动化系统 - 一键安装与环境修复

set "REPO_URL=https://github.com/Chlorinel/exam_exe.git"
set "RUNTIME_BRANCH=user_runtime"
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
for %%I in ("%~dp0.") do set "SCRIPT_DIR=%%~fI"
set "TARGET_DIR=%SCRIPT_DIR%\examworker"
set "USE_LOCAL_SOURCE="
if exist "%SCRIPT_DIR%\question_workflow_gui.py" if exist "%SCRIPT_DIR%\requirements.txt" (
    set "TARGET_DIR=%SCRIPT_DIR%"
    set "USE_LOCAL_SOURCE=1"
)
set "VENV_PYTHON=%TARGET_DIR%\.venv\Scripts\python.exe"
set "PATH=%PATH%;%ProgramFiles%\Git\cmd;%ProgramFiles(x86)%\Git\cmd;%LOCALAPPDATA%\Programs\Git\cmd"

if /I "%~1"=="--check" goto CHECK_ONLY
if not "%~1"=="" goto USAGE

echo.
echo ============================================================
echo   考试自动化系统 - 一键安装与环境修复
echo ============================================================
echo   仓库："%REPO_URL%"，分支：%RUNTIME_BRANCH%
echo   安装目录："%TARGET_DIR%"
echo.

call :GET_SOURCE
if errorlevel 1 goto FAILED
call :ENSURE_PYTHON
if errorlevel 1 goto FAILED
call :ENSURE_EDGE
if errorlevel 1 goto FAILED
call :SETUP_VENV
if errorlevel 1 goto FAILED
call :VERIFY_PROJECT
if errorlevel 1 goto FAILED
call :PREPARE_USER_FILES
if errorlevel 1 goto FAILED

echo.
echo ============================================================
echo   配置完成
echo ============================================================
echo   项目目录："%TARGET_DIR%"
echo   Python："%VENV_PYTHON%"
echo   AI 出题、审核和上传：双击 question_workflow_gui.pyw
echo   创建、编辑和发布考试：双击 exam_editor_gui.pyw
echo   已有考试配置和 work 工作记录会保留。
echo.
pause
exit /b 0

:ENSURE_WINGET
where winget >nul 2>nul
if not errorlevel 1 exit /b 0
echo [错误] 缺少 winget，无法安装当前缺少的基础软件。
echo 请从 Microsoft Store 安装“应用安装程序”，再重新运行本脚本。
echo https://apps.microsoft.com/detail/9nblggh4nns1
exit /b 1

:ENSURE_GIT
where git >nul 2>nul
if not errorlevel 1 exit /b 0
call :ENSURE_WINGET
if errorlevel 1 exit /b 1
echo [安装] Git
winget install --id Git.Git -e --source winget --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1
where git >nul 2>nul
if not errorlevel 1 exit /b 0
echo [错误] Git 安装后仍无法找到，请关闭窗口后重新运行脚本。
exit /b 1

:TRY_PYTHON
if defined PYTHON_EXE exit /b 0
if not exist "%~1" exit /b 1
if /I "%~dp1"=="%LOCALAPPDATA%\Microsoft\WindowsApps\" exit /b 1
"%~1" -E -c "import sys,sysconfig; sys.exit(0 if (3,10) <= sys.version_info < (3,15) and sysconfig.get_platform() == 'win-amd64' else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
set "PYTHON_EXE=%~1"
exit /b 0

:TRY_LAUNCHER
if defined PYTHON_EXE exit /b 0
for /f "delims=" %%P in ('py -%~1-64 -E -c "import sys; print(sys.executable)" 2^>nul') do call :TRY_PYTHON "%%P"
exit /b 0

:FIND_PYTHON
set "PYTHON_EXE="
for %%V in (3.12 3.11 3.13 3.14 3.10) do call :TRY_LAUNCHER %%V
for %%V in (312 311 313 314 310) do (
    call :TRY_PYTHON "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    call :TRY_PYTHON "%ProgramFiles%\Python%%V\python.exe"
)
for /f "delims=" %%P in ('where python.exe 2^>nul') do call :TRY_PYTHON "%%P"
if defined PYTHON_EXE exit /b 0
exit /b 1

:ENSURE_PYTHON
call :FIND_PYTHON
if not errorlevel 1 goto PYTHON_READY
call :ENSURE_WINGET
if errorlevel 1 exit /b 1
echo [安装] Python 3.12 x64
winget install --id Python.Python.3.12 -e --source winget --architecture x64 --scope user --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1
call :FIND_PYTHON
if not errorlevel 1 goto PYTHON_READY
echo [错误] 未找到可用的 Python 3.10 至 3.14 x64。
exit /b 1
:PYTHON_READY
echo [Python] "%PYTHON_EXE%"
exit /b 0

:FIND_EDGE
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" exit /b 0
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" exit /b 0
if exist "%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe" exit /b 0
exit /b 1

:ENSURE_EDGE
call :FIND_EDGE
if not errorlevel 1 exit /b 0
call :ENSURE_WINGET
if errorlevel 1 exit /b 1
echo [安装] Microsoft Edge
winget install --id Microsoft.Edge -e --source winget --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1
call :FIND_EDGE
exit /b %errorlevel%

:GET_SOURCE
if defined USE_LOCAL_SOURCE (
    echo [源码] 使用当前项目，修复环境；不更新当前代码。
    exit /b 0
)
call :ENSURE_GIT
if errorlevel 1 exit /b 1
if exist "%TARGET_DIR%\.git" goto UPDATE_SOURCE
if not exist "%TARGET_DIR%" goto CLONE_SOURCE
dir /b /a "%TARGET_DIR%" 2>nul | findstr . >nul
if errorlevel 1 goto CLONE_SOURCE
echo [错误] 目标目录非空且不是 Git 仓库："%TARGET_DIR%"
echo 请将安装脚本放在另一个目录，或在已有项目内部运行安装脚本。
exit /b 1
:CLONE_SOURCE
echo [下载] 克隆项目
git clone --depth 1 --single-branch --branch "%RUNTIME_BRANCH%" "%REPO_URL%" "%TARGET_DIR%"
exit /b %errorlevel%
:UPDATE_SOURCE
echo [更新] 获取最新代码，保留本地配置和工作记录
git -C "%TARGET_DIR%" fetch origin "%RUNTIME_BRANCH%:refs/remotes/origin/%RUNTIME_BRANCH%"
if errorlevel 1 exit /b 1
git -C "%TARGET_DIR%" checkout "%RUNTIME_BRANCH%"
if errorlevel 1 exit /b 1
git -C "%TARGET_DIR%" merge --ff-only "origin/%RUNTIME_BRANCH%"
if not errorlevel 1 exit /b 0
echo [错误] 本地修改或提交阻止更新，已停止；未强制覆盖本地代码。
exit /b 1

:SETUP_VENV
if not exist "%TARGET_DIR%\requirements.txt" exit /b 1
if not exist "%VENV_PYTHON%" goto REBUILD_VENV
"%VENV_PYTHON%" -E -c "import sys,sysconfig; sys.exit(0 if (3,10) <= sys.version_info < (3,15) and sysconfig.get_platform() == 'win-amd64' and sys.prefix != sys.base_prefix else 1)" >nul 2>nul
if errorlevel 1 goto REBUILD_VENV
"%VENV_PYTHON%" -E -m pip --version >nul 2>nul
if not errorlevel 1 goto INSTALL_REQUIREMENTS
:REBUILD_VENV
if not exist "%TARGET_DIR%\.venv" goto CREATE_VENV
echo [环境] 旧虚拟环境不可用，备份后重建
powershell -NoProfile -Command "$root=[IO.Path]::GetFullPath($env:TARGET_DIR); $item=Get-Item -Force -LiteralPath (Join-Path $root '.venv') -ErrorAction Stop; if ($item.Parent.FullName -ne $root -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unsafe virtual environment path' }; $backup=Join-Path $root ('.venv.backup-'+[guid]::NewGuid().ToString('N')); Move-Item -LiteralPath $item.FullName -Destination $backup -ErrorAction Stop; Write-Host ('Backup: '+$backup)"
if errorlevel 1 exit /b 1
:CREATE_VENV
echo [环境] 创建项目专用 Python 环境
"%PYTHON_EXE%" -E -m venv "%TARGET_DIR%\.venv"
if errorlevel 1 exit /b 1
:INSTALL_REQUIREMENTS
echo [依赖] 安装到项目专用环境
"%VENV_PYTHON%" -E -m pip install --upgrade pip --retries 3 --timeout 60
if errorlevel 1 exit /b 1
"%VENV_PYTHON%" -E -m pip install -r "%TARGET_DIR%\requirements.txt" --retries 3 --timeout 60
exit /b %errorlevel%

:VERIFY_PROJECT
echo [检查] 核对依赖并实际导入程序和本地公式组件
"%VENV_PYTHON%" -E -m pip check
if errorlevel 1 exit /b 1
pushd "%TARGET_DIR%"
if errorlevel 1 exit /b 1
"%VENV_PYTHON%" -E -c "import selenium, PySide6; from pathlib import Path; from PySide6.QtQml import QJSEngine; from PySide6.QtSvg import QSvgRenderer; assert all(Path('assets/mathjax', name).is_file() for name in ('native-math.js','tex-svg-full.js')), 'MathJax assets are missing; download the complete project'; import question_workflow_gui, exam_editor_gui, gui_launcher; print('Import check OK: Selenium', selenium.__version__, 'PySide6', PySide6.__version__)"
set "VERIFY_EXIT=%errorlevel%"
popd
exit /b %VERIFY_EXIT%

:PREPARE_USER_FILES
echo [配置] 准备用户考试配置表和工作目录
"%VENV_PYTHON%" -E "%TARGET_DIR%\config_writer.py" --prepare-install "%TARGET_DIR%"
exit /b %errorlevel%

:CHECK_ONLY
echo [检查] 项目目录："%TARGET_DIR%"
call :FIND_EDGE
if errorlevel 1 (
    echo [缺少] Microsoft Edge
    exit /b 1
)
if not exist "%VENV_PYTHON%" (
    echo [缺少] 项目虚拟环境，请不带 --check 参数运行安装脚本。
    exit /b 1
)
call :VERIFY_PROJECT
exit /b %errorlevel%

:USAGE
echo 用法：双击安装；或 setup_new_pc.cmd --check 只检查当前环境。
exit /b 2

:FAILED
echo.
echo [失败] 配置未完成，请保留上方错误信息。
echo 修复网络或软件安装问题后，可重新运行本脚本继续。
pause
exit /b 1
