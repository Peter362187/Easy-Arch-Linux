# Projekt: Custom Arch Linux Builder

Ich möchte ein professionelles Programm entwickeln, mit dem Benutzer **individuelle Arch-Linux-ISOs automatisch erstellen können**, ohne selbst `archiso`, Paketlisten oder Konfigurationsdateien manuell bearbeiten zu müssen.

Das Programm soll als grafischer **Arch Linux ISO Builder** funktionieren.

## 1. Ziel des Projekts

Der Benutzer soll in einer übersichtlichen GUI auswählen können, wie sein eigenes Arch-Linux-System aussehen soll.

Beispiel:

* Desktop-Umgebung auswählen
* Window Manager auswählen
* Kernel auswählen
* Browser auswählen
* Office-Software auswählen
* Audio-System auswählen
* Netzwerk-Manager auswählen
* Gaming-Pakete auswählen
* Entwicklerwerkzeuge auswählen
* zusätzliche Pakete auswählen
* Services auswählen
* Sprache und Tastatur auswählen
* Zeitzone auswählen
* eigenes Branding auswählen

Danach soll das Programm automatisch alle notwendigen Konfigurationen erzeugen und mit `archiso` eine **bootfähige ISO-Datei** erstellen.

Der Benutzer soll am Ende beispielsweise erhalten:

```text
CustomArch.iso
```

Diese ISO soll mit einem normalen USB-Stick bootbar sein.

---

# 2. Wichtiges Grundprinzip

Das Projekt soll **nicht versuchen, Arch Linux selbst nachzubauen**.

Stattdessen soll die offizielle Arch-Linux-Infrastruktur verwendet werden, insbesondere:

* archiso
* pacman
* offizielle Arch-Repositories
* AUR nur optional
* systemd
* offizielle Arch-Paketquellen

Das Programm ist eine Automatisierungsschicht über diesen Komponenten.

Die Architektur soll so gestaltet sein, dass Arch Linux möglichst wenig verändert wird.

---

# 3. GUI

Erstelle eine moderne, übersichtliche grafische Benutzeroberfläche.

Die GUI soll einen Wizard besitzen.

### Schritt 1 – Grundkonfiguration

Optionen:

```text
Hostname
Locale
Tastatur
Zeitzone
```

Beispiele:

```text
Locale:
[ de_DE.UTF-8 ]

Keyboard:
[ de-latin1 ]

Timezone:
[ Europe/Berlin ]
```

---

### Schritt 2 – Desktop

Der Benutzer kann auswählen:

```text
○ Kein Desktop
○ KDE Plasma
○ GNOME
○ XFCE
○ Cinnamon
○ LXQt
```

Zusätzlich:

```text
○ Hyprland
○ i3
○ Sway
```

Desktop und Window Manager sollen logisch getrennt behandelt werden.

---

### Schritt 3 – Kernel

Auswahl:

```text
○ linux
○ linux-lts
○ linux-zen
```

Die Architektur soll später problemlos um weitere Kernel erweitert werden können.

---

### Schritt 4 – Netzwerk

Auswahl:

```text
☑ NetworkManager
☐ systemd-networkd
```

Wenn NetworkManager ausgewählt wurde, soll der entsprechende Service automatisch aktiviert werden.

---

### Schritt 5 – Audio

Auswahl:

```text
○ PipeWire
○ PulseAudio
○ Keine Audio-Unterstützung
```

PipeWire soll bevorzugt verwendet werden.

---

### Schritt 6 – Programme

Kategorien:

### Browser

```text
☐ Firefox
☐ Chromium
```

### Office

```text
☐ LibreOffice
```

### Gaming

```text
☐ Steam
☐ Lutris
☐ Gamescope
```

### Entwicklung

```text
☐ Git
☐ Python
☐ Node.js
☐ Docker
☐ GCC
☐ CMake
```

### Sonstiges

```text
☐ VLC
☐ Neofetch/Alternative
☐ btop
☐ fastfetch
```

Die Paketliste darf NICHT hart in der GUI codiert werden.

Verwende stattdessen eine strukturierte Konfigurationsdatei, damit Pakete später leicht ergänzt werden können.

---

# 4. Eigene Pakete

Der Benutzer soll zusätzliche Pakete manuell hinzufügen können.

Beispiel:

```text
Zusätzliche Pakete:

[ neovim ]
[ htop ]
[ wget ]
[ curl ]
```

Das Programm soll die Eingaben validieren.

Ungültige oder nicht existierende Pakete sollen vor dem ISO-Build erkannt und verständlich gemeldet werden.

---

# 5. Services

Der Benutzer soll Services auswählen können.

Beispielsweise:

```text
☑ NetworkManager
☑ bluetooth
☐ sshd
☐ docker
```

Das Programm soll daraus automatisch die notwendigen systemd-Konfigurationen erstellen.

---

# 6. Benutzerkonfiguration

Optional soll der Benutzer einen Standardbenutzer festlegen können:

