# Branch `Hofa` — Analyse, Korrekturen, neue Oberfläche

Dieses Dokument hält fest, was auf dem Branch passiert ist und **warum**. Es
ist kein Werbetext: die Fundliste steht vollständig darin, auch die Punkte, die
bewusst nicht angefasst wurden, und die Behauptungen, die sich beim Nachsehen
als falsch erwiesen haben.

---

## Ausgangslage

| | Stand vorher |
|---|---|
| Umfang | rund 13 000 Zeilen, 13 Commits |
| Tests | 425 Testfunktionen, mit Parametrisierungen etwa 532 |
| Linter | keiner eingerichtet |
| Fortlaufende Prüfung | keine |
| Oberfläche | `QWizard`, rund 20 verstreute `setStyleSheet`-Aufrufe |

Der Kern war in gutem Zustand — die Architektur (Katalog → Resolver → Plan →
Profil → Bau) trägt. Die Fehler saßen an den Rändern: im Zusammenspiel der
Bauwege, in der Fehlerbehandlung nach einem Abbruch, und in der Oberfläche.

---

## Reihenfolge der Arbeit

1. Testsuite vom Rechner des Entwicklers entkoppeln
2. Container-Weg funktionsfähig machen, Windows-Prozesse ohne Fenster
3. Abbruch und Aufräumen im Bau geradeziehen
4. Kern korrigieren: installiertes System, Paketprüfung, Geheimnisse
5. Datenverlust und hängende Fäden in der Oberfläche
6. Startskripte zusammenführen, Werkzeuge und Doku geradeziehen
7. Fundament der neuen Oberfläche: Designsystem, Bewegung, Navigation
8. Neue Oberfläche: eigene Navigation statt `QWizard`
9. Bau ohne Oberfläche, ISO-Prüfung, Bauhistorie, fortlaufende Prüfung

Nach jedem Block lief die vollständige Testsuite.

---

## Befunde

### Kritisch und hoch

| # | Datei | Befund | Umgesetzt |
|---|---|---|---|
| B1 | `core/build/container.py` | `podman build --file -` bekam die Containerdatei nie über stdin; als Kontext diente das Arbeitsverzeichnis der Anwendung | Eingabe wird übergeben, leeres Temp-Verzeichnis als Kontext |
| B2 | `core/build/targets.py` | `ensure_image()` wurde nie aufgerufen, obwohl die Vorabprüfung „Abbild wird erzeugt" versprach | Aufruf in `deliver_profile` |
| B3 | `gui/wizard.py` | Vorabprüfung synchron im Oberflächenfaden; beim WSL-Ziel rund acht `wsl.exe`-Aufrufe → „keine Rückmeldung" | im Hintergrund, jetzt Teil der Bauseite |
| B4 | `gui/pages/welcome.py` | Zurück zur Startseite und wieder vor löschte die gesamte Zusammenstellung | wird nur bei geänderter Wahl angewendet, und dann mit Rückfrage |
| B5 | `gui/wizard.py` | abgebrochener WSL-Dialog bedeutete „lokal bauen"; bei `export_requested` liefen Export und Bau gleichzeitig | eigenes Abbruch-Zeichen |
| B6 | `gui/wizard.py` | `_saved_once` wurde nie zurückgesetzt: Änderungen nach dem ersten Speichern gingen beim Beenden wortlos verloren | Fingerabdruck wandert mit |
| B7 | `gui/widgets/build_dialog.py` | Escape schloss den Baudialog, der Bau lief unsichtbar weiter | der Bau ist ein Navigationsschritt, kein Dialog |
| B8 | `gui/build_worker.py` | Fadenverweis wurde vor Fadenende gelöscht; „QThread: Destroyed while thread is still running" | Freigabe erst über `finished` |
| B9 | `gui/store.py` | ein Profil ohne `user.*` erzeugte eine Live-ISO, an der sich niemand anmelden kann | fehlende Feld-Vorgaben werden nach dem Laden gesetzt |
| B10 | `core/plan.py` | die archinstall-Paketliste enthielt nur die frei eingegebenen Pakete | `resolution.package_names` wird übernommen |
| B11 | `core/plan.py` | `sys_lang` + `sys_enc` ergaben `de_DE.UTF-8.UTF-8` | Locale wird zerlegt |
| B12 | Katalog | `gfx_driver: "Mesa open-source"`, `greeter: "lightdm"` sind keine archinstall-Werte | korrigiert, Test gegen die Allow-Liste |
| B13 | `core/packages/` | multilib-Paket wurde geprüft, aber das Repository landete nie in `pacman.conf` | Repositorien aus dem Prüfergebnis fließen in die Auflösung |
| B14 | `backend_remote.py` | fiel ein Repository aus, galt der Teilindex als vollständig | fehlende Repos in den Metadaten → `UNVERIFIED` |
| B16 | `archcustomiser.sh` | `"$@"` unter `set -u` brach den Start auf macOS ab | `${1+"$@"}` |
| B17 | `core/build/targets.py` | pkexec-Modus: `EPERM` beim Beenden still verschluckt | wird gemeldet, `pkexec kill` als Ersatzweg |
| B18 | `wsl_build.py`, `targets.py` | Aufräumen mit `rm -rf` ohne `umount -R` und ohne `--one-file-system` | Einhängungen zuerst lösen |

