# Änderungen

Das Format lehnt sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/)
an. Versionen folgen [Semantic Versioning](https://semver.org/lang/de/).

---

## [Unveröffentlicht]

Zwei Entwicklungslinien sind hier zusammengeführt: der Zweig `Hofa` mit der
neuen Oberfläche, und `main` mit einer Durchsicht des Kerns. Beide hatten
unabhängig voneinander an denselben Fehlern gearbeitet — teils mit
unterschiedlichen Lösungen. Wo sie sich widersprachen, hat die bessere gewonnen,
nicht die neuere.

### Aus `main` übernommen

- **NVIDIA zeigte auf Pakete, die es nicht mehr gibt.** Arch hat am 20.12.2025
  auf die offenen Kernelmodule umgestellt: `nvidia` → `nvidia-open`,
  `nvidia-lts` → `nvidia-open-lts`, `nvidia-dkms` → `nvidia-open-dkms`. Ein Bau
  mit dem Standard- oder LTS-Kernel scheiterte deshalb mit „nicht gefunden".
  Der Wert für `profile_config.gfx_driver` stand auf `Nvidia (proprietary)`,
  das archinstalls Enum gar nicht mehr führt — ein unbekannter Wert lässt
  archinstall die **ganze** Konfiguration verwerfen. Ebenso `p7zip`, das nur
  noch als *provides* von `7zip` existiert.
- **Der Container-Weg ist zum ersten Mal wirklich gelaufen** (podman 6.1, echter
  Linux-Kernel): 1311 MB, `CD001`, `0x55AA`, `MINIARCH_1_0` — zeichengleich mit
  der über WSL gebauten ISO. Dabei kamen zwei Fehler heraus, die hier noch
  offen waren: **docker wurde grundsätzlich für tot erklärt**, weil die
  Rootless-Frage in podman-Vokabular gestellt wurde (`{{.Host…}}`) und docker
  sie mit einem Fehler quittiert — womit macOS unerreichbar war; und dem
  **Container-Abbild fehlte `grub`**, ohne das der Bootmodus `uefi.grub` nicht
  baubar ist. Die Vorabprüfung hätte „Abbild neu bauen" gesagt, wobei wieder
  eines ohne grub entstanden wäre.
- **Aus einer Zusicherung wurde eine Messung.** Statt die Engine zu fragen, ob
  sie rootless läuft, hängt die Vorabprüfung `devtmpfs` im Container ein und
  wieder aus — zwei Sekunden, und die Antwort ist endgültig. Der Hinweis auf
  rootless bleibt für den Fall, dass noch kein Abbild vorliegt.
- Multilib-Pakete aus dem Freitextfeld aktivieren jetzt auch `[multilib]` in
  der erzeugten `pacman.conf`; ein Teilindex gilt als unvollständig, statt
  vorhandene Pakete als „gibt es nicht" zu melden; `minimal.yaml` legt ein
  Benutzerkonto an, statt eine ISO zu erzeugen, in die sich niemand anmelden
  kann.
- `.github/workflows/container.yml` und `tools/containerprobe.py`: ein echter
  Container-Lauf auf einem echten Ubuntu, nur auf Knopfdruck.

### Aus `Hofa` übernommen

Eine Runde Aufräumen und eine neue Oberfläche. Der Kern hat dabei mehr Fehler
verloren als die Oberfläche Zeilen gewonnen hat.

### Neu

**Die Oberfläche ist neu gebaut.** Eigene Navigation statt `QWizard`, ein
Designsystem statt zwanzig verstreuter `setStyleSheet`-Aufrufe, selbst
gezeichnete Karten statt sechs Kindwidgets je Option.

- **Schrittliste mit fünf Zuständen**, darunter *übersprungen*. Wer keine
  grafische Sitzung wählt, sieht „Grafiktreiber" als übersprungen statt als
  offenen Schritt, auf den er vergeblich wartet.
- **Vorwärtsspringen.** Die Seiten holen ihren Inhalt ohnehin aus dem Store; es
  gab keinen Grund, jemanden durch vierzehn Schritte zu führen, der weiß, was er
  will.
- **ISO-Panel neben jeder Seite:** Paketzahl und Herkunft, Repositorien,
  Dienste, geschätzte Größe, Kernel, ISO-Name. Diese Frage wurde bisher genau
  einmal beantwortet — auf der letzten Seite.
- **Branding-Vorschau:** Bootmenü, Live-Desktop und `os-release`, aus den
  aktuellen Feldwerten gezeichnet. Ein falsch dimensioniertes Splash-Bild fällt
  jetzt vor dem Bau auf und nicht nach einer halben Stunde.
- **Hell und Dunkel**, frei wählbare Akzentfarbe, beide Paletten gegen WCAG AA
  geprüft. Dunkel ist die Vorgabe.
- **Bewegungen respektieren die Systemeinstellung** („Animationen reduzieren").
  Ohne sie ist jede Dauer 0 und der Endzustand steht sofort.
- **Die Bauseite ersetzt zwei modale Dialoge:** Vorabprüfung mit gestaffelten
  Haken, Phasenliste samt der erst zur Laufzeit bekannten mkarchiso-Stufen,
  Protokoll mit Autoscroll-Pause, dauerhaft sichtbarer Abbruch, Ergebnis mit
  Pfad, Größe, Dauer und SHA-256.

**Neue Funktionen außerhalb der Oberfläche:**

- `--build PROFIL` baut eine ISO **ohne Bildschirm**. Fortschritt geht nach
  stderr, das Ergebnis nach stdout; das Passwort kommt über `--password-stdin`
  und nie über ein Argument; Strg+C bricht den Bau ab, statt ihn zu erschlagen.
  Eigene Rückgabewerte für fertig, fehlgeschlagen, abgebrochen und blockiert.
- `--verify-iso DATEI` prüft eine ISO auf Plausibilität: CD001-Signatur,
  MBR-Signatur, El-Torito-Bootkatalog, Sektorausrichtung. Ohne Fremdwerkzeug,
  es werden nur ein paar Kilobyte gelesen.
- **Prüfsumme neben der ISO.** `<name>.iso.sha256` im Format von `sha256sum -c`.
  Berechnet wird sie im Bau-Faden — eine 4-GB-Datei im Oberflächenfaden zu lesen
  legt das Fenster still.
- **Bauhistorie.** `--history` listet, was hier schon gebaut wurde: Name, Größe,
  Dauer, Bauweg, Prüfsumme. Ein JSON-Eintrag je Bau unter `state_dir()/builds`,
  ohne einen einzigen Wert aus dem SecretStore.
- **Fortlaufende Prüfung auf GitHub Actions:** Ubuntu, Windows und macOS ×
  Python 3.11 bis 3.13, dazu `ruff`, `mypy` und ein Lauf, der `core/` ohne
  installiertes PySide6 importiert.
- `tools/gallery.py` erzeugt die Bildschirmfotos offscreen — reproduzierbar,
  statt von Hand abfotografiert.
- Ein gemeinsamer Startkern `tools/bootstrap.py`; `.bat` und `.sh` sind nur noch
  Starter. Argumente werden durchgereicht, eine kaputte venv wird erkannt.

### Behoben

- **Ein Hintergrundfaden riss beim Beenden eine Ausnahme hoch.** Wird das
  Fenster geschlossen, während die Paketdaten noch laden, räumt Qt den
  Empfänger ab — der Faden sendet dann ins Leere („Signal source has been
  deleted"). Das `except` deckte das Laden ab, nicht das Melden. Beim
  Zusammenführen aufgefallen, weil der Starttest das Programm absichtlich
  mitten im Laden beendet.


**Datenverlust und hängende Fäden**

- Von der ersten Katalogseite zurück zur Startseite und wieder vor **löschte die
  gesamte Zusammenstellung**. „Von vorn beginnen" ist vorgehakt, und das
  Weitergehen rief bedingungslos `store.reset()`. Die einzige Stelle im
  Programm, die Arbeit ohne Warnung vernichtete — während das Beenden
  ausdrücklich nachfragt.
- Nach dem ersten Speichern galt **jede weitere Änderung als gesichert**: der
  Merker „einmal gespeichert" wurde nie zurückgesetzt.
- Escape schloss den Baudialog. `QDialog` ruft dabei `reject()` und nicht
  `close()`, `closeEvent` wurde also übergangen: der Bau lief unsichtbar weiter,
  ein zweiter war startbar, und beim Beenden zerstörte Qt einen laufenden
  Faden.
- Escape umging auch den nicht abbrechbaren Wartedialog — bei der
  archiso-Installation lief pacman danach unsichtbar weiter.
- Der Abbruch lief im Oberflächenfaden bis in die WSL-Verteilung hinein und
  legte das Fenster bis zu anderthalb Minuten still. Der Knopf wird aber
  gedrückt, **weil** der Rechner schon überlastet ist.

**Der Bau**

- `podman build --file -` bekam die Containerdatei nie über die
  Standardeingabe, und als Kontext diente das Arbeitsverzeichnis der
  Anwendung.
- `ensure_image()` wurde nie aufgerufen, obwohl die Vorabprüfung „Abbild wird
  erzeugt" versprach.
- Nach `BuildError` und `ProfileError` blieb aufgeräumt nichts — zehn bis
  dreißig Gigabyte blieben liegen.
- Aufgeräumt wurde mit `rm -rf` ohne vorheriges `umount -R` und ohne
  `--one-file-system`. Bei liegengebliebenen pacstrap-Einhängungen ist das
  gefährlich.
- Das Kill-Muster des Abbruchs traf das eigene Aufräum-`rm`.
- Ein abgebrochener WSL-Dialog bedeutete „lokal bauen" — unter Windows ein
  sinnloser Versuch mit irreführender Fehlermeldung.
- Im pkexec-Modus wurde `EPERM` beim Beenden still verschluckt; der Dialog blieb
  bis zum Bauende gesperrt.
- Unter `pythonw` blitzten Konsolenfenster auf — beim Abbruch zweimal pro
  Sekunde.

**Das installierte System**

- Die erzeugte `archinstall.json` enthielt als Paketliste **nur** die frei
  eingegebenen Zusatzpakete. Desktop, Treiber und Programme fehlten im dauerhaft
  installierten System.
- `sys_lang` und `sys_enc` ergaben zusammen `de_DE.UTF-8.UTF-8`.
- `gfx_driver: "Mesa open-source"` und `greeter: "lightdm"` sind keine gültigen
  archinstall-Werte.

**Die Paketprüfung**

- Ein frei eingegebenes multilib-Paket wurde gegen `multilib` geprüft, aber das
  Repository landete nie in der erzeugten `pacman.conf` — der Bau brach erst
  nach Minuten mit „target not found" ab.
- Fiel ein Repository beim Laden aus, galt der Teilindex als vollständig, und
  seine Pakete wurden als „nicht gefunden" gemeldet — blockierend.
- Ein defekter Zwischenspeicher (`zlib.error`) blockierte jeden Start.
- Ein abgeschnittener Download wurde als vollständig gespeichert.

**Geheimnisse und Rechte**

- Aus einem Profil geladene Paketnamen liefen ohne Prüfung in `packages.x86_64`.
- `etc/shadow` wurde mit dem Passwort-Hash weltlesbar (0644) exportiert.

**Kleineres**

- Für Ausgabe- und Arbeitsverzeichnis erschien ein Datei-Dialog; ein Verzeichnis
  ließ sich damit gar nicht wählen.
- Die Passwortprüfung las den Store, der bei geheimen Feldern bis zum
  Fokuswechsel hinterherhinkt: die Wiederholung meldete dauerhaft „stimmen nicht
  überein", und „Weiter" brauchte zwei Klicks.
- Nach einem fehlgeschlagenen Laden der Paketdaten blieb „Paketdaten
  aktualisieren" dauerhaft gesperrt.
- `"$@"` unter `set -u` brach den Start auf macOS ab (bash 3.2).
- Die UTF-16-Erkennung der `wsl.exe`-Ausgabe verschluckte sich an CJK-Zeichen.
- Der Zeilenpuffer des Bau-Protokolls wuchs quadratisch.
- Overlay-Kategorien ohne `step_order` landeten hinter der Zusammenfassung.

### Geändert

- `core/subprocess_util.py` bündelt `CREATE_NO_WINDOW` und `stdin=DEVNULL`
  für alle Unterprozesse.
- `gui/navigation.py` ist **Qt-frei** — die Navigationsregeln sind ohne
  Bildschirm prüfbar.
- Der Katalog kennt zwei neue generische Schlüssel: `Category.preview` und
  `FieldSpec.preview_role`. Die Oberfläche kennt weiterhin **keine** Kategorie
  namentlich.
- `ruff` und `mypy` sind eingerichtet und laufen sauber durch.
- Der Auftragstext aus dem Wurzelverzeichnis heißt jetzt `docs/SPEC.md` —
  ein Leerzeichen im Dateinamen bricht jeden zweiten Shell-Aufruf.

### Entfernt

`gui/wizard.py`, `gui/theme.py`, `gui/widgets/build_dialog.py`,
`gui/widgets/preflight_dialog.py`, `gui/widgets/step_sidebar.py`,
`gui/widgets/option_widget.py`.

Jede Fähigkeit daraus ist an anderer Stelle wieder da; die Abnahme lief gegen
eine Paritätsliste in `docs/PLAN-Hofa.md`. `format_size`, `mono_font` und die
Schriftstufen aus `theme.py` liegen jetzt in `gui/design/typo.py`.

**Aus einer Durchsicht der neuen Oberfläche** (sieben Prüfrichtungen, jeder
Befund gegengeprüft):

- **Die Oberfläche startete gar nicht.** `run()` rief eine Eigenschaft als
  Methode auf; das Programm stürzte ab, bevor ein Fenster zu sehen war. Kein
  Test hatte `run()` je aufgerufen.
- Eine Animation, deren Widget mitten in der Bewegung verschwand, blieb ewig in
  der Buchführung stehen. Zweimal schnell „Weiter" genügte: die
  Leerlaufzusicherung wurde damit dauerhaft falsch, und das Schließen des
  Fensters brach mit einem Fehler ab.
- Das Beenden brach den Bau ab und fragte **danach** nach der ungesicherten
  Zusammenstellung. Wer dort abbrach, blieb im Programm — ohne seinen Bau.
- Nach einem fehlgeschlagenen Bau gab es keinen Weg zurück; die
  Protokollansicht hatte keinen einzigen Knopf.
- Ein gescheiterter Abbruch war unsichtbar. Ein zu spät gekommener Abbruch warf
  die Prüfsumme einer fertigen ISO weg.
- Die Schrittliste war nur mit der Maus bedienbar.
- Zwei Textfarben und zwei Knopfbeschriftungen erfüllten die AA-Schwelle nicht;
  der Fokusrahmen auf einem akzentfarbenen Knopf war akzentfarben.
- Ein Tippfehler im Validatornamen deaktivierte die Feldprüfung lautlos.
- Der Protokollfilter erfasste weder Tracebacks noch Argumente, die keine
  Zeichenketten sind — beide entstehen erst im Formatter.

### Tests

Von 532 auf 738. Neu unter anderem: Navigation ohne Qt, Bauseite mit
Attrappen-Auftrag, ISO-Plausibilität, Bauhistorie, Bau ohne Oberfläche,
Startskripte, Kommandozeile.

Die Testsuite fasst außerdem das echte Benutzerverzeichnis nicht mehr an — sie
schrieb Bauprotokolle dorthin und löschte dort echte.