```text
Username:
[ jason ]

Full Name:
[ Jason ]

Password:
[ ******** ]
```

Wichtig:

Passwörter dürfen niemals im Klartext in Logs ausgegeben werden.

Die Anwendung soll sensible Daten möglichst nicht dauerhaft speichern.

---

# 7. Branding

Ich möchte die Möglichkeit haben, ein eigenes Branding einzubauen.

Beispielsweise:

```text
Distribution Name:
[ FLOS ]

Version:
[ 1.0 ]

Logo:
[ logo.png ]
```

Das Branding soll unter anderem anpassbar sein für:

* ISO-Name
* Boot-Menü
* Desktop-Hintergrund
* Login-/Display-Manager
* Systeminformationen
* `/etc/os-release`

Das System soll dabei weiterhin klar auf Arch Linux basieren und keine falschen Angaben über die Herkunft des Systems machen.

---

# 8. Konfigurationsprofile

Das Programm soll Profile unterstützen.

Beispiel:

```text
profiles/
├── minimal.yaml
├── desktop.yaml
├── gaming.yaml
├── development.yaml
└── custom.yaml
```

Ein Profil könnte beispielsweise enthalten:

```yaml
name: Gaming

desktop: kde
kernel: linux

packages:
  - firefox
  - steam
  - lutris
  - gamescope

services:
  - NetworkManager
```

Der Benutzer soll Profile speichern und später wieder laden können.

---

# 9. ISO-Build

Wenn der Benutzer auf

```text
ISO ERSTELLEN
```

klickt, soll ein Build-Prozess gestartet werden.

Die GUI soll den Fortschritt anzeigen.

Beispielsweise:

```text
[██████████████░░░░░░] 72%

Installing packages...
```

Zusätzlich sollen verständliche Statusmeldungen angezeigt werden:

```text
✓ Configuration generated
✓ Archiso profile created
✓ Packages validated
✓ Base system prepared
→ Installing packages...
→ Creating ISO...
```

Bei Fehlern soll die GUI eine verständliche Fehlermeldung anzeigen und zusätzlich einen ausführlichen Log bereitstellen.

---

# 10. Architektur

Strukturiere das Projekt sauber.

Empfohlene Struktur:

```text
custom-arch-builder/
│
├── src/
│   ├── gui/
│   ├── builder/
│   ├── archiso/
│   ├── packages/
│   ├── profiles/
│   ├── configuration/
│   ├── validation/
│   └── utils/
│
├── profiles/
│
├── assets/
│   ├── icons/
│   └── branding/
│
├── tests/
│
├── docs/
│
├── README.md
└── ...
```

Die genaue Struktur darf verbessert werden, wenn du eine bessere Architektur erkennst.

Wichtig ist eine klare Trennung zwischen:

* GUI
* Konfiguration
* Paketverwaltung
* ISO-Build
* Validierung
* Logging

---

# 11. Betriebssystem

Das Programm soll zunächst **unter Linux** funktionieren.

Zielplattform:

```text
Arch Linux
```

Später soll die Architektur eine Erweiterung auf andere Linux-Distributionen ermöglichen.

Das Programm soll erkennen, ob notwendige Tools vorhanden sind.

Beispielsweise:

```text
archiso
pacman
mkarchiso
```

Wenn notwendige Komponenten fehlen, soll das Programm den Benutzer verständlich darauf hinweisen.

---

# 12. Sicherheit

Sicherheit ist wichtig.

Beachte insbesondere:

* keine unsichere Shell-Ausführung von Benutzereingaben
* keine Passwörter in Logs
* keine unnötigen Root-Rechte
* privilegierte Operationen klar trennen
* Pakete validieren
* Pfade validieren
* keine beliebigen Befehle aus GUI-Feldern ausführen
* temporäre Build-Verzeichnisse sicher erstellen
* Build-Verzeichnisse nach Möglichkeit automatisch bereinigen

Da ISO-Builds teilweise Root-Rechte benötigen, soll die Anwendung diese nur dort verwenden, wo sie wirklich notwendig sind.

---

# 13. Fehlerbehandlung

Das Programm darf bei Fehlern nicht einfach abstürzen.

Beispielsweise:

```text
ERROR

Das Paket "example-package" konnte nicht gefunden werden.

Mögliche Ursachen:
- Paketname falsch geschrieben
- Repository nicht erreichbar
- Paket existiert nicht

[Zurück] [Log anzeigen]
```

Build-Logs sollen gespeichert werden können.

---

# 14. Dry Run

Implementiere einen Dry-Run-Modus.

Dabei wird noch keine ISO erstellt.

Das Programm zeigt stattdessen:

```text
Geplante Installation:

Desktop:
KDE Plasma

Kernel:
linux

Packages:
firefox
steam
git
...

Services:
NetworkManager

Locale:
de_DE.UTF-8

Timezone:
Europe/Berlin
```

Der Benutzer kann anschließend bestätigen:

```text
[ BUILD STARTEN ]
```

