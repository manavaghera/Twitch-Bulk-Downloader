@echo off
rem Clip Studio - double-click to open the web page at http://localhost:8501
rem First run creates a private Python environment in .venv and installs
rem what the page needs; later runs start straight away. Close this window
rem to stop the page.

cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo Setting up for the first time - this takes a minute or two...
    python -m venv .venv || goto :no_python
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r scripts\requirements.txt || goto :failed
)

".venv\Scripts\python.exe" -m streamlit run scripts\web_app.py --browser.gatherUsageStats false
goto :eof

:no_python
echo Could not find Python. Install it from https://www.python.org and tick
echo "Add python.exe to PATH" during setup, then run this again.
pause
goto :eof

:failed
echo Installing the requirements failed - see the messages above.
pause