**B15 — nicht umgesetzt wie vorgeschlagen.** Der Verdacht lautete,
`airootfs/etc/os-release` kollidiere mit dem Symlink des Pakets `filesystem`.
Dagegen sprechen die zwei tatsächlich gebauten ISOs. Der Befund bleibt
unbestätigt; die vorgeschlagene Umgehung über einen pacman-Hook wäre in beiden
Fällen korrekt, wurde aber nicht eingebaut, weil sie ein funktionierendes
Verhalten gegen ein ungetestetes tauschen würde.

### Mittel

Umgesetzt: fehlendes `CREATE_NO_WINDOW` und `stdin=DEVNULL` (gemeinsamer
Helfer `core/subprocess_util.py`) · kein Aufräumen nach `BuildError` ·
Abbruch während `fetch_iso` · Kill-Muster traf das eigene Aufräum-`rm` ·
`rmtree` ohne Markerprüfung · Paketnamen aus Profilen ohne Validierung ·
`etc/shadow` weltlesbar exportiert · Datei-Dialog für Verzeichnisfelder ·
Passwortprüfung las den nachhinkenden Store · gesperrter Aktualisieren-Knopf
nach einem Fehler · Escape im nicht abbrechbaren Wartedialog · `zlib.error`
blockierte jeden Start · abgeschnittener Download galt als vollständig ·
Overlay-Kategorien landeten hinter der Zusammenfassung · Tests schrieben ins
echte Zustandsverzeichnis · Tests starteten echtes `wsl.exe` und `podman info` ·
rootless podman wurde erkannt, aber nicht beanstandet.

**Nicht umgesetzt:** die WSL-Vorabprüfung prüft `out_dir` weiterhin nur aus
Windows-Sicht (M14); der Tippfehler-Vorschlag läuft weiterhin ohne Vorfilter
über den ganzen Index (M17). Beides ist Aufwand ohne belegte Beschwerde.

### Beim Nachsehen verworfen

* **„Die Bootmodi sind ungültig."** Die zweiteiligen Namen
  `bios.syslinux`, `uefi.systemd-boot`, `uefi.grub` sind die Namen neuerer
  archiso-Fassungen. Die zwei tatsächlich gebauten ISOs belegen sie.
* **„Der pacman-Zähler ist kaputt."** pacman schreibt ohne Terminal andere
  Zeilen; die Anzeige bleibt stehen, ein Fehler ist es nicht.
* **„`openssh` ist eine tote Option."** Ein Client ohne Dienst ist gewollt.

### Anomalien und Werkzeuge

* Clone- und pipx-URLs zeigten auf ein Repository, das es nicht gibt.
* `archcustomiser.sh` lag im Index als `100644` und war damit nicht ausführbar;
  die README erklärte das als „beim Herunterladen verloren".