---

# 15. Abhängigkeiten

Das Programm soll möglichst intelligent mit Paketabhängigkeiten umgehen.

Wenn beispielsweise ein Benutzer eine Komponente auswählt, die weitere Pakete benötigt, sollen diese automatisch berücksichtigt werden.

Beispielsweise:

```text
Steam
 ↓
benötigte Abhängigkeiten
 ↓
zusätzliche Pakete
```

Dabei soll möglichst der Arch-Paketmanager die tatsächliche Dependency-Auflösung übernehmen und die Anwendung nicht versuchen, den Arch-Paketmanager nachzubauen.

---

# 16. Updates

Die Anwendung soll nicht mit einer veralteten Paketliste arbeiten.

Vor einem Build soll sie optional die Paketdatenbanken aktualisieren bzw. sicherstellen, dass die benötigten Paketinformationen aktuell sind.

Es soll außerdem klar angezeigt werden, wann die Paketdaten zuletzt aktualisiert wurden.

---

# 17. Erweiterbarkeit

Das Projekt soll von Anfang an modular aufgebaut sein.

Ich möchte später problemlos neue Optionen hinzufügen können:

```text
Desktop
Browser
Kernel
Audio
Networking
Gaming
Development
Drivers
Themes
Services
```

Neue Pakete oder Optionen sollen möglichst über Konfigurationsdateien hinzugefügt werden können, ohne die gesamte GUI neu schreiben zu müssen.

---

# 18. Tests

Erstelle Tests für wichtige Komponenten.

Insbesondere:

* Konfigurationsvalidierung
* Paketvalidierung
* Profil laden/speichern
* Generierung der archiso-Konfiguration
* sichere Pfadverarbeitung
* Build-Plan-Erstellung

ISO-Builds selbst müssen nicht in jedem Test automatisch durchgeführt werden.

---

# 19. Dokumentation

Erstelle eine ausführliche README.

Die README soll erklären:

* Was das Projekt macht
* Voraussetzungen
* Installation
* benötigte Arch-Pakete
* Verwendung
* Profile
* ISO-Build
* eigene Pakete
* eigenes Branding
* Fehlerbehebung
* Architektur des Projekts

Erstelle außerdem eine kurze Entwicklerdokumentation.

---

# 20. Entwicklungsstrategie

Arbeite NICHT alles auf einmal blind herunter.

Gehe schrittweise vor:

### Phase 1

Erstelle die grundlegende Projektstruktur und GUI.

### Phase 2

Implementiere die Konfiguration.

### Phase 3

Implementiere Profile.

### Phase 4

Implementiere Paketvalidierung.

### Phase 5

Implementiere die archiso-Integration.

### Phase 6

Implementiere den tatsächlichen ISO-Build.

### Phase 7

Implementiere Logging und Fehlerbehandlung.

### Phase 8

Implementiere Branding.

### Phase 9

Implementiere Tests.

### Phase 10

Verbessere UI/UX und Dokumentation.

Nach jeder Phase soll der aktuelle Stand funktionsfähig bleiben.

---

# 21. Wichtige Anforderung

Bevor du irgendwelche Arch-spezifischen Befehle oder APIs implementierst, überprüfe die **aktuelle Arch-Linux- und archiso-Dokumentation**.

Verwende keine veralteten Tutorials oder Befehle, wenn die aktuelle Dokumentation eine andere Vorgehensweise beschreibt.

Das Projekt soll mit einer aktuellen Arch-Linux-Version funktionieren.

---

# 22. Endziel

Am Ende möchte ich ein Programm haben, bei dem ein Benutzer ungefähr Folgendes macht:

```text
1. Programm starten

2. "Neue ISO erstellen"

3. Desktop auswählen
   → KDE

4. Kernel auswählen
   → linux

5. Programme auswählen
   → Firefox
   → Steam
   → LibreOffice
   → Git

6. Netzwerk
   → NetworkManager

7. Audio
   → PipeWire

8. Sprache
   → Deutsch

9. Branding
   → FLOS

10. "ISO erstellen"

11. Programm baut automatisch die ISO

12. Ergebnis:

    FLOS-1.0-x86_64.iso
```

Der Benutzer soll dabei **keine Kenntnisse über `archiso`, `pacman` oder die interne ISO-Struktur benötigen**.

## Wichtig für deine Arbeitsweise

Analysiere zuerst das Projekt und die aktuelle Arch-/archiso-Dokumentation.

Erstelle danach einen konkreten Implementierungsplan.

Beginne anschließend mit Phase 1.

Wenn du während der Entwicklung eine technische Entscheidung treffen musst, bevorzuge:

1. aktuelle offizielle Arch-Dokumentation
2. einfache und robuste Lösungen
3. modulare Architektur
4. sichere Implementierung
5. gute Erweiterbarkeit

Vermeide unnötige Komplexität.

Das Ziel ist eine **echte funktionierende Anwendung**, nicht nur ein Mockup oder eine Demo.
