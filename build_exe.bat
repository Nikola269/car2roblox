@echo off
rem Builds Windows executables with PyInstaller, then (if Inno Setup is installed) a proper
rem "Setup.exe" install wizard around them. Needs Python 3.9+ installed (python.org).
rem Output:  dist\Car2Roblox.exe          (window, double-click)
rem          dist\car2roblox-cli.exe      (command line)
rem          dist\Car2RobloxSetup.exe     (install wizard - only if Inno Setup is installed, see below)
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 ( set "PY=py -3" ) else ( set "PY=python" )

%PY% -m venv .venv-build || goto :err
call .venv-build\Scripts\activate.bat || goto :err
python -m pip install --upgrade pip || goto :err
python -m pip install -r requirements.txt pyinstaller || goto :err

for /f "delims=" %%v in ('python -c "import car2roblox; print(car2roblox.__version__)"') do set "VER=%%v"

set "HIDDEN=--collect-submodules car2roblox --hidden-import PIL.DdsImagePlugin --hidden-import PIL.PngImagePlugin --hidden-import PIL.JpegImagePlugin --hidden-import PIL.TgaImagePlugin --hidden-import PIL.BmpImagePlugin"
set "ICON=--icon assets\icon.ico"

pyinstaller --noconfirm --clean --onefile --windowed --name Car2Roblox %ICON% %HIDDEN% launcher.py || goto :err
pyinstaller --noconfirm --onefile --console --name car2roblox-cli %ICON% %HIDDEN% launcher.py || goto :err

echo.
echo Done:
echo   dist\Car2Roblox.exe       - double-click to open the converter window
echo   dist\car2roblox-cli.exe   - command line version

rem ---- optional: wrap both into a proper Setup.exe install wizard with Inno Setup ----
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC for /f "delims=" %%p in ('where iscc 2^>nul') do if not defined ISCC set "ISCC=%%p"

if defined ISCC (
    "%ISCC%" /DMyAppVersion=%VER% installer\Car2Roblox.iss || goto :err
    echo   dist\Car2RobloxSetup.exe  - install wizard: Next, Next, Finish ^(this is the one to share^)
) else (
    echo.
    echo [no installer built] Inno Setup isn't installed, so only the two .exe files above were made.
    echo To also get a proper Setup.exe install wizard:
    echo   1. Install Inno Setup ^(free, about 3 MB^): https://jrsoftware.org/isdl.php
    echo   2. Re-run this build_exe.bat - it will find Inno Setup and build the wizard automatically.
    echo   ^(Or open installer\Car2Roblox.iss in the Inno Setup app and press Compile.^)
)

pause
exit /b 0

:err
echo.
echo Build failed - see the messages above.
pause
exit /b 1