* `py.typed` stand in den Paketdaten, die Datei fehlte.
* `.bat` und `.sh` enthielten dieselbe Logik zweimal, reichten Argumente nicht
  durch, erkannten keine defekte venv und installierten für Endanwender die
  Entwicklungsabhängigkeiten. Ersetzt durch `tools/bootstrap.py` als
  gemeinsamen Kern.
* `tools/echtbau.py` enthielt ein fest eingebautes Passwort. Ersetzt durch
  `--build`.
* Der Auftragstext lag mit einem Leerzeichen im Dateinamen im
  Wurzelverzeichnis; jetzt `docs/SPEC.md`.

---

## Technische Entscheidung zur Oberfläche

Zur Wahl standen drei Wege:

| | Ansatz | Bewertung |
|---|---|---|
| **A** | PySide6/QWidgets, neu gebaut mit eigenem Designsystem | **gewählt** |
| B | QML | zweite Sprache, Bindungsfehler erst zur Laufzeit, offscreen schwer prüfbar |
| C | C#/Avalonia über eine JSON-RPC-Brücke | .NET-Laufzeit (≥ 70 MB) zusätzlich, oder 532 Tests verlieren |

Ausschlaggebend war weniger die Optik als die Prüfbarkeit. Offscreen-Tests
sind mit QWidgets im Projekt bewiesen; QML wäre in pytest nur über
`objectName` greifbar. Und `store.py`, alle drei Worker, die Dialoge und die
gesamte Seitenlogik bleiben bei A unverändert erhalten.

Der einzige Punkt, an dem B klar besser gewesen wäre, sind Animationen. Der
Gegenbeweis kam aus der Praxis: rund hundert selbst gezeichnete Karten, eine
Bauseite und ein Erscheinungswechsel laufen flüssig, weil jede Karte **ein**
Widget ist und nicht sechs.

---

## Paritätsliste

Vor dem Löschen der alten Oberfläche wurde jede Fähigkeit der sechs
entfernten Dateien einzeln nachgesehen. Die Liste ist die Abnahme.

**Fenster und Anwendung:** Titel · aus dem Bildschirm abgeleitete
Mindestgröße · Anwendungs- und Organisationsname · Fenstersymbol ·
Katalogfehler als Meldung mit Rückgabewert 2 · Umgebung wird protokolliert ·
Paketdaten laufen nach `show()` an · Fehlermeldung der Paketdaten als Notiz ·
Tastenkürzel · Mnemoniken.

**Navigation:** nur sichtbare Kategorien in Katalogreihenfolge ·
`visible_when` · Zustände und Anklickbarkeit · Sprung · nach dem Laden eines
Profils gilt alles als besucht · Weiter gesperrt bei blockierenden Meldungen ·
die Zusammenfassung ist der Punkt ohne Wiederkehr.

**Startseite:** Vorlagenkarten · „Eigenes Profil laden" · „Von vorn
beginnen" · Umgebungszeile · Anwenden beim Verlassen · Abbruch des
Dateidialogs · Hinweise-Dialog · Passwort-Hinweis · `profileLoaded`.

**Profile:** Laden ab dem mitgelieferten Verzeichnis · Fehler und Hinweise ·
Speichern mit Vorschlagspfad und Bestätigung · Export als Archiv oder
Verzeichnis · Ergebnisdialog · Fehlerdialog mit Ursachen · Rückfrage beim
Beenden über den Fingerabdruck.

**Auswahlseite:** Gruppen und Reihenfolge · Spaltenzahl · Exklusivität über
die ganze Kategorie · Suche ab acht Einträgen · Trefferzähler · Suche über
Beschriftung, Beschreibung, Kennung und Paketnamen · leere Gruppen
verschwinden · automatisch Ergänztes bleibt sichtbar · Begründung der
Ergänzung · `enabled_when` mit Hinweis · Suchbegriff überlebt das
Neuzeichnen · Abzeichen · Hilfe-Link.

**Formularseite:** alle acht Widget-Arten · Geheimnisse erst bei
`editingFinished` · Live-Prüfung bei `textEdited` · 250 ms Verzögerung ·
Pflichtstern und Legende · Hilfetext · `visible_when`/`enabled_when` ·
Pflicht, Wiederholung und Validator mit Warnung gegen Fehler ·
Hashing-Warnung · Feldfehler in der Hinweisleiste · Abgleich ohne
Geheimnisse.

