> **🇬🇧 English:** [README.en.md](README.en.md) — shorter overview; the full documentation is German.

# ArchCustomiser / Das Ultimative LARP Tool 

Wenn du zu faul oder zu blöd bist um dir Arch Linux selber einzurichten ist das eine wahre Goldgrube für dich.
Dieses Tool ermöglicht es dir eine Bootfähige Arch ISO Datei zu erstellen die du schön und einfach mit clicky bunti customisen kannst, wenn du fertig bist kannst du endlich jedem sagen das du Arch Linux verwendest obwohl du keinen Plan hast was du eigendlich tust.
Eine bessere und genauere Anleitung gibt es hier:

Grafischer Builder für eigene, auf Arch Linux basierende Live-ISOs.

Der Benutzer klickt sich durch einen Wizard — Desktop, Kernel, Programme,
Netzwerk, Audio, Sprache, Branding — und erhält am Ende eine bootfähige ISO.
Kenntnisse über `archiso`, `pacman` oder die interne ISO-Struktur sind nicht
nötig.

Das Programm baut Arch Linux **nicht nach**. Es ist eine Automatisierungsschicht
über der offiziellen Infrastruktur: `archiso`, `pacman`, die offiziellen
Repositories, `systemd` und `archinstall`. Die Abhängigkeitsauflösung macht
pacman, nicht dieses Programm.

---

## Stand der Umsetzung

| Phase | Inhalt | Status |
|-------|--------|--------|
| 1 | Projektstruktur, GUI-Gerüst, Umgebungserkennung, Logging | fertig |
| 2 | Katalog, Datenmodell, Resolver, generische Wizard-Seiten | fertig |
| 3 | Profile speichern, laden, migrieren | fertig |
| 4 | Paketvalidierung gegen die echten Arch-Repositories | fertig |
| 5 | archiso-Profil erzeugen | fertig |
| 6 | ISO-Build ausführen (`mkarchiso` starten) | fertig |
| 7 | Logging und Fehlerbehandlung | fertig |
| 8 | Branding | fertig |
| 9 | Tests | laufend (530) |
| 10 | UI/UX und Dokumentation | laufend |

**Der Funktionsumfang ist vollständig:** Wizard, Profile, Paketprüfung, Dry-Run,
Profilerzeugung und der ISO-Build mit Fortschrittsanzeige, Abbruch und Protokoll.

**Zwei echte ISOs wurden gebaut** (1311 MB und 2525 MB). Der vollständige Ablauf ist über die
Programmschnittstelle durchlaufen worden — Profil erzeugen, in eine
WSL-Arch-Verteilung übertragen, `mkarchiso` ausführen, ISO zurückholen:

```
Datei        : miniarch-1.0-x86_64.iso (1311 MB)
ISO-9660     : gültig (CD001-Signatur)
Datenträger  : MINIARCH_1_0
MBR-Signatur : 0x55AA vorhanden → BIOS-startfähig
Dauer        : rund 3 Minuten
```

**Was noch aussteht:** der Boot-Test von echter Hardware oder aus einer
virtuellen Maschine.

---

## Voraussetzungen

### Zum Bedienen (jede Plattform)

* Python 3.11 oder neuer
* PySide6, PyYAML, Jinja2

Konfiguration, Profile, Paketprüfung und Dry-Run funktionieren unter Windows,
macOS und Linux gleichermaßen.

### Systempakete für die Oberfläche (nur schlanke Linux-Systeme)

PySide6 bringt Qt mit, aber nicht dessen Systembibliotheken. Auf einem frisch
aufgesetzten Ubuntu, Debian oder Fedora bricht der Start sonst mit
`Could not load the Qt platform plugin "xcb"` ab. Einmalig:

```bash
sudo apt install libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libdbus-1-3 fontconfig
```

```bash
sudo dnf install mesa-libGL libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms
```

