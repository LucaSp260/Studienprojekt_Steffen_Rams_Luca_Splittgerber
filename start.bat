@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Bitte zuerst setup.bat ausfuehren.
    if not defined AI_LEARNING_COMPANION_NO_PAUSE pause
    exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run app.py
if errorlevel 1 (
    echo Start fehlgeschlagen. Bitte setup.bat ausfuehren und die Meldung oben pruefen.
    if not defined AI_LEARNING_COMPANION_NO_PAUSE pause
    exit /b 1
)
