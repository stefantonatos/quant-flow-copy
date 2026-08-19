@echo off
REM ===========================================================================
REM  start_bridge.bat - one click to bring the whole bridge up.
REM
REM  Starts the webhook server, opens your TradingView chart in Brave, and
REM  optionally launches MT5. Put a shortcut to this on your desktop and the
REM  whole setup becomes one double-click.
REM
REM  EDIT THE SETTINGS BELOW ONCE, then never think about them again.
REM ===========================================================================

REM ---------------------------------------------------------------------------
REM  SETTINGS -- change these to match your setup
REM ---------------------------------------------------------------------------

REM The shared secret. Must match what you typed into the extension's Settings.
set SECRET=bananabread12345

REM Which symbols the bridge is allowed to trade. Comma separated, no spaces.
set SYMBOLS=EURNZD

REM Your MT5 queue file. This is the terminal folder MT5 showed you under
REM File -> Open Data Folder, with \MQL5\Files\queue.txt on the end.
set QUEUE=C:\Users\Evi\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files\queue.txt

REM The chart to open.
set CHART=https://www.tradingview.com/chart/yBVDuN0x/?symbol=OANDA%%3AEURNZD

REM Set to 1 to also launch MetaTrader 5, 0 to leave it alone.
set LAUNCH_MT5=1

REM ---------------------------------------------------------------------------
REM  Nothing below here needs editing.
REM ---------------------------------------------------------------------------

cd /d "%~dp0"
echo.
echo  ===============================================
echo   QuantFlow bridge launcher
echo  ===============================================
echo.

REM --- Python present? -------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo  [X] Python was not found on your PATH.
    echo      Install it from python.org and tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)
echo  [ok] Python found

REM --- Is the server file where we expect? -----------------------------------
if not exist "bridge\webhook_server.py" (
    echo  [X] Cannot find bridge\webhook_server.py
    echo      This file must sit in the SAME folder as the bridge folder.
    echo      Current folder: %CD%
    echo.
    pause
    exit /b 1
)
echo  [ok] Bridge found

REM --- Does the MT5 queue folder exist? --------------------------------------
REM  A wrong path here is the classic silent failure: the server happily
REM  queues orders into a folder MT5 never reads, and nothing appears to be
REM  wrong until you notice no trades are being placed.
for %%F in ("%QUEUE%") do set QUEUEDIR=%%~dpF
if not exist "%QUEUEDIR%" (
    echo  [!] WARNING: the MT5 Files folder does not exist:
    echo      %QUEUEDIR%
    echo      Orders will be written somewhere MT5 is not reading.
    echo      Fix the QUEUE setting at the top of this file.
    echo.
    pause
) else (
    echo  [ok] MT5 queue folder found
)

REM --- Launch MT5 ------------------------------------------------------------
if "%LAUNCH_MT5%"=="1" (
    if exist "%ProgramFiles%\MetaTrader 5\terminal64.exe" (
        echo  [ok] Starting MetaTrader 5
        start "" "%ProgramFiles%\MetaTrader 5\terminal64.exe"
    ) else (
        echo  [!] MetaTrader 5 not found in Program Files -- start it yourself.
    )
)

REM --- Open the chart in Brave ----------------------------------------------
set BRAVE=
if exist "%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe" set BRAVE=%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe
if exist "%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe" set BRAVE=%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe
if exist "%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe" set BRAVE=%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe

if defined BRAVE (
    echo  [ok] Opening the chart in Brave
    start "" "%BRAVE%" "%CHART%"
) else (
    echo  [!] Brave not found -- opening in your default browser instead.
    start "" "%CHART%"
)

REM --- Start the server ------------------------------------------------------
echo.
echo  Starting the bridge server in a new window.
echo  KEEP THAT WINDOW OPEN -- closing it stops the bridge.
echo.
echo  Reminder: the EA in MT5 has its own DryRun setting. The server running
echo  live does NOT mean orders reach your broker; the EA decides that.
echo.

start "QuantFlow Bridge Server" cmd /k python bridge\webhook_server.py --secret %SECRET% --symbols %SYMBOLS% --queue "%QUEUE%" --live

echo  Done. This window can be closed.
timeout /t 6 >nul