Unter Arch bringt `qt6-base` das ohnehin mit, unter Windows und macOS ist
nichts nötig.

---

## Wie die ISO gebaut wird

`archiso` gibt es **nur unter Arch Linux** — Debian, Ubuntu, Fedora und
openSUSE paketieren es nicht. Das Programm löst das nicht, indem es das
Zielsystem arch-fähig macht, sondern indem es ein Arch *danebenstellt*. Es
erkennt selbst, welcher Weg auf diesem Rechner möglich ist:

| System | Weg | Einmalig einzurichten |
|---|---|---|
| Arch Linux | direkt | `sudo pacman -S --needed archiso` |
| Windows | WSL-Verteilung | `wsl --install archlinux`, Neustart, dann `pacman -S archiso` |
| Ubuntu, Fedora, Debian, Mint | Container | `sudo apt install podman` (Fedora hat es schon) |
| macOS | Container | Docker Desktop |
| sonst | Profil exportieren | nichts — auf einem Arch-System bauen |

**Du wählst nichts.** Das Programm prüft beim Klick auf „ISO erstellen", was da
ist, nimmt den besten Weg und sagt in einem Satz, welchen. Geht keiner, bietet
es den Profil-Export an statt eines ausgegrauten Knopfes.

**Zum Container, offen gesagt:** er läuft mit `--privileged`. Nicht wegen
`mkarchiso`, sondern wegen `pacstrap` — das hängt acht Dateisysteme in den
Zielbaum ein und braucht dafür `CAP_SYS_ADMIN`. Ein Weg ohne diese Rechte
existiert nicht: `devtmpfs` lässt sich in einem User-Namespace grundsätzlich
nicht einhängen. Arch baut seine eigenen ISOs genauso.

### Ein Bau bekommt nie den ganzen Rechner

Das ist eine Zusicherung, keine Einstellung. Der Bau nimmt sich **die Hälfte
der Kerne**, der Rest bleibt für die Bedienung frei. Vor dem Start steht die
Zahl in der Vorabprüfung:

```
[ok  ] Rechenlast: 6 von 12 Kernen -- der Rest bleibt fuer die Bedienung des Rechners frei
```

Der Grund ist ein echter Vorfall vom 03.09.2026: ein Bau auf einem Rechner mit
zwölf Kernen hat Windows vollständig zum Stillstand gebracht — kein Fenster
ließ sich mehr verschieben, der Abbrechen-Knopf war nicht mehr erreichbar, nur
ein harter Neustart half. Die letzte Zeile im Protokoll lautete
`Parallel mksquashfs: Using 12 processors`.

Ohne Vorgabe startet `mksquashfs` einen Kompressionsfaden je sichtbarem Kern.
Unter WSL2 sind das ohne `.wslconfig` **alle** Kerne des Wirts — für den
Fensterverwalter von Windows bleibt dann keine Rechenzeit mehr. Das Programm
bindet den Bau deshalb selbst an eine Teilmenge der Kerne (`taskset` bzw.
`--cpus` beim Container), auf allen drei Bauwegen gleichermaßen. Eine
`.wslconfig` musst du dafür **nicht** anlegen.

Ein Bau bleibt trotzdem spürbar: der Rechner wird wärmer und langsamer. Aber er
bleibt bedienbar, und **Abbrechen wirkt sofort** — die Abbruchkette läuft seit
demselben Tag neben der Oberfläche statt in ihr.

### Zum Bauen einer ISO unter Windows

Einmalig, in der PowerShell **als Administrator**:

```bash
wsl --install archlinux
```

Nach dem Neustart in Arch:

```bash
sudo pacman -Syu --needed archiso
```

Das Programm erkennt das selbst und **führt beim ersten „ISO erstellen"
durch diese Schritte** — es zeigt sie der Reihe nach an, jeweils mit einem
Knopf zum Kopieren. Ausgeführt werden sie nicht automatisch: `wsl --install`
braucht Administratorrechte und einen Neustart. Platzbedarf: WSL legt seine virtuelle Platte auf `C:` ab, für
ein Desktop-Abbild mit Spielen werden dort 25–40 GB gebraucht.