**Zusatzpakete:** Hinweis · Platzhalter · 350 ms · Tabelle mit Farben, Fettung
und Kurzhilfen · Anbieterwahl · Status und Aktualisieren · Fehler in der
Hinweisleiste · Neuprüfung nach dem Laden.

**Zusammenfassung:** Kopfzeile · vier Registerkarten · Kopieren · Verdikt ·
Profilfehler in der Hinweisleiste · Bauplan · Paketprüfung.

**Bau:** Zielwahl · WSL-Einrichtung · Export-Angebot · „hier nicht baubar" ·
Inhalt der Vorabprüfung · Inhalt des Baudialogs · Bündelung der Ausgabe alle
120 ms.

**Dialoge und Bausteine:** `ExportResultDialog` · `ErrorDialog` ·
`WslSetupDialog` · `run_with_wait` · `copy_to_clipboard` · `open_path` ·
`CodeBlock` · `HintLabel` · `HeadlineLabel` · Schrittliste ·
Erkennung der Systemerscheinung · `format_size` · Monospace-Schrift ·
relative Schriftgrößen · SVG-Symbole.

**Ersatzlos entfallen: nichts.** Was den Ort gewechselt hat: `format_size`,
`mono_font` und die Schriftstufen aus `theme.py` liegen jetzt in
`gui/design/typo.py`; die Zustandszeichen der Schrittliste sind gezeichnet
statt geschrieben.

---

## Teststrategie

| Ebene | Was geprüft wird |
|---|---|
| `tests/test_navigation.py` | die Navigationsregeln — **ohne Qt**, ohne `QApplication` |
| `tests/test_gui_window.py` | Fenster, Seiten, Erscheinungsbild, Vorschau, ISO-Panel |
| `tests/test_build_page.py` | Vorabprüfung, Bau, Abbruch, Ergebnis — mit Attrappen-Auftrag |
| `tests/test_gui.py` | Store, Geheimnisse, Abbruchfäden, Prüfsumme |
| `tests/test_verify.py` | ISO-Plausibilität, auch die kaputten Fälle |
| `tests/test_history.py` | Bauhistorie, Robustheit, keine Geheimnisse |
| `tests/test_cli_build.py` | Bau ohne Oberfläche, Rückgabewerte, Passwortweg |

Drei Regeln, die im ganzen Lauf gelten:

1. **Offscreen.** `QT_QPA_PLATFORM=offscreen`, kein Fenster geht auf.
2. **Keine Bewegung.** `ARCHCUSTOMISER_MOTION=off` setzt jede Dauer auf 0; die
   Tests prüfen Endzustände, nicht Frames.
3. **Nichts außerhalb des Testverzeichnisses.** Eine Fixture lenkt Heimat-,
   Zustands- und Zwischenspeicherverzeichnis um. Vorher schrieb die Testsuite
   Bauprotokolle in das echte Verzeichnis des Benutzers und löschte dort
   echte.

Eine Falle, die dabei auffiel und der Erwähnung wert ist: ein Fenster je Test
zu erzeugen und nur `deleteLater()` zu rufen genügt nicht. Ohne laufende
Ereignisschleife bleibt die Löschung in der Warteschlange stehen, die Fenster
aller bisherigen Tests leben weiter, und jedes `setStyleSheet` poliert sie
mit. Der Lauf wurde dadurch quadratisch langsam — 307 Sekunden für 35 Tests.
Mit `sendPostedEvents(None, DeferredDelete)` und einem einmal gesetzten
Stylesheet sind es 12.

---

## Offene Punkte

1. **Keine LICENSE.** Das ist eine Entscheidung des Repository-Autors; es wurde
   keine angelegt.
2. **Der Boot-Test steht weiter aus.** Die neue ISO-Prüfung schließt aus, dass
   eine Datei sicher *nicht* startet — sie beweist nicht, dass sie startet.
3. **Der Container-Weg** ist durch Tests belegt, aber auf keinem echten
   Ubuntu, Fedora oder Mac gelaufen.
4. **B15** bleibt unbestätigt (siehe oben).
