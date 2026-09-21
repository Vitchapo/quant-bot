@echo off
REM ===================================================================
REM  Preparation FTMO - quantbot          (double-cliquable)
REM
REM  Ce fichier ne fait que TROUVER un Python 64 bits et lui passer la
REM  main. Tout le travail reel - detection du terminal, ecriture de la
REM  config, verification de la connexion - est dans
REM  scripts\preparer_ftmo.py, parce que le batch ne sait ni editer du
REM  YAML proprement ni parler a MetaTrader 5.
REM
REM  Il ne declare JAMAIS que tout est pret : seul le script Python le
REM  fait, et seulement apres que le terminal a repondu.
REM ===================================================================
setlocal
pushd "%~dp0"

echo.
echo   Preparation FTMO - quantbot
echo   ===========================
echo.

set "PY="
if exist "C:\Program Files\Python38\python.exe" set "PY=C:\Program Files\Python38\python.exe"
if not defined PY if exist "C:\Program Files\Python312\python.exe" set "PY=C:\Program Files\Python312\python.exe"
if not defined PY if exist "C:\Program Files\Python311\python.exe" set "PY=C:\Program Files\Python311\python.exe"
if not defined PY (where py >nul 2>&1 && set "PY=py")
if not defined PY (where python >nul 2>&1 && set "PY=python")

if not defined PY (
  echo   ARRET : aucun interpreteur Python trouve.
  echo.
   echo   Installe Python 3.12 en 64 bits depuis python.org, en cochant
   echo   "Add python.exe to PATH", puis relance ce fichier.
  goto :fin
)

echo   interpreteur : %PY%

REM -- Un Python 32 bits ne peut PAS charger MetaTrader5. Autant le dire
REM -- ici plutot que de laisser un ImportError obscur sortir plus tard.
"%PY%" -c "import struct,sys; sys.exit(0 if struct.calcsize('P')*8==64 else 1)"
if errorlevel 1 (
  echo.
  echo   ARRET : cet interpreteur est en 32 bits.
  echo   La librairie MetaTrader5 exige un Python 64 bits, sans
  echo   contournement possible. Reinstalle Python en 64 bits.
  goto :fin
)

"%PY%" "scripts\preparer_ftmo.py" %*
set "CODE=%errorlevel%"
echo.
if "%CODE%"=="0" (echo   Termine sans erreur.) else (echo   Code %CODE% : rien n a ete declare pret.)

:fin
echo.
popd
pause
endlocal