### Zum Bauen einer ISO (direkt auf Arch Linux)

```bash
sudo pacman -S --needed archiso arch-install-scripts squashfs-tools libisoburn dosfstools mtools pacman gzip libarchive awk openssl
```

Das Programm prüft das beim Start selbst und nennt fehlende Pakete samt
Installationsbefehl. Prüfen lässt sich das auch ohne GUI:

```bash
python -m archcustomiser --check-env
```

**Root wird nicht benötigt.** Seit archiso 89 kapselt `mkarchiso` die
privilegierten Schritte in `unshare --map-auto --map-root-user`. Dafür braucht
der aufrufende Benutzer allerdings Sub-ID-Bereiche:

```bash
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $USER
```

Fehlen sie, weicht das Programm auf `pkexec` aus und sagt das auch.

**Plattenplatz:** Das Arbeitsverzeichnis braucht für ein Desktop-Image mit
Spielen realistisch 25–40 GB und muss auf einem Linux-Dateisystem liegen.

---

## Installation

### Windows — der einfache Weg

1. Auf der Projektseite auf **Code → Download ZIP** klicken und den Ordner
   entpacken. Wer git nutzt, klont stattdessen:

   ```bash
   git clone https://github.com/Peter362187/Easy-Arch-Linux.git
   ```

2. Im entpackten Ordner **`ArchCustomiser.bat` doppelklicken.**

Das war alles. Beim ersten Mal richtet die Datei die Programmumgebung selbst
ein — das dauert ein paar Minuten und lädt rund 670 MB. Danach startet ein
Doppelklick das Programm sofort.

Fehlt Python, sagt sie das und öffnet die Download-Seite. Wichtig ist dort der
Haken **„Add Python to PATH"**. Gebraucht wird Python 3.11 oder neuer.

#### Wenn beim Doppelklick nichts passiert

Sollte das Fenster kommentarlos verschwinden, hilft ein Blick ins Protokoll:

```
%LOCALAPPDATA%\ArchCustomiser\State\archcustomiser.log
```

Diesen Pfad in den Explorer einfügen. Seit dieser Fassung zeigt das Programm
Abstürze zusätzlich als Fenster an, statt still zu enden.

Führt nichts zum Ziel: den Ordner `.venv` löschen und `ArchCustomiser.bat`
erneut doppelklicken. Damit wird die Einrichtung von vorn gemacht.

### Linux und macOS — der einfache Weg

```bash
git clone https://github.com/Peter362187/Easy-Arch-Linux.git && cd Easy-Arch-Linux
```

```bash
./archcustomiser.sh
```

Meldet die Shell `Permission denied`, ist beim Herunterladen das
Ausführbar-Recht verlorengegangen. Dann entweder `chmod +x archcustomiser.sh`
oder einfach `sh archcustomiser.sh` — das geht immer.

Das ist das Gegenstück zu `ArchCustomiser.bat` und tut dasselbe: beim ersten
Mal richtet es die Programmumgebung selbst ein, danach startet es nur noch.
Scheitert etwas, sagt es in einem Satz, woran — und nennt den Befehl, der es
behebt, passend zum jeweiligen Paketverwalter (apt, dnf, zypper, pacman, brew).

