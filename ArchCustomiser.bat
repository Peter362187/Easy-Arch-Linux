@echo off
rem ---------------------------------------------------------------------
rem  ArchCustomiser -- einfach doppelklicken.
rem
rem  Diese Datei sucht nur ein taugliches Python und uebergibt danach an
rem  tools\bootstrap.py. Alles Weitere -- Programmumgebung anlegen,
rem  Abhaengigkeiten installieren, pruefen, starten -- steht dort, einmal,
rem  gemeinsam mit archcustomiser.sh.
rem
rem  Vorher stand dieselbe Abfolge hier und im Shellskript getrennt. Die
rem  beiden liefen auseinander: das Shellskript reichte Argumente durch,
rem  diese Datei nicht -- die dokumentierten Aufrufe --check-env und
rem  --dry-run waren unter Windows also gar nicht erreichbar.
rem
rem  Wichtig bleibt: JEDER Fehlerweg endet mit "pause". Ohne das verschwindet
rem  das Fenster kommentarlos, und der haeufigste Anfaengerfehler bleibt
rem  unsichtbar.
rem ---------------------------------------------------------------------

setlocal

rem  pushd statt cd: cmd.exe kann einen UNC-Pfad (\\server\freigabe\...) nicht
rem  als aktuelles Verzeichnis fuehren und faellt kommentarlos auf C:\Windows
rem  zurueck -- alle relativen Pfade zeigten danach dorthin. pushd bildet den
rem  UNC-Pfad stattdessen auf einen freien Laufwerksbuchstaben ab.
pushd "%~dp0" 2>nul
if errorlevel 1 (
    echo.
    echo   Dieser Ordner laesst sich nicht als Arbeitsverzeichnis verwenden.
    echo   Bitte den Ordner auf eine lokale Festplatte kopieren.
    echo.
    pause
    exit /b 1
)

rem -- Python suchen: Version zaehlt, nicht die Reihenfolge --------------
rem  Frueher stand "py -3" fest, sobald der Launcher existierte -- auch wenn
rem  er auf ein zu altes Python zeigte, waehrend ein neueres "python" im PATH
rem  lag. Das Shellskript probierte dagegen alle Kandidaten durch.
set "PYTHON="
for %%K in ("py -3.14" "py -3.13" "py -3.12" "py -3.11" "py -3" "python") do (
    if not defined PYTHON (
        %%~K -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYTHON=%%~K"
    )
)

if not defined PYTHON (
    echo.
    echo   Es wurde kein Python 3.11 oder neuer gefunden.
    echo.
    echo   Die Download-Seite wird jetzt geoeffnet. Bitte bei der
    echo   Installation den Haken "Add Python to PATH" setzen und danach
    echo   diese Datei erneut doppelklicken.
    echo.
    start "" "https://www.python.org/downloads/windows/"
    pause
    exit /b 1
)

rem -- Einrichten und starten -------------------------------------------
rem  %* reicht Kommandozeilenargumente durch; ohne Argumente startet
rem  bootstrap.py die Oberflaeche ueber pythonw.exe, damit kein zweites
rem  schwarzes Fenster danebensteht.
%PYTHON% "tools\bootstrap.py" --fensterlos %*
if errorlevel 1 (
    echo.
    pause
    popd
    exit /b 1
)

popd
exit /b 0
