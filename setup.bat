@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
if not "%~1"=="" (
    "%~1" -m venv .venv
    goto checkvenv
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv .venv
    goto checkvenv
)
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    python -m venv .venv
    goto checkvenv
)
echo Bitte Python 3.10 oder neuer installieren und Python zum PATH hinzufuegen.
echo Alternativ: setup.bat "C:\Pfad\zu\python.exe"
goto failed
:checkvenv
if errorlevel 1 goto failed
if not exist ".venv\Scripts\python.exe" goto failed
:install
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if errorlevel 1 (
    echo Die vorhandene virtuelle Umgebung verwendet kein unterstuetztes Python ab Version 3.10.
    echo Bitte den Ordner .venv entfernen und setup.bat erneut ausfuehren.
    goto failed
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist "data" mkdir "data"
if not exist "user_data" mkdir "user_data"
if not exist "knowledge_base" mkdir "knowledge_base"
if not exist "chroma_db" mkdir "chroma_db"
".venv\Scripts\python.exe" -c "from src.persistence.database import initialize_database; initialize_database()"
if errorlevel 1 goto failed
echo Einrichtung erfolgreich. Die Anwendung kann mit start.bat gestartet werden.
if not defined AI_LEARNING_COMPANION_NO_PAUSE pause
exit /b 0
:failed
echo Einrichtung fehlgeschlagen. Bitte die Meldung oben pruefen.
if not defined AI_LEARNING_COMPANION_NO_PAUSE pause
exit /b 1