Von Hand geht es weiterhin:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/python -m archcustomiser
```

Alternativ ohne Quellordner, direkt aus dem Repository:

```bash
pipx install git+https://github.com/Peter362187/Easy-Arch-Linux.git
```

Danach startet `archcustomiser` die Oberfläche.

---

## Verwendung

Unter Windows: `ArchCustomiser.bat` doppelklicken.
Unter Linux und macOS: `./archcustomiser.sh` ausführen.

Sonst, mit aktivierter Programmumgebung:

```bash
python -m archcustomiser
```

Ohne Aktivierung geht es direkt über den Pfad — `.venv/bin/python` unter
Linux und macOS, `.venv\Scripts\python.exe` unter Windows.

Weitere Aufrufformen für die Kommandozeile:

```bash
python -m archcustomiser --check-env
```

```bash
python -m archcustomiser --dry-run src/archcustomiser/profiles/gaming.yaml
```

```bash
python -m archcustomiser --export-profile src/archcustomiser/profiles/gaming.yaml --out ~/flos-profil.tar.gz
```

Der Wizard führt durch eine Startseite und vierzehn Schritte. Seiten, die nicht zutreffen, werden
übersprungen — ohne Desktop und ohne Window Manager erscheint zum Beispiel die
Treiberseite gar nicht erst.

---

## Vom Wizard zur ISO

**Auf einem Arch-System** genügt „ISO erstellen". Davor erscheint eine
Vorabprüfung — Werkzeuge, Rechte, Plattenplatz, Dateisystem — die alle Befunde
auf einmal zeigt, statt nach jeder Korrektur den nächsten Fehler. Danach läuft
der Build mit Fortschrittsbalken, abgehakter Schrittliste und mitlaufendem
Protokoll. Abbrechen ist jederzeit möglich.

Der Fortschritt kommt nicht aus einer Schätzung: `mkarchiso` meldet jeden
Abschnitt, und innerhalb der beiden langen Phasen zählt pacman die Pakete
während `mksquashfs` und `xorriso` Prozente melden.

**Auf Windows** baut das Programm über WSL — ein Linux innerhalb von Windows,
kein zweiter Rechner und kein Dual-Boot. Beim ersten Mal führt ein Dialog durch
die Einrichtung (zwei Befehle zum Kopieren); danach genügt ebenfalls „ISO
erstellen". Das Profil wandert als Archiv hinüber, wird dort ausgepackt und
gebaut, und die fertige ISO landet wieder in deinem Windows-Ordner.

Warum der Umweg über ein Archiv: Unter `/mnt/c` liegt ein Windows-Dateisystem
ohne symbolische Verknüpfungen — und ein archiso-Profil besteht zu einem
Drittel daraus. Direkt dorthin geschrieben bräche der Build ab.

**Auf jedem anderen Linux und auf macOS** baut das Programm in einem
Container mit dem `archlinux`-Abbild — derselbe Gedanke wie bei WSL, nur
heißt der Nachbar hier podman oder docker. Der volle Funktionsumfang
bleibt erhalten, weil pacman im Container läuft.

**Geht keiner dieser Wege**, bietet das Programm den Profilexport an — als
`.tar.gz` oder als Ordner.

Danach auf einem Arch-System:

```bash
tar xzf flos-profil.tar.gz && cd flos-profil && mkarchiso -v -w ../work -o ../out .
```

Ergebnis: `../out/flos-1.0-x86_64.iso`.

Zwei Dinge, die dabei überraschen können:

**Das Archiv muss auf dem Linux-System entpackt werden.** Unter Windows bricht
`tar` ab, weil ein archiso-Profil Verknüpfungen auf Pfade wie
`/usr/lib/systemd/system/sddm.service` enthält, die es dort nicht gibt. Das
offizielle archiso-Repository verhält sich exakt genauso — es ist keine
Eigenart dieses Programms.

**Root wird nicht gebraucht.** Seit archiso 89 kapselt `mkarchiso` die
privilegierten Schritte selbst; nötig sind nur Sub-ID-Bereiche (siehe
Voraussetzungen).

Die Registerkarte **„Profildateien"** in der Zusammenfassung zeigt den
vollständigen Baum, bevor eine einzige Datei entsteht — mit Pfad, Größe und der
Katalogoption, die jede Datei beigesteuert hat.

### Was das Programm automatisch ergänzt

Der Katalog enthält nur, was zur Auswahl steht. Zum Booten fehlen dann Pakete,
die keine Wahlmöglichkeit sind: `base`, `mkinitcpio`, `mkinitcpio-archiso`, und
je nach Bootmodus `syslinux`. Diese ergänzt der Generator — jedes mit einer
Begründung, die in der Paketliste und im Dry-Run steht.

Ebenso wird `[multilib]` in der erzeugten `pacman.conf` aktiviert, sobald Steam
oder ein anderes 32-Bit-Paket gewählt ist. In der archiso-Vorlage ist der
Abschnitt auskommentiert; ohne diesen Schritt bräche der Build an einem nicht
gefundenen Paket ab.

---

## Profile

Ein Profil hält die Auswahl fest und lässt sich jederzeit wieder laden.
Mitgeliefert werden `minimal`, `desktop`, `gaming` und `development` im
Verzeichnis `src/archcustomiser/profiles/`. Eigene Profile landen unter
`~/.config/archcustomiser/profiles/`.

```yaml
schema_version: 1
catalog_version: "2026.09.01"
name: Gaming

