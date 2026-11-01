@echo off
REM Spusti runner Knowledge OS (okno nechte otevrene). Dvojklik na tento soubor.
cd /d "%~dp0"
title Knowledge OS runner
python -m pipeline runner
pause
