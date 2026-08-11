@echo off
cd /d "%~dp0"
python run.py "macd" --data yahoo --symbol QQQ --walkforward 4 --montecarlo 1000
pause