selections:
  desktop: [kde]
  kernel: [linux-zen]
  audio: [pipewire]
  apps: [firefox, steam, lutris, gamescope]
  services: [bluetooth]

fields:
  basics.hostname: arch-gaming
  basics.locale: de_DE.UTF-8
  branding.distro_name: CustomArch Gaming
```

Drei Eigenschaften, die im Alltag zählen:

**Automatische Ergänzungen werden nicht gespeichert.** KDE zieht SDDM nach sich,
aber im Profil steht nur KDE. Bringt eine spätere Katalogversion einen anderen
Login-Screen mit, übernimmt ein altes Profil ihn automatisch — statt eine
Entscheidung von 2026 festzuschreiben.

**Passwörter stehen nie drin.** Sie liegen ausschließlich im Speicher und
erreichen die Konfigurationsstruktur gar nicht erst. Steht in einer von Hand
bearbeiteten Datei doch eines, wird es beim Laden verworfen und gemeldet.

**Unbekannte Einträge gehen nicht verloren.** Ein Profil, das mit einer
Katalogerweiterung erstellt wurde, behält seine Einträge auch dann, wenn es auf
einem Rechner ohne diese Erweiterung geöffnet und gespeichert wird.

---

## Eigene Pakete

Auf der Seite „Zusätzliche Pakete" lässt sich frei eingeben. Jede Zeile wird
sofort gegen die echten Repositories geprüft und eingeordnet:

| Anzeige | Bedeutung |
|---|---|
| `extra/neovim 0.12.5-1, 31 MB` | gefunden |
| `Paketgruppe mit 70 Paketen` | eine Gruppe wie `plasma` |
| `wird bereitgestellt von noto-fonts` | virtuelles Paket mit einem Anbieter |
| `11 Anbieter — bitte einen wählen` | mehrdeutig, Auswahl nötig |
| `nicht gefunden. Meinten Sie: neovim?` | Tippfehler mit Vorschlag |
| `nicht prüfbar` | keine Paketdaten verfügbar |

Zur letzten Zeile: **Solange keine vollständigen Paketdaten vorliegen, behauptet
das Programm nie, ein Paket existiere nicht.** Andernfalls würde ein
Netzwerkausfall dazu führen, dass jemand einen völlig korrekten Paketnamen
löscht, weil das Programm ihn als Tippfehler ausgibt.

Gruppen werden bewusst **nicht** in Einzelpakete aufgelöst, bevor sie in die
Paketliste wandern. `packages.x86_64` akzeptiert Gruppennamen, und pacstrap löst
sie zur Bauzeit auf dem dann aktuellen Stand auf. Eine hier eingefrorene
Mitgliederliste wäre beim nächsten Repo-Update bereits veraltet.

---

## Eigenes Branding

Einstellbar sind Distributionsname, Version, Logo, Hintergrundbild und
Boot-Splash. Daraus ergeben sich automatisch:

* ISO-Dateiname — `FLOS` + `1.0` → `flos-1.0-x86_64.iso`
* Datenträgerbezeichnung — `FLOS_1_0` (ISO-9660: nur `A-Z0-9_`, max. 32 Zeichen)
* Verzeichnis auf der ISO — `flos` (mkarchiso erlaubt nur `[a-z0-9]`, max. 30)
* `/etc/os-release`, Bootmenü, Login-Screen, Desktop-Hintergrund

### Zur Herkunftsangabe

Das System bleibt erkennbar arch-basiert: `/etc/os-release` bekommt
`ID_LIKE=arch` und `PRETTY_NAME="FLOS 1.0 (based on Arch Linux)"`. Das ist der
in `os-release(5)` vorgesehene, maschinenlesbare Herkunftsnachweis.

Namen, die sich als Arch Linux selbst ausgeben, werden abgelehnt. Namen, die mit
„Arch" beginnen, lösen eine Warnung aus: die Arch-Markenrichtlinie (Fassung
2021-04-18) stuft solche Namen als verwechselbar ein. Ausdrücklich erlaubt ist
dagegen der Zusatz „based on Arch Linux".

---

## Fehlerbehebung

**„Direkt auf diesem System kann nicht gebaut werden"**
Kein Fehler, sondern eine Einordnung: dieser Rechner ist kein Arch-System.
Das Programm sucht sich dann selbst einen Weg — eine WSL-Verteilung unter
Windows, sonst einen Container. Findet es keinen, sagt es, was fehlt und
was dagegen hilft, und bietet den Profilexport an.

**„Die Paketdaten sind X Tage alt"**
Auf der Paketseite „Paketdaten aktualisieren" anklicken. Die Anzeige nennt den
Stand der Repositories, nicht den Zeitpunkt der letzten Abfrage.

**„Keine Verbindung zu den Arch-Paketservern"**
Der Wizard bleibt vollständig bedienbar; Paketnamen werden dann als „nicht
prüfbar" geführt. Falsch geschriebene Namen fallen in diesem Fall erst beim
Build auf.

**„Es fehlen benötigte Werkzeuge"**
`python -m archcustomiser --check-env` nennt jedes fehlende Paket und einen
fertigen Installationsbefehl.

**„Für einen Build ohne Root-Rechte werden Sub-ID-Bereiche benötigt"**
Siehe `usermod`-Befehl unter Voraussetzungen. Alternativ läuft der Build über
`pkexec`.

**Ein Paket wird als „mehrdeutig" gemeldet**
Der Name ist ein virtuelles Paket mit mehreren Anbietern. pacman würde hier
nachfragen — `mkarchiso` läuft aber ohne Rückfrage und würde abbrechen. Deshalb
muss der Anbieter vorher feststehen.

**Logdatei** — je System dort, wo man sie erwartet:

| System | Pfad |
|---|---|
| Linux | `~/.local/state/archcustomiser/archcustomiser.log` |
| Windows | `%LOCALAPPDATA%\ArchCustomiser\State\archcustomiser.log` |
| macOS | `~/Library/Logs/ArchCustomiser/archcustomiser.log` |

Auf macOS bewusst nicht `~/.local/state`: Punkt-Verzeichnisse sind im Finder
unsichtbar, und wer sein Protokoll an eine Fehlermeldung hängen soll, findet es
dort nicht.

Passwörter und Passwort-Hashes werden in der Datei maskiert.

---

## Architektur

```
src/archcustomiser/
├── core/                    komplett ohne Qt -- ohne Bildschirm testbar
│   ├── catalog/             YAML-Modell, Lader, Prädikate
│   ├── packages/            Paketvalidierung (siehe unten)
│   ├── config.py            BuildConfig -- die Auswahl des Benutzers
│   ├── resolver.py          Auflösung zu Paketen, Diensten, Dateien
│   ├── profiles.py          Speichern, Laden, Migrieren
│   ├── archiso/             Profilerzeugung: Baum, Ausgabewege, Bootloader
│   ├── build/               ISO-Bau: drei Bauwege (lokal, WSL, Container)
│   ├── plan.py              Bauplan und archinstall-Konfiguration
│   ├── validation.py        Feldprüfungen
│   ├── environment.py       Werkzeug- und Rechteerkennung
│   ├── secrets.py           Passwortbehandlung
│   └── logging_setup.py     Logging mit Maskierung
├── gui/                     PySide6
│   ├── store.py             einzige Stelle, die Konfiguration verändert
│   ├── wizard.py            QWizard, Seitenreihenfolge, Profile
│   └── pages/               vier generische Seitentypen
├── data/catalog/            der gesamte Optionsumfang als YAML
└── profiles/                mitgelieferte Profile

