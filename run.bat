@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM run.bat - Windows menu mirroring justfile recipes for
REM msb_ssis2sql. Run from the repo root in cmd.exe.
REM Prereq: uv installed and on PATH (https://docs.astral.sh/uv/).
REM ============================================================

:menu
cls
echo ============================================================
echo  msb_ssis2sql - Windows launcher (mirrors justfile)
echo ============================================================
echo.
echo  ENVIRONMENT
echo    1.  install                 - uv sync (sync venv)
echo    2.  lock                    - uv lock (refresh lockfile)
echo    3.  clean                   - remove .venv, caches, build artefacts
echo   21.  install-offline         - create .venv and install from wheels\ (no internet)
echo.
echo  QUALITY GATES
echo    4.  test                    - run pytest
echo    5.  cov                     - pytest with coverage
echo    6.  lint                    - ruff check
echo    7.  typecheck               - mypy
echo.
echo  CONVERSION
echo    8.  demo                    - convert examples\sales_etl.dtsx
echo    9.  migrate-file            - convert one .dtsx (prompts)
echo   10.  migrate-directory       - convert a tree of .dtsx (prompts)
echo   11.  inspect                 - print parsed component graph (prompts)
echo   12.  extract-agent-jobs      - extract msdb agent jobs to YAML (prompts)
echo.
echo  UI / SERVER
echo   13.  tui                     - launch Textual control panel
echo   14.  web                     - serve TUI in browser (prompts)
echo.
echo  VALIDATION FRAMEWORK
echo   15.  validate                - full differential validation (needs SQL Server)
echo   16.  validate-unit           - validation framework unit tests
echo   17.  validate-cov            - validation unit tests with coverage
echo   18.  validate-static         - validation static structural checks
echo.
echo  ADVANCED
echo   19.  extract-agent-jobs-smoke - live SQL Server smoke (Docker)
echo   20.  convert-samples         - convert every .dtsx under examples\samples
echo   22.  extract-packages-smoke  - live SQL Server smoke for package extractor
echo.
echo    0.  exit
echo.
set /p choice="Select: "

if "%choice%"=="1"  goto install
if "%choice%"=="2"  goto lock
if "%choice%"=="3"  goto clean
if "%choice%"=="4"  goto test
if "%choice%"=="5"  goto cov
if "%choice%"=="6"  goto lint
if "%choice%"=="7"  goto typecheck
if "%choice%"=="8"  goto demo
if "%choice%"=="9"  goto migrate_file
if "%choice%"=="10" goto migrate_directory
if "%choice%"=="11" goto inspect
if "%choice%"=="12" goto extract_agent_jobs
if "%choice%"=="13" goto tui
if "%choice%"=="14" goto web
if "%choice%"=="15" goto validate
if "%choice%"=="16" goto validate_unit
if "%choice%"=="17" goto validate_cov
if "%choice%"=="18" goto validate_static
if "%choice%"=="19" goto extract_agent_jobs_smoke
if "%choice%"=="20" goto convert_samples
if "%choice%"=="21" goto install_offline
if "%choice%"=="22" goto extract_packages_smoke
if "%choice%"=="0"  goto end

echo Unknown choice: %choice%
pause
goto menu

REM ------------------------------------------------------------
:install
echo.
echo Running: uv sync
uv sync
goto done

:lock
echo.
echo Running: uv lock
uv lock
goto done

:clean
echo.
echo Removing: .venv .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info __pycache__
if exist .venv         rmdir /s /q .venv
if exist .pytest_cache rmdir /s /q .pytest_cache
if exist .ruff_cache   rmdir /s /q .ruff_cache
if exist .mypy_cache   rmdir /s /q .mypy_cache
if exist build         rmdir /s /q build
if exist dist          rmdir /s /q dist
for /d /r %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"
for /d %%d in (*.egg-info) do if exist "%%d" rmdir /s /q "%%d"
goto done

REM ------------------------------------------------------------
:test
echo.
echo Running: uv run pytest
uv run pytest
goto done

:cov
echo.
echo Running: uv run pytest --cov=msb_ssis2sql --cov-report=term-missing
uv run pytest --cov=msb_ssis2sql --cov-report=term-missing
goto done

:lint
echo.
echo Running: uv run ruff check .
uv run ruff check .
goto done

:typecheck
echo.
echo Running: uv run mypy msb_ssis2sql validation
uv run mypy msb_ssis2sql validation
goto done

REM ------------------------------------------------------------
:demo
echo.
echo Running: uv run python -m msb_ssis2sql convert examples\sales_etl.dtsx
uv run python -m msb_ssis2sql convert examples\sales_etl.dtsx
goto done

:migrate_file
echo.
set /p src="Source .dtsx path: "
set /p dst="Output .sql path: "
if "%src%"=="" goto cancel
if "%dst%"=="" goto cancel
echo.
echo Running: uv run python -m msb_ssis2sql convert "%src%" -o "%dst%"
uv run python -m msb_ssis2sql convert "%src%" -o "%dst%"
goto done

:migrate_directory
echo.
set /p src="Input directory: "
set /p dst="Output directory: "
if "%src%"=="" goto cancel
if "%dst%"=="" goto cancel
echo.
echo Running: uv run python -m msb_ssis2sql convert-tree "%src%" "%dst%"
uv run python -m msb_ssis2sql convert-tree "%src%" "%dst%"
goto done

:inspect
echo.
set /p src=".dtsx path to inspect: "
if "%src%"=="" goto cancel
echo.
echo Running: uv run python -m msb_ssis2sql inspect "%src%"
uv run python -m msb_ssis2sql inspect "%src%"
goto done

:extract_agent_jobs
echo.
echo MSDB connection settings are read from MSDB_DSN / MSDB_USER / MSDB_PASSWORD env vars,
echo or --dsn below overrides MSDB_DSN.
echo.
set /p dsn="DSN (leave blank to use MSDB_DSN env): "
set /p out="Output directory for YAML (default: jobs): "
set /p job_filter="Optional job-name LIKE pattern (blank for all): "
set /p proc_manifest="Optional path to _proc_manifest.json (blank to skip SSIS step rewriting): "
if "%out%"=="" set out=jobs

set cmd=uv run python -m msb_ssis2sql extract-agent-jobs --out "%out%"
if not "%dsn%"==""           set cmd=!cmd! --dsn "%dsn%"
if not "%job_filter%"==""    set cmd=!cmd! --filter "%job_filter%"
if not "%proc_manifest%"=="" set cmd=!cmd! --proc-manifest "%proc_manifest%"

echo.
echo Running: !cmd!
!cmd!
goto done

REM ------------------------------------------------------------
:tui
echo.
echo Running: uv run python -m msb_ssis2sql.tui
uv run python -m msb_ssis2sql.tui
goto done

:web
echo.
set /p host="Host (default: localhost): "
set /p port="Port (default: 8000): "
if "%host%"=="" set host=localhost
if "%port%"=="" set port=8000
echo.
echo Running: uv run python -m msb_ssis2sql.web --host %host% --port %port%
uv run python -m msb_ssis2sql.web --host %host% --port %port%
goto done

REM ------------------------------------------------------------
:validate
echo.
echo Running: uv run pytest validation/ -m validation
uv run pytest validation/ -m validation
goto done

:validate_unit
echo.
echo Running: uv run pytest validation/tests
uv run pytest validation/tests
goto done

:validate_cov
echo.
echo Running: uv run pytest validation/tests --cov=validation --cov-report=term-missing --cov-report=json
uv run pytest validation/tests --cov=validation --cov-report=term-missing --cov-report=json
goto done

:validate_static
echo.
echo Running: uv run pytest validation/test_static.py
uv run pytest validation/test_static.py
goto done

REM ------------------------------------------------------------
:extract_agent_jobs_smoke
echo.
echo Running: uv run pytest validation/ -m agent_smoke
echo NOTE: This target expects a containerised SQL Server (Docker on Windows).
echo       See plan-final.md section 9 for setup.
uv run pytest validation/ -m agent_smoke
goto done

REM ------------------------------------------------------------
:extract_packages_smoke
echo.
echo Running: uv run pytest validation/ -m package_smoke
echo NOTE: Needs MSSQL_SERVER_ADDRESS set and a SQL Server reachable via
echo       Windows Integrated auth (e.g. a domain-joined host). Skips otherwise.
uv run pytest validation/ -m package_smoke
goto done

:convert_samples
echo.
echo Convert every .dtsx under examples\samples (skipping bin\ build copies).
if not exist generated_scripts mkdir generated_scripts
set /a count=0
for /r examples\samples %%f in (*.dtsx) do (
    echo %%f | findstr /i "\\bin\\" >nul
    if errorlevel 1 (
        set "src=%%f"
        for %%n in ("%%~nf") do set "name=%%~nn"
        set "out=generated_scripts\!name!.sql"
        echo Converting "%%f" -^> "!out!"
        uv run python -m msb_ssis2sql convert "%%f" -o "!out!" -vv
        set /a count+=1
    )
)
echo.
echo Done: !count! package(s) converted into generated_scripts\
goto done

REM ------------------------------------------------------------
:install_offline
echo.
echo Offline install from wheels\ (requires Python 3.14 win_amd64 on PATH).
if not exist wheels\requirements.txt (
    echo ERROR: wheels\requirements.txt not found. Bundle missing.
    goto done
)
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found on PATH. Install Python 3.14 first.
    goto done
)
if not exist .venv (
    echo Creating .venv with system python ...
    python -m venv .venv
    if errorlevel 1 goto done
)
echo Bootstrapping pip in venv ...
.venv\Scripts\python.exe -m ensurepip --upgrade
echo Upgrading pip / setuptools / wheel from bundle ...
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels\ --upgrade pip setuptools wheel
echo Installing dependencies from wheels\ (no network) ...
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels\ --requirement wheels\requirements.txt
if errorlevel 1 goto done
echo Installing project (editable) ...
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels\ --no-build-isolation -e .
goto done

REM ------------------------------------------------------------
:cancel
echo.
echo Cancelled - empty input.
goto done

:done
echo.
pause
goto menu

:end
endlocal
exit /b 0
