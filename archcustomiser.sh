#!/bin/sh
# ---------------------------------------------------------------------
#  ArchCustomiser -- das Gegenstueck zu ArchCustomiser.bat fuer Linux
#  und macOS.
#
#      ./archcustomiser.sh
#
#  Dieses Skript sucht nur ein taugliches Python und uebergibt danach an
#  tools/bootstrap.py. Alles Weitere -- Programmumgebung anlegen,
#  Abhaengigkeiten installieren, pruefen, starten -- steht dort, einmal,
#  gemeinsam mit ArchCustomiser.bat.
#
#  Bewusst POSIX-sh und nicht bash: auf einem frisch aufgesetzten Debian
#  ohne bash-Erweiterungen und auf macOS mit seiner alten bash-Fassung
#  laeuft das hier unveraendert.
# ---------------------------------------------------------------------

set -eu

# Der Ordner des Skripts, egal von wo aus es gerufen wurde.
cd "$(dirname "$0")" || {
    echo "Der Ordner dieses Skripts liess sich nicht bestimmen." >&2
    exit 1
}

rot()  { printf '\033[31m%s\033[0m\n' "$*"; }
fett() { printf '\033[1m%s\033[0m\n' "$*"; }

# -- Welcher Paketverwalter liegt hier? -------------------------------
#  Ein fester Befehl waere hier derselbe Fehler wie ein "sudo pacman -S"
#  auf Ubuntu: ein Rat, den man nicht befolgen kann.
paketbefehl() {
    if [ "$(uname -s)" = "Darwin" ]; then
        echo "brew install python@3.12"
    elif command -v apt >/dev/null 2>&1; then
        echo "sudo apt install python3 python3-venv"
    elif command -v dnf >/dev/null 2>&1; then
        echo "sudo dnf install python3"
    elif command -v zypper >/dev/null 2>&1; then
        echo "sudo zypper install python3"
    elif command -v pacman >/dev/null 2>&1; then
        echo "sudo pacman -S --needed python"
    else
        echo "Python 3.11 oder neuer installieren"
    fi
}

qt_hinweis() {
    if [ "$(uname -s)" = "Darwin" ]; then
        return
    fi
    echo
    fett "  Falls oben von \"xcb\" oder \"platform plugin\" die Rede ist:"
    echo
    echo "  PySide6 bringt Qt mit, aber nicht dessen Systembibliotheken."
    echo "  Einmalig nachinstallieren:"
    echo
    if command -v apt >/dev/null 2>&1; then
        echo "    sudo apt install libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 \\"
        echo "                     libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 \\"
        echo "                     libdbus-1-3 fontconfig"
    elif command -v dnf >/dev/null 2>&1; then
        echo "    sudo dnf install mesa-libGL libxkbcommon-x11 xcb-util-cursor \\"
        echo "                     xcb-util-wm xcb-util-keysyms"
    elif command -v pacman >/dev/null 2>&1; then
        echo "    sudo pacman -S --needed qt6-base"
    else
        echo "    Die Qt-Systembibliotheken des jeweiligen Systems (libGL, xcb-cursor)."
    fi
    echo
}

# -- 1. Python finden -------------------------------------------------
PYTHON=""
for kandidat in python3 python; do
    command -v "$kandidat" >/dev/null 2>&1 || continue
    if "$kandidat" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$kandidat"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo
    rot "  Es wurde kein Python 3.11 oder neuer gefunden."
    echo
    if command -v python3 >/dev/null 2>&1; then
        echo "  Gefunden wurde: $(python3 --version 2>&1)"
        echo "  Gebraucht wird 3.11 oder neuer."
    fi
    echo
    echo "  Installieren mit:"
    echo
    echo "    $(paketbefehl)"
    echo
    exit 1
fi

# -- 2. Einrichten und starten ----------------------------------------
#  ${1+"$@"} statt "$@": bash-Fassungen vor 4.4 -- und /bin/sh auf macOS ist
#  bash 3.2 -- behandeln "$@" bei leerer Argumentliste unter 'set -u' als
#  unbound variable. Der Start ohne Argumente, also der Normalfall, brach
#  dort ab.
#
#  Der Rueckgabecode wird mit "|| code=$?" eingefangen und nicht ueber ein
#  "if ...; then" -- nach einem fehlgeschlagenen if ist $? bereits das
#  Ergebnis des if selbst, also 0. Der echte Code waere verloren, und die
#  Meldung lautete "Fehlercode 0". Genau das ist beim Testen passiert.
code=0
"$PYTHON" tools/bootstrap.py ${1+"$@"} || code=$?

if [ "$code" -eq 0 ]; then
    exit 0
fi

echo
rot "  Das Programm wurde mit Fehlercode $code beendet."
qt_hinweis
exit "$code"
