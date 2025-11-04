@echo off
setlocal enabledelayedexpansion
cd /d "C:\Users\Egor\Documents\3dTreeAuto"

if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo [WARN] venv not found, continuing without activation.
)

:loop
    echo === Running download.py ===
    python download.py --verbose
    if errorlevel 1 echo [ERROR] download.py failed & goto :wait

    echo === Running parse.py ===
    python parse.py --verbose
    if errorlevel 1 echo [ERROR] parse.py failed & goto :wait

    echo === Running generate_files.py ===
    python generate_files.py --verbose
    if errorlevel 1 echo [ERROR] generate_files.py failed & goto :wait

    echo === Running tag_orders.py ===
    python tag_orders.py --verbose
    if errorlevel 1 echo [ERROR] tag_orders.py failed & goto :wait

:wait
    echo === Cycle complete. Waiting 3600 seconds ===
    TIMEOUT /T 3600
    goto :loop