tests/                       530 Tests, ohne Netz und ohne Bildschirm
tools/                       Hilfsskripte für die Entwicklung
ArchCustomiser.bat           Doppelklick-Start, richtet sich selbst ein
```

Katalog und Profile liegen **innerhalb** des Pakets. Damit landen sie in einem
Wheel und die Anwendung funktioniert auch ohne Quellordner — vorher fand sie
ihren Katalog nur, wenn sie mit `pip install -e` an Ort und Stelle installiert
war.

Ausführlicher in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

### Der Katalog ist das Programm

Die Oberfläche kennt keine einzige Desktop-Umgebung namentlich. Es gibt vier
Seitentypen (`selection`, `form`, `free_packages`, `summary`); alles andere ist
YAML. Eine neue Desktop-Umgebung, ein neuer Kernel, ein neues Eingabefeld oder
eine komplette neue Kategorie ist ein Eintrag in `src/archcustomiser/data/catalog/` — ohne eine
Zeile Python.

---

## Tests

```bash
.venv/bin/python -m pytest -q
```

Die Testsuite läuft ohne Netzwerkzugriff und ohne Bildschirm. Der ALPM-Parser
wird gegen **echte** `tar.gz`-Archive geprüft, nicht gegen Attrappen — so fällt
eine Formatänderung bei pacman auf.

```bash
.venv/bin/python -m pytest -m network
```

Zusätzliche Tests gegen die echten Arch-Server; standardmäßig abgewählt.

---

## Lizenz und Verhältnis zu Arch Linux

Dieses Projekt ist nicht mit dem Arch-Linux-Projekt verbunden und wird nicht von
ihm unterstützt. „Arch Linux" ist eine Marke des Arch-Linux-Projekts.

---

## Was geprüft ist und was nicht

Diese Trennung gehört in die Dokumentation, nicht ins Kleingedruckte:

| | Zustand |
|---|---|
| Oberfläche unter Windows | im Gebrauch |
| ISO-Bau über WSL | **zwei echte ISOs gebaut**, 1311 MB und 2525 MB |
| Kerngrenze beim Bau | **auf echter Hardware gemessen**: mksquashfs meldet 6 statt 12 Fäden |
| Profil-Erzeugung und Export | durch Tests und im Gebrauch belegt |
| ISO-Bau direkt auf Arch | durch Tests belegt, nicht auf echter Hardware gelaufen |
| **ISO-Bau im Container** | **Aufrufe durch Tests belegt, aber noch auf keinem echten Ubuntu, Fedora oder Mac gelaufen** |
| Boot-Test der fertigen ISO | steht aus |

Der Container-Weg ist sorgfältig gebaut und folgt dem, was Arch für seine
eigenen ISOs tut — aber „sollte funktionieren" ist nicht dasselbe wie
„funktioniert". Wer ihn auf einem echten System ausprobiert, möge berichten.
