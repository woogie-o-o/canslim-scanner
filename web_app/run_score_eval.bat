@echo off
REM Score-signal out-of-sample validation cache refresh (score_eval_US/KR.json)
REM %~dp0 = this .bat's own folder (web_app) -> avoids hardcoding the Korean path.
pushd "%~dp0"
set "PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python311-32\python.exe"
"%PY%" score_eval.py US --horizons 1,3,5,21,63 --save
"%PY%" score_eval.py KR --horizons 1,3,5,21,63 --save
popd
