# Entwicklerdokumentation

Diese Datei erklärt, **warum** der Code so aufgebaut ist. Das *Was* steht im
Code selbst.

---

## Die drei Entscheidungen, die alles andere bestimmen

### 1. Semantik ist die Wahrheit, Pakete sind abgeleitet

`BuildConfig` speichert `desktop.kde`, nicht `["plasma-meta", "dolphin", ...]`.
Die Paketliste entsteht daraus im `Resolver`.

Der Grund ist nicht Eleganz, sondern eine harte Anforderung: `archiso` erzeugt
ausschließlich ein **Live-System**. Nach einem Neustart ist alles weg. Damit der
Benutzer sein System dauerhaft installieren kann, liefert die ISO `archinstall`
samt vorbereiteter Konfiguration mit — und archinstall will semantische Angaben:

```json
{
  "profile_config": {"profile": {"main": "Desktop", "details": ["KDE Plasma"]},
                     "greeter": "sddm"},
  "audio_config": {"audio": "pipewire"},
  "kernels": ["linux-zen"]
}
```

Aus einer flachen Paketliste ließe sich `"KDE Plasma"` nicht zurückgewinnen.
Würde das Modell nur Pakete führen, müsste man später von Paketnamen auf
Semantik raten — und zwar für jede Option einzeln.

Deshalb trägt jede Katalogoption ein `semantics`-Feld mit gepunkteten
Schlüsseln, die `plan.py` in die verschachtelte archinstall-Form überführt.

### 2. Der Katalog ist das Programm

Vier Seitentypen, alles andere YAML. Die Spezifikation verlangt das für
Paketlisten ausdrücklich; hier gilt es für **alle** Schritte, auch Textfelder
und Passwortfelder.

Praktische Folge: Die Datei `src/archcustomiser/data/catalog/categories/65-drivers.yaml` wurde
angelegt, *nachdem* die Oberfläche fertig war, und erschien allein durch ihre
Existenz als vollwertiger Wizard-Schritt — inklusive Sichtbarkeitsbedingung,
kernelabhängiger Paketauswahl und Beitrag zur Installationskonfiguration.

**Capabilities statt Konfliktlisten.** Statt jede Audio-Option gegen jede andere
zu deklarieren (quadratischer Aufwand), liefern alle die Rolle `audio-server`
mit `arity: at_most_one`. Eine neue Audio-Option erbt die Regel automatisch.
Dasselbe Muster löst den Display-Manager: `required_if: ["cap:graphical-session"]`
plus `default_provider` — wer eine grafische Sitzung wählt, bekommt automatisch
einen Login-Screen, sichtbar gekennzeichnet als „automatisch ergänzt".

### 3. Nie eine Aussage ohne Datengrundlage

Die Paketschicht darf ein Paket **nur dann** als „existiert nicht" melden, wenn
ein vollständiger Index vorliegt. Ohne Index lautet die Antwort „nicht prüfbar".

Ohne diese Regel würde ein DNS-Ausfall die Meldung „Das Paket firefox existiert
nicht" erzeugen — und der Benutzer würde einen korrekten Paketnamen löschen. Ein
Test hält das fest: `test_without_index_nothing_is_claimed_missing`.

---

## Die Paketschicht im Detail

### Warum ein Index und keine Einzelabfragen

Naheliegend wäre `https://archlinux.org/packages/search/json/?name=firefox` pro
Name. Drei Befunde sprechen dagegen — alle gegen die echten Endpunkte geprüft:

1. **Kein Batching.** `?name=firefox&name=htop` liefert **nur `htop`**. Django
   nimmt bei einem `CharField` den letzten Wert. Der Fehler wäre *still*: man
   bekäme `valid: true` und würde `firefox` als „nicht gefunden" melden.
2. **`?grp=base-devel` wird ignoriert.** Kein Fehler, sondern `valid: true` plus
   250 beliebige Pakete. Wer den Parameter rät, validiert Müll als gültig.
3. **Gruppen und virtuelle Pakete sind über die Namenssuche prinzipiell nicht
   beantwortbar.** `plasma` ist kein Paket, sondern eine Gruppe mit 70
   Mitgliedern; `ttf-font` ist eine Rolle, die elf Schriftpakete ausfüllen.

Der Index löst alle drei Probleme auf einmal:

| Fall | Anfragen | Datenmenge |
|---|---|---|
| Erster Start | 3 | ~9 MB |
| Frischeprüfung (304) | 3 | ~0 |
| 5 Pakete prüfen | **0** | 0 |
| 500 Pakete prüfen | **0** | 0 |

Gemessen: 15.424 Pakete, 107 Gruppen und 7.490 bereitgestellte Namen in rund
3 Sekunden. Danach ist jede Prüfung eine Suche im Speicher.

### Zwei Backends, ein Parser

* `PacmanSyncBackend` liest `/var/lib/pacman/sync/*.db` direkt. Kein Subprozess,
  keine Root-Rechte, keine übersetzte Ausgabe zu parsen — und die
  Datei-Änderungszeit ist unmittelbar die Antwort auf „wann zuletzt
  aktualisiert".
* `RemoteIndexBackend` lädt dieselben Dateien von einem Spiegelserver. Läuft
  unter Windows und dient auf Arch als Rückfallebene.

Beide erzeugen denselben `RepoIndex` mit demselben Parser. Rund 90 % des Codes
sind geteilt.

**Aktualisieren ohne Root** folgt dem Muster von `checkupdates` aus
pacman-contrib:

```
fakeroot -- pacman -Sy --disable-sandbox-filesystem --dbpath <eigener-pfad> --logfile /dev/null
```

Die Systemdatenbank wird dabei nicht angefasst — ein ISO-Builder hat keinen
Grund, den Paketstand des Rechners zu verändern. `sudo` wird nie verwendet.

### Echte Abhängigkeitsauflösung

Steht in einem **getrennten** Protokoll (`SupportsDependencyPreview`), das nur
das pacman-Backend erfüllt. Wo pacman fehlt, blendet die Oberfläche die Vorschau
aus, statt eine plausible, aber selbst gerechnete Liste anzuzeigen. Eine
erfundene Liste wäre schlimmer als keine, weil sie glaubwürdig aussieht.

### Sicherheitsgrenze: `names.py`

Jeder Name aus einem Eingabefeld oder einer Profildatei muss durch
`validate_name()`, bevor er `subprocess`, `urllib` oder das Dateisystem
erreicht. Abgelehnt werden unter anderem führendes `-` (wäre ein
Kommandozeilenschalter), Pfadtrenner, Nicht-ASCII-Zeichen (Homoglyphen) und
Nullbytes.

Zusätzlich setzt jeder pacman-Aufruf `--` vor die Namensliste. Zwei unabhängige
Schutzschichten. Ein Test weist nach, dass bei ungültiger Eingabe weder ein
Prozess gestartet noch eine Verbindung geöffnet wird.

---

## systemd-Dienste sind keine Strings

Der häufigste Fehler beim Nachbau von `systemctl enable`: anzunehmen, jeder
Dienst brauche einen Symlink unter `multi-user.target.wants/`.

Gegen die echten Unit-Dateien und archiso/releng geprüft:

| Unit | Was `enable` tatsächlich anlegt |
|---|---|
| `NetworkManager.service` | `multi-user.target.wants/` **und** einen dbus-Alias |
| `sddm.service` | **nur** `display-manager.service` (Alias) — kein `.wants` |
| `bluetooth.service` | `bluetooth.target.wants/` + `dbus-org.bluez.service` |
| `systemd-timesyncd.service` | `sysinit.target.wants/` |
| `fstrim.timer` | `timers.target.wants/` |

Ein `graphical.target.wants/sddm.service` wäre wirkungslos — der Login-Screen
würde einfach nicht starten, ohne Fehlermeldung.

Das ist aus dem Paketnamen nicht ableitbar und steht deshalb kuratiert im
Katalog. Der Lader lehnt einen Dienst mit `action: enable` ohne `wanted_by` und
ohne `aliases` ab, weil ein solcher Eintrag garantiert wirkungslos wäre.

`enable_in` trennt zusätzlich Live-ISO und installiertes System: `graphical.target`
gehört in die Live-Sitzung, nicht in die archinstall-Dienstliste.

---

## Warum QWizard und nicht QStackedWidget

* `nextId()` plus der interne Seitenverlauf erledigen das Überspringen
  unsichtbarer Kategorien und das korrekte Zurückblättern. Ein Eigenbau müsste
  den Verlaufsstapel samt Randfällen nachbilden — geschätzt 200 Zeilen reine
  Navigationslogik ohne funktionalen Gewinn.
* `isComplete()` und `validatePage()` sind zwei vorhandene, semantisch
  verschiedene Prüfebenen: die eine sperrt den Weiter-Knopf live, die andere
  prüft beim Verlassen.
* `IndependentPages` verhindert, dass Qt beim Zurückblättern Eingaben
  zurücksetzt — der Store ist die Quelle der Wahrheit, nicht die Seite.

Bewusst **nicht** benutzt: `registerField()`. Die API ist auf statisch
deklarierte Widgets zugeschnitten und für dynamisch erzeugte Checkbox-Mengen
unbrauchbar.

**Absicherung:** Der Seiteninhalt lebt in gewöhnlichen `QWidget`s. Sollte später
eine freie Sprungnavigation nötig werden, bleibt der Umbau lokal.

Seiten werden zur Laufzeit **nie** hinzugefügt oder entfernt — das würde den
Seitenverlauf zerstören. Nicht zutreffende Seiten werden in `nextId()`
übersprungen.

---

## Signalfluss

```
OptionWidget.toggled
  -> CatalogSelectionPage._on_option_toggled
  -> SelectionStore.toggle
       [BuildConfig ändern, Resolver aufrufen, Resolution zwischenspeichern]
  -> selectionChanged + resolutionChanged + issuesChanged
       -> Seiten zeichnen sich neu (mit QSignalBlocker)
       -> completeChanged  (Weiter-Knopf)
       -> Schrittliste markiert Fehler
```

Schleifenschutz doppelt: `QSignalBlocker` beim programmatischen Setzen und ein
`_applying`-Flag im Store.

`SelectionSource` (`USER`/`AUTO`/`PROFILE`/`DEFAULT`) ist der Schlüssel für
korrektes Rückgängigmachen. Wechselt jemand von KDE zu GNOME, verschwindet das
automatisch ergänzte SDDM — ein selbst angeklicktes SDDM bleibt.

---

## Passwörter

Drei Schichten:

1. `Secret` verweigert die versehentliche Preisgabe: `repr`/`str`/`format`
   liefern `***`, Pickling wirft. Der Klartext ist nur über `reveal()`
   erreichbar — eine Stelle, die man greppen kann.
2. Der Klartext liegt in einem `bytearray`, das `burn()` mit Nullen
   überschreibt.
3. Jeder Wert registriert sich beim Log-Filter, der zusätzlich alles maskiert,
   was wie ein crypt(3)-Hash aussieht (`$y$`, `$6$`, `$2b$`).

Strukturell wichtig: Geheimnisse liegen im `SecretStore`, **nicht** in
`BuildConfig`. Ein versehentliches `yaml.dump(config)` kann sie damit gar nicht
erfassen.

Python 3.13 hat das `crypt`-Modul entfernt (PEP 594). Umgesetzt sind deshalb
zwei Stufen: `ctypes` gegen `libxcrypt` (yescrypt, kein Subprozess), sonst die
eigene sha512crypt-Rechnung in `core/archiso/sha512crypt.py`. Beide brauchen
kein fremdes Programm — der Klartext verlässt den Prozess nie, und die Frage
nach argv über `/proc/<pid>/cmdline` stellt sich gar nicht mehr.

Einzelheiten im Abschnitt [Passwort-Hash](#passwort-hash) weiter unten.

---

## Tests

561 Tests, ohne Netzwerk und ohne Bildschirm.

* **`build_fake_syncdb()`** erzeugt echte `tar.gz`-Archive im ALPM-Format, keine
  Attrappen. So fällt eine Formatänderung bei pacman auf.
* **`FakeTransport`** spielt Antwortfolgen ab: `200 → 304`, Ausweichen auf
  einen zweiten Spiegelserver, Netzausfall mit vorhandenem Zwischenspeicher,
  beschädigtes Archiv.
* **`FakeRunner`** zeichnet jede Argumentliste auf — Grundlage der
  Sicherheitstests.
* **Ein Test prüft in einem eigenen Prozess**, dass `core` kein `PySide6`
  hereinzieht.
* **`@pytest.mark.network`** markiert Tests gegen die echten Arch-Server;
  standardmäßig abgewählt.

---

## Was Phase 5 und 6 vorbereitet vorfinden

Diese Felder existieren bereits im Datenmodell, weil sie später sonst einen
Umbau erzwingen würden:

* `boot.modes` mit der XOR-Regel `uefi.systemd-boot` ⊕ `uefi.grub` — mkarchiso
  bricht seit Version 89 ab, wenn beide gesetzt sind.
* `live.cow_spacesize` — der archiso-Standard von 256 MB reicht für eine
  KDE-Live-Sitzung nicht; Vorgabe ist deshalb 2 GB.
* `build.compression` — xz mit BCJ braucht auf einem KDE+Steam-Dateisystem
  leicht 20–40 Minuten. Für Iterationen ist zstd nötig.
* `file_permissions_extra` als **akkumulierbare** Struktur: Benutzer-, Branding-
  und Installer-Erzeugung schreiben unabhängig hinein. Ein statisches Template
  würde in Phase 8 kollidieren.
* `semantics.kernel_suffix` — Preset-Dateiname (`linux-zen.preset`) und alle
  Bootmenü-Pfade (`vmlinuz-linux-zen`) müssen aus derselben Variable kommen.

### Fortschrittsanzeige (Phase 6)

`mkarchiso` braucht zwingend `-v`; ohne den Schalter gibt es keine einzige
INFO-Zeile und damit keinerlei Fortschrittsinformation. Die Ausgabe folgt dem
Muster `[mkarchiso] INFO: <text>` und ist immer englisch (`LC_ALL=C.UTF-8` wird
intern gesetzt).

Verwertbare Marken, in Reihenfolge: `Copying custom airootfs files...` →
`Installing packages to '` (längste Phase) → `Done! Packages installed
successfully.` → `Creating SquashFS image...` → `Creating ISO image...` →
`Done!`

Zwei Fallstricke: Die Ausgabe muss an `\n` **und** `\r` getrennt werden, sonst
sind die Fortschrittszeilen von `mksquashfs` und `xorriso` unsichtbar. Und
`Creating a list of installed packages on live-enviroment...` enthält einen
Tippfehler in der Originalquelle — nur auf das Präfix matchen.

---

## Einen neuen Katalogeintrag hinzufügen

Beispiel: ein zusätzlicher Browser.

```yaml
# src/archcustomiser/data/catalog/categories/60-apps.yaml, unter options:
  - id: librewolf
    group: browser
    order: 15
    label: "LibreWolf"
    description: "Auf Datenschutz ausgerichteter Firefox-Abkömmling."
    packages: [librewolf]
```

Fertig. Kein Python, kein Neustart der Entwicklung. Der Name wird beim nächsten
Öffnen der Paketseite automatisch gegen die echten Repositories geprüft.

Eine ganze neue Kategorie ist eine neue Datei mit `category:` und `options:` —
mit eindeutiger `step`-Nummer, weil daraus die Seiten-ID im Wizard wird.

Benutzer können den Katalog erweitern, ohne ausgelieferte Dateien anzufassen:
YAML-Dateien unter `~/.config/archcustomiser/catalog/` werden über `category.id`
und `option.id` eingemischt.

---

## Profilerzeugung (Phase 5)

### Warum ein Baum im Speicher statt direkt auf die Platte

`core/archiso/tree.py` baut das Profil erst als Datenstruktur auf. Drei Gründe,
in der Reihenfolge ihrer Wichtigkeit:

1. **Ein archiso-Profil besteht zu einem Drittel aus Symlinks.** Die lassen sich
   auf NTFS ohne besondere Rechte nicht anlegen — im Speicher dagegen prüfen.
   Ohne diese Zwischenstufe wäre die halbe Phase auf diesem Rechner nicht
   testbar.
2. **Zwei Ausgabewege, ein Codepfad.** `DirectorySink` und `TarSink` sind zwei
   Senken über demselben Baum.
3. **Pfadprüfung an genau einer Stelle.** `add_file`/`add_symlink` sind die
   einzigen Eingänge. Zielpfade stammen aus dem Katalog, also potenziell aus
   einem Benutzer-Overlay.

**Dateimodi stehen bewusst nicht im Baum.** mkarchiso kopiert das airootfs mit
`cp -af --no-preserve=ownership,mode` und verwirft sie. Rechte kommen
ausschließlich aus `file_permissions` in `profiledef.sh` — deshalb sammelt der
Baum sie unter `permissions` und der Generator schreibt sie ganz zuletzt.

### Die Sicherheitsgrenze: profiledef.sh wird ausgeführt

`_read_profile()` in mkarchiso enthält wörtlich:

```bash
. "${profile}/profiledef.sh"
```

Die Datei wird **gesourced**, nicht gelesen. Jeder unmaskierte Benutzertext
darin ist Codeausführung mit den Rechten des bauenden Benutzers. Ein
Herausgeber-Feld

```
Jason"; rm -rf ~; echo "
```

würde beim nächsten Build ausgeführt.

`core/archiso/quoting.py` ist die einzige Stelle, die Werte nach Bash überträgt.
Sie benutzt `shlex.quote` (POSIX-korrekte einfache Anführungszeichen) und lehnt
Steuerzeichen ab — ein Zeilenumbruch könnte sonst eine eigene Zuweisung
eröffnen. Im ganzen Modul steht kein einziges direktes Einsetzen in Bash-Syntax.

Der Test `test_sourcing_preserves_the_value_without_executing` ist der Beweis:
er erzeugt die Datei, liest sie mit dem echten `bash` ein — genau wie mkarchiso
— und prüft zweierlei. Der Wert muss wörtlich erhalten sein, und die
Marker-Datei, die der eingebettete Befehl anlegen würde, darf nicht existieren.

**`declare -A` nicht vergessen.** Ohne die Deklaration liest Bash
`[/etc/shadow]` als Rechenausdruck und bricht ab. mkarchiso deklariert die
Variable zwar selbst (Zeile 47), aber dann ließe sich die erzeugte Datei nicht
mehr eigenständig prüfen — deshalb schreibt der Generator sie mit.

### Benutzeranlage ohne /etc/group anzufassen

pacman behandelt `/etc/group` als `backup`-Datei: eine eigene Fassung verdrängt
die Standardgruppen (`root`, `tty`, `disk` …), die Paketfassung landet nur als
`.pacnew` daneben. archiso/releng liefert deshalb **kein** `/etc/group` — und
auch nur eine einzige Zeile in `/etc/passwd`.

Dass das reicht, liegt an `systemd-sysusers`: der pacman-Hook erzeugt während
`pacstrap` alle Systemkonten aus den `sysusers.d`-Dateien der Pakete. Genau
diesen Mechanismus benutzt der Generator für die Gruppenzugehörigkeit:

```
airootfs/usr/lib/sysusers.d/10-archcustomiser.conf
    m jason wheel
```

Die Direktive `m` heißt laut Handbuch „add a user to a group, creating both
implicitly if needed". Rein deklarativ, keine Shell im Chroot, hier vollständig
prüfbar. Ein `u`-Eintrag wäre falsch — der legt ein *gesperrtes* Systemkonto an.

Die Benutzerzeile bleibt trotzdem in `/etc/passwd`, weil mkarchiso daraus das
Home-Verzeichnis ableitet (`_make_customize_airootfs`: UIDs 1000–59999,
`install -d -m 0750`, `/etc/skel` kopieren, rekursiv chownen).

### Passwort-Hash ohne das crypt-Modul

Python hat `crypt` in 3.13 entfernt (PEP 594). Die Kaskade in
`core/archiso/users.py`:

1. **`ctypes` gegen `libcrypt.so.2`**, mit `crypt_gensalt_rn` + `crypt_rn`.
   Kein Subprozess, keine Pipe, kein argv — der Klartext verlässt den Prozess
   nie. `crypt_gensalt_rn(NULL, 0, NULL, 0, …)` liefert das beste verfügbare
   Verfahren mit Salt aus der Zufallsquelle des Systems; auf Arch ist das
   yescrypt, also genau das, was `passwd(1)` dort erzeugen würde.
   Bewusst `crypt_rn` statt `crypt`: threadsicher, eigener Puffer, und Fehler
   kommen als `NULL` statt als String mit `*` am Anfang.
2. **Eigene sha512crypt-Rechnung** (`core/archiso/sha512crypt.py`) — reines
   `hashlib` nach der SHA-crypt-Spezifikation, gegen deren Testvektoren *und*
   gegen echtes `openssl passwd -6` geprüft.

Früher stand auf Stufe 2 ein `openssl passwd -6`-Subprozess. Der lieferte
dasselbe Ergebnis, war aber an ein fremdes Programm gebunden: macOS liefert
LibreSSL, das den Schalter `-6` gar nicht kennt, und unter Windows gibt es
openssl nur zufällig über Git for Windows. Wo beides fehlte, wurde das Konto
**gesperrt** angelegt.

Die eigene Rechnung ist deshalb nicht nur portabler, sondern auch sicherer: der
Klartext verlässt den Prozess gar nicht mehr. `users.py` importiert `subprocess`
inzwischen nicht einmal — ein Test hält das fest.

Der Import steht in der Funktion, nicht auf Modulebene: unter Windows gibt es
libcrypt nicht, das Modul muss aber importierbar bleiben.

`hashing_available()` liefert seither überall `True`; die Funktion bleibt, weil
die Oberfläche sie abfragt und ein künftiger Zweig sie wieder verneinen könnte.

### Kernel-Kopplung

Der Kernelname steuert drei Stellen, die zusammenpassen müssen:

| Stelle | bei `linux-zen` |
|---|---|
| Preset-Dateiname | `airootfs/etc/mkinitcpio.d/linux-zen.preset` |
| `ALL_kver` darin | `/boot/vmlinuz-linux-zen` |
| jeder Bootmenü-Eintrag | `vmlinuz-linux-zen`, `initramfs-linux-zen.img` |

Stimmen sie nicht überein, baut mkinitcpio ein Abbild **ohne** die
archiso-Hooks, und die ISO startet nicht. Alle drei kommen deshalb aus einer
Variablen (`semantics.kernel_suffix` aus dem Katalog), und ein
parametrisierter Test prüft alle drei Kernel.

### Bootloader-Vorlagenmarken

mkarchiso ersetzt `%INSTALL_DIR%`, `%ARCH%`, `%ARCHISO_LABEL%` und
`%ARCHISO_UUID%` beim Bauen — sie bleiben im Profil wörtlich stehen. Sie vorab
einzusetzen wäre falsch: `%ARCHISO_UUID%` ist der Zeitstempel des Bauvorgangs
und nur mkarchiso bekannt.

Wo **nicht** ersetzt wird, ist ebenso wichtig:

| Datei | Ersetzung |
|---|---|
| `syslinux/*.cfg` | ja |
| `efiboot/loader/entries/*.conf` | ja |
| **`efiboot/loader/loader.conf`** | **nein** — wird mit `install` kopiert |
| `grub/*.cfg` | ja, zusätzlich `%ARCHISO_SEARCH_FILENAME%` |

Eine Marke in `loader.conf` bliebe wörtlich stehen. Die Selbstprüfung des
Generators fängt genau das ab — und ignoriert dabei Kommentarzeilen, damit man
in der Datei über Platzhalter schreiben darf.

Zweiter Unterschied: GRUB kann per `-f` selbst prüfen, ob eine Datei existiert,
und blendet den Eintrag sonst aus. syslinux und systemd-boot können das nicht —
dort wird ein Memtest-Eintrag nur geschrieben, wenn das Paket auch in der
Paketliste steht.

### Die Selbstprüfung

`ProfileGenerator._self_check` prüft dieselben Bedingungen wie mkarchiso, nur
schon jetzt: Pflichtdateien, `syslinux` in der Paketliste bei BIOS-Start,
systemd-boot ⊕ GRUB, Kernel-Konsistenz, keine Marken in `loader.conf`.

Diese Fehler beim Erzeugen zu melden ist deutlich freundlicher, als sie den
Benutzer nach dem Kopieren auf ein Arch-System entdecken zu lassen.

### Warum das Archiv unter Windows nicht entpackbar ist

Ein archiso-Profil enthält Symlinks auf absolute Pfade des späteren Systems
(`/usr/lib/systemd/system/sddm.service`). Die existieren zur Erzeugungszeit
nicht — baumelnde Symlinks sind hier der Normalfall.

Unter Windows scheitert `tar` daran, weil es echte Windows-Verknüpfungen
anlegen will und das Ziel fehlt. Gegenprobe: das **offizielle
archiso-Repository** erzeugt beim Entpacken unter Windows exakt dieselben
Meldungen. Das Archiv ist korrekt; nur das Entpacken gehört auf das
Linux-System.

---

## Der erste echte Container-Lauf (07.09.2026)

Der Container war der einzige Bauweg, den der Rechner des Autors nicht
ausführen kann — dort läuft Windows mit einer WSL-Arch-Verteilung. Er stand
deshalb als „durch Tests belegt, aber nie gelaufen" in der Dokumentation.

Die Lösung war naheliegender als gedacht: **WSL2 ist eine echte VM mit einem
echten Linux-Kernel.** Ein Container darin ist keine verschachtelte
Virtualisierung, sondern gewöhnliche Namespace-Trennung — und man ist dort
root, also ist podman rootful. Genau der Fall, den pacstrap braucht.

Der Lauf in fünf Sprossen, jede für sich abbrechbar:

| # | Was | Gemessen |
|---|---|---|
| 0 | Abbild bauen (`build_image()`) | 23 s |
| 1 | `devtmpfs`, `proc`, `sysfs`, `tmpfs` mit `--privileged` | alle vier OK; **ohne** `--privileged` scheitert `devtmpfs` |
| 2 | `pacstrap -c /t base` | 25 s, `PACSTRAP-OK` |
| 3 | `mkarchiso` mit archisos `baseline`-Profil | 1 m 49 s, 489 MB |
| 4 | Der Programmweg über `BuildController` | 203 s, **1311 MB** |

Sprosse 1 ist die wichtigste: sie entscheidet die Behauptung, um die sich das
ganze Modul dreht. Die Gegenprobe ohne `--privileged` gehört dazu — ohne sie
wüsste man nur, dass es geht, nicht ob der Schalter etwas dazu beiträgt.

Die ISO aus Sprosse 4 ist zeichengleich mit der über WSL gebauten: 1311 MB,
`CD001`, `0x55AA`, `MINIARCH_1_0`.

### Vier Fehler, die nur ein echter Lauf zeigen konnte

Die Attrappentests decken die **Form** der Aufrufe lückenlos ab und ihre
**Wirkung** überhaupt nicht. Das ist für Attrappen richtig — es heißt nur, dass
die Testzahl nichts über die Funktionsfähigkeit aussagt.

1. **Das Containerfile erreichte die Engine nie.** `--file -` heißt „von
   stdin", übergeben wurde nichts. Behoben über ein Wegwerfverzeichnis.
2. **`ensure_image()` rief niemand.** Ohne Abbild scheitert `podman run` an
   einem `localhost/`-Namen, der nicht aus dem Netz geholt wird.
3. **Docker wurde grundsätzlich für tot erklärt.** Die Rootless-Frage stellte
   `{{.Host.Security.Rootless}}` — podman-Vokabular. Dockers `info` kennt kein
   Feld `Host`, die Vorlage scheitert, der Rückgabecode ist ungleich null, und
   die Meldung lautete „docker laeuft nicht". Damit war macOS immer tot.
   Zwei Lehren stecken darin: je Engine die passende Frage stellen, und eine
   unbeantwortbare **Neben**frage nie als Ausfall der **Haupt**sache deuten.
4. **Dem Abbild fehlte `grub`.** archiso zieht es nicht mit, der Bootmodus
   `uefi.grub` braucht es. Die Vorabprüfung hätte gesagt „das Abbild muss neu
   gebaut werden" — und dabei wäre wieder eines ohne grub entstanden. Eine
   Sackgasse für zwölf Megabyte. Ein Test hält jetzt fest, dass alles aus
   `CONDITIONAL_TOOLS` im Containerfile steht.

### Aus einer Zusicherung wurde eine Prüfung

`can_mount_privileged()` hängt `devtmpfs` im Container ein und wieder aus. Zwei
Sekunden. `devtmpfs` ist der richtige Prüfstein, weil es im Kernel kein
`FS_USERNS_MOUNT`-Flag hat und in einem Benutzer-Namensraum grundsätzlich nicht
einhängbar ist.

Vorher stand an dieser Stelle in der Vorabprüfung eine Behauptung („der
Container läuft privilegiert"). Auf einem normalen Ubuntu läuft podman als
normaler Benutzer aber rootless, und dort wirkt `--privileged` nur *innerhalb*
des Benutzer-Namensraums. Der Bau lief dann bis pacstrap und starb dort am
ersten Mount — nach Minuten, mit einer Meldung, die niemand deuten kann. Jetzt
blockiert die Vorabprüfung mit dem Befehl, der hilft.

### Was weiterhin offen ist

docker als Engine, Fedoras SELinux-Kennzeichnung `:Z`, macOS. Der Ablauf unter
`.github/workflows/container.yml` deckt die ersten beiden Punkte ab, sobald ihn
jemand auslöst — er läuft bewusst nur auf Knopfdruck.

---

## Durchsicht vom 07.09.2026

Zwanzig gemeldete Punkte, jeder am Quelltext geprüft. **Zwei waren falsch** —
und die Widerlegungen sind wertvoller als die Reparaturen, weil sie sonst
irgendwann erneut „behoben" würden:

* **`de_DE.UTF-8.UTF-8`.** Gemeldet als doppelte Endung im archinstall-Locale.
  Der Code hängt aber nirgends `sys_enc` an `sys_lang` an, und archinstalls
  `set_locale()` trennt ausdrücklich am Punkt: aus `de_DE.UTF-8` wird
  `lang='de_DE'`, `encoding='UTF-8'`, und der erzeugte Ausdruck
  `#de_DE(\.UTF-8)? UTF-8` trifft die Zeile in `/etc/locale.gen`. Beide
  Schreibweisen funktionieren.
* **`"$@"` unter `set -u` in bash 3.2.** Den Abbruch gab es **nur in bash
  4.0.x**; Chet Ramey baute ihn in 4.0 ein und nahm ihn in 4.1-alpha wieder
  heraus, nachdem die Austin Group klarstellte, dass `$@` und `$*` ausgenommen
  sind. bash 3.2 (die Fassung, die macOS als `/bin/sh` ausliefert) ist *älter*
  als dieser Einbau und kennt den Fehlerpfad nicht. `${1+"$@"}` schützt gegen
  ein anderes, vor-POSIX-Problem und ist hier unnötig.

### Der Container-Weg konnte nie funktionieren

Zwei voneinander unabhängige Fehler, die sich gegenseitig verdeckten:

1. `build_image()` rief `podman build --file - .` auf — „Containerfile von
   stdin". Übergeben wurde es nie: `_run()` kennt kein `input=`. Die Engine
   erbte damit das stdin des Programms und wartete aus einem Terminal bis zum
   Zeitlimit von 1800 s, aus der Oberfläche las sie sofort EOF.
2. `ensure_image()` wurde im ganzen Programm von niemandem gerufen. Die
   Vorabprüfung meldete „wird beim ersten Bau erzeugt" — ein Versprechen, das
   niemand einlöste.

Beides ist behoben; das Containerfile geht jetzt über ein Wegwerfverzeichnis,
das zugleich der Baukontext ist (vorher `.`, also der ganze Projektordner).
`ContainerError` erbt seither von `BuildError`, weil `controller.run` nur
diesen fängt — und daran hängt das Schreiben der Protokolldatei.

### Wo Anzeige und Ergebnis auseinanderliefen

`SelectionStore.replace_config()` trug beim Laden eines Profils keine
Katalogvorgaben nach, `reset()` dagegen schon. Ein Formularfeld ohne Wert zeigt
die Vorgabe an — die Konfiguration blieb leer. Bei `minimal.yaml` sah der
Benutzer deshalb den Benutzer „arch", und die erzeugte ISO hatte gar kein
Konto; bei gesperrtem Root-Konto also **niemanden, der sich anmelden konnte**.
Seither füllt `replace_config` fehlende Felder nach (nur fehlende — ein leerer
Wert im Profil kann gewollt sein), und `airootfs.py` meldet den Fall
ausdrücklich, wenn er trotzdem eintritt.

### Multilib: gefunden, aber nicht installierbar

Der Paketindex lädt `multilib` mit, ein eingetipptes `steam` galt also als
gefunden. Die erzeugte `pacman.conf` kannte das Repository nicht, und der Bau
scheiterte erst Minuten später in pacstrap. `config.extra_repositories` wird
jetzt aus der Paketprüfung abgeleitet (`validator.repositories_of`), im Profil
gespeichert und vom Resolver in `pacman.conf` **und** `archinstall.json`
durchgereicht.

### Ein Teilindex ist kein Index

Fiel beim Laden ein einzelnes Repository aus, meldete `PackageService`
trotzdem „alles gut" — und jedes Paket aus dem fehlenden Repository galt als
`NOT_FOUND`: rot, blockierend, „in den offiziellen Repositories nicht
gefunden". Genau der Fehler, den Entscheidung 3 oben verhindern soll. Jetzt
wird gegen `config.repos` abgeglichen; fehlt etwas, gilt der Index als
`degraded`, und die Prüfung liefert `UNVERIFIED` statt `NOT_FOUND`.

---

## Der ISO-Build (Phase 6)

### Die Lastgrenze: ein Bau bekommt nie den ganzen Rechner

Am 03.09.2026 hat ein Bau über WSL einen Windows-Rechner (12 Kerne, 15,4 GB)
vollständig unbedienbar gemacht — kein Fenster ließ sich mehr verschieben, der
Abbrechen-Knopf war nicht erreichbar, nur ein harter Neustart half. Die letzte
Protokollzeile war `Parallel mksquashfs: Using 12 processors`.

`core/build/limits.py` hält die Regel: **die Hälfte der Kerne, mindestens
einer, nie alle.** Umgesetzt wird sie in `wrap()` jedes Ziels — `taskset -c
0-N` bei `LocalTarget` und `WslExecutionTarget`, `--cpus` bei
`ContainerExecutionTarget`.

Vier Wege wurden geprüft und drei verworfen:

* **`-processors` in `COMPRESSION_PRESETS`** (`archiso/settings.py`) — verworfen.
  Die Presets wandern unverändert in `profiledef.sh`, und das Profil ist
  exportierbar: eine auf zwölf Kernen ermittelte Zahl landete in einem Profil,
  das jemand auf vier Kernen baut. Außerdem müsste `build_settings` den
  Zielrechner befragen — und es läuft in der Vorschau der Zusammenfassungsseite
  **auf dem GUI-Faden**. Eine Reparatur gegen ein Einfrieren, die ein
  Einfrieren einbaut.
* **`nice`/`ionice` in der Verteilung** — verworfen, weil wirkungslos. `nice`
  ordnet Prozesse innerhalb *einer* Planungsdomäne; in der WSL-Maschine läuft
  außer dem Bau nichts, dem er weichen könnte, und der Bedarf, den `vmmem` beim
  Windows-Planer anmeldet, bleibt unverändert. Nur eine echte Obergrenze senkt
  die Nachfrage.
* **`-mem` bei mksquashfs** — verworfen als Hauptmittel. Der Schalter begrenzt
  nur dessen eigene Caches (Vorgabe 25 % des sichtbaren Speichers, auf dem
  betroffenen Rechner gemessen: 1903 MB). Was den Speicher wirklich füllt, ist
  der Seiten-Cache des Gastkernels. Weniger Fäden senken den Durchsatz und
  damit auch diesen Druck — der Kernschnitt wirkt auf beides.
* **`taskset` im `wrap()` des Ziels** — gewählt. Dort ist der Zielrechner
  bekannt, Abfragen laufen im Bau-Faden, und die Bindung erbt sich über
  `execve` durch den ganzen Prozessbaum: pacstrap, pacman, mksquashfs und
  xorriso gleichermaßen. Profil, Export und Oberfläche bleiben unberührt.

Belegt, nicht vermutet — auf dem betroffenen Rechner gemessen:

```
mksquashfs …                 →  Parallel mksquashfs: Using 12 processors
taskset -c 0-5 mksquashfs …  →  Parallel mksquashfs: Using 6 processors
```

`taskset` steht dabei **vor** `pkexec`, nicht dahinter: sonst sähe polkit
`taskset` als das auszuführende Programm, und eine Regel, die nur `mkarchiso`
erlaubt, bräche stillschweigend.

### Warum der Abbruch nach dem Arbeitsverzeichnis sucht

`pkill -TERM -f mkarchiso` war falsch — und zwar auf eine Art, die niemandem
auffiel: `mkarchiso` ist nur das rufende Bash-Skript. Die Last erzeugt sein
Kind `mksquashfs`, dessen Befehlszeile das Wort „mkarchiso" **nicht** enthält.
Der Abbruch ging ins Leere, während die Oberfläche „Abgebrochen" meldete.

`WslExecutionTarget.kill_pattern()` verwendet stattdessen das
Arbeitsverzeichnis. Es steht in der Befehlszeile *jedes* beteiligten Prozesses
und ist zugleich eng genug, dass ein `pacman` des Benutzers in derselben
Verteilung unbehelligt bleibt. Die Punkte im Pfad (`/root/.cache/…`) werden
maskiert, sonst träfe der Ausdruck mehr als gemeint. Nach `TERM` wird mit
`pgrep` nachgesehen; erst dann folgt `KILL`, und überlebt trotzdem etwas, sagt
das Protokoll den Befehl zum Nachhelfen.

### Warum der Abbruch neben der Oberfläche läuft

Ein Abbruch kostet beim WSL-Ziel mehrere `wsl.exe`-Aufrufe mit je 30 s
Zeitlimit plus die Frist. Bis zum 03.09.2026 lief das alles im Oberflächenfaden
— genau verkehrt herum, denn der Knopf wird gedrückt, *weil* der Rechner
überlastet ist. `BuildJob.cancel()` startet dafür jetzt `_CancelThread` und
kehrt sofort zurück; ein zweiter Klick startet keinen zweiten Faden.

### Ohne `-v` gibt es keinen Fortschritt

`mkarchiso` setzt intern `quiet="y"` als Vorgabe; `_msg_info()` schreibt dann
gar nichts. Ohne den Schalter liefe ein Prozess vierzig Minuten schweigend.
`MkarchisoRunner.build_argv()` setzt ihn deshalb immer — ein Test hält das fest.

### Warum byteweise gelesen wird

`mksquashfs` und `xorriso` beenden ihre Fortschrittszeilen mit **Wagenrücklauf**,
nicht mit Zeilenumbruch. Ein `for line in process.stdout` blockiert dann genau
während der beiden längsten Bauphasen und liefert am Ende alles auf einmal.

Der Runner liest deshalb Blöcke und trennt selbst an `\r` **und** `\n`. Ein
unvollständiger Rest bleibt im Puffer, bis das nächste Stück kommt — sonst
erschiene eine mitten durchgeschnittene Zeile zweimal.

### Die Fortschrittsmarken sind belegt, nicht geraten

`STAGES` in `core/build/progress.py` bildet die Reihenfolge aus
`_build_iso_base()` und `_build_iso_image()` ab. Die Gewichtung folgt der
tatsächlichen Dauer: `pacstrap` und die Kompression machen zusammen über zwei
Drittel aus.

Zwei Details, die man beim Nachbau falsch macht:

* `Creating a list of installed packages on live-**enviroment**...` enthält
  einen Tippfehler im Original. Verglichen wird deshalb nur das Präfix.
* Die Zählerzeilen von pacman sind **eingerückt** (` (  1/312) downloading`).
  Ein am Zeilenanfang verankertes Muster greift nie — und der Balken stünde
  während des längsten Abschnitts still. Genau das hat ein Test aufgedeckt.

Der Fortschritt ist **monoton**: eine spätere Zeile kann ihn nie zurückdrehen.
Ein zurückspringender Balken sieht nach einem Fehler aus, auch wenn alles
stimmt.

### `-r` wird nicht verwendet

Der Schalter löscht das Arbeitsverzeichnis, aber schon *während* der
ISO-Erzeugung Teile davon — und er verweigert die Arbeit, wenn das Verzeichnis
vorher existierte. Aufgeräumt wird stattdessen selbst, nach Auswertung des
Ergebnisses, und ein misslungenes Aufräumen macht einen erfolgreichen Build
nicht nachträglich zum Fehlschlag.

### Vorabprüfung statt später Enttäuschung

Ein Build dauert zwanzig Minuten bis eine Stunde. `core/build/preflight.py`
prüft vorher alles ohne Ausführung Feststellbare und sammelt **alle** Befunde,
statt beim ersten abzubrechen — wer nach jeder Korrektur den nächsten Fehler
bekommt, gibt beim dritten Mal auf.

Geprüft wird unter anderem der Dateisystemtyp des Arbeitsverzeichnisses (aus
`/proc/mounts`, längster passender Einhängepunkt). Auf NTFS oder exFAT scheitert
der Build sonst mitten im Entpacken, weil Eigentümer und Rechte verlorengehen.

### Threading

Der Build bekommt einen **eigenen `QThread`**, keinen `QThreadPool`-Auftrag wie
der Profilexport: er läuft eine halbe Stunde und würde sonst dauerhaft einen
Platz im gemeinsamen Vorrat belegen.

Die Ausgabezeilen werden gesammelt und alle 120 ms gebündelt weitergereicht.
Während `pacstrap` kommen mehrere Zeilen pro Sekunde; jede einzeln durch die
Signalwarteschlange und in ein Textfeld zu schieben lässt die Oberfläche
sichtbar stocken.

### Wie das ohne Arch getestet wird

`tests/fake_mkarchiso.py` ist ein eigenständiges Programm, das aufgezeichnete
Ausgabe im echten Format erzeugt — einschließlich der `\r`-Fortschrittszeilen.
Es wird als **echter Prozess** gestartet, nicht gemockt: Puffern, Signale und
Abbruch sind genau die Stellen, an denen Fehler stecken, und ein Mock würde sie
alle überspringen.

Umgebungsvariablen steuern die Fehlerfälle: `FAKE_FAIL_AT` bricht nach einer
bestimmten Marke ab, `FAKE_NO_ISO` meldet Erfolg ohne Ergebnisdatei,
`FAKE_SLOW` verlangsamt den Lauf für den Abbruchtest.

**Was das nicht ersetzt:** einen echten Build. Die Ausgabe ist nachgebildet, das
Format belegt — aber ob `pacstrap` die erzeugte Paketliste akzeptiert und ob die
fertige ISO bootet, zeigt erst der erste Lauf auf Arch.

---

## Bauen über WSL

Damit lässt sich auf einem Windows-Rechner eine ISO erzeugen — ohne zweiten
Computer und ohne Dual-Boot.

### Zwei Eigenheiten von `wsl.exe`

**Die eigenen Meldungen kommen in UTF-16-LE.** `--status`, `--list` und jede
Fehlermeldung des Verwaltungswerkzeugs. Die Ausgabe eines *aufgerufenen*
Linux-Programms (`wsl -e …`) ist dagegen normales UTF-8 — beides muss
unterschiedlich behandelt werden.

Unterschieden wird an den **Bytes**, nicht am Ergebnis: reiner ASCII-Text in
UTF-8 lässt sich ebenfalls als UTF-16 dekodieren, nur eben zu Unsinn (aus
„Hallo" würden zwei chinesische Zeichen). Verlässlich sind die Nullbytes — in
UTF-16-LE ist bei lateinischem Text etwa jedes zweite Byte null, in UTF-8
keines.

**Die Exit-Codes unterscheiden sich je Unterbefehl.** Nur `--status` meldet mit
dem eindeutigen Code 50, dass WSL gar nicht eingerichtet ist; `--list` gibt im
selben Fall bloß eine 1 zurück, die sich nicht von „keine Verteilung vorhanden"
unterscheiden ließe. Deshalb wird zuerst `--status` gefragt.

Auf Textmeldungen wird nie geprüft — sie sind übersetzt. Die Verteilungsliste
wird über ihre *Struktur* gelesen (Stern = Standard, letzte Spalte = Version),
nicht über die Spaltenüberschriften.

### Der entscheidende Kniff: Übertragung als Archiv

Naheliegend wäre, das Profil auf ein Windows-Laufwerk zu schreiben und in WSL
über `/mnt/e/…` darauf zuzugreifen. Das geht schief: unter `/mnt` liegt ein
Windows-Dateisystem, und dort gibt es weder symbolische Verknüpfungen noch
Linux-Dateirechte. Ein archiso-Profil besteht zu einem Drittel aus
Verknüpfungen — der Build bräche mitten im Kopieren ab.

Deshalb wandert ein **tar-Archiv** hinüber, in dem Verknüpfungen bloße
Metadaten sind, und wird *innerhalb* des Linux-Dateisystems ausgepackt. Danach
zählt `find -type l` nach, ob tatsächlich alle Verknüpfungen angekommen sind —
fehlen welche, wird abgebrochen, statt eine ISO ohne aktivierte Dienste zu
bauen.

Aus demselben Grund liegen Arbeits- und Ausgabeverzeichnis in Linux; erst die
fertige ISO wird per `cp` nach Windows kopiert. Der Weg über den Netzwerkpfad
`\wsl$\…` wäre deutlich langsamer.

### `ExecutionTarget` — wo gebaut wird

`core/build/targets.py` enthält ein Protokoll aus zwölf Methoden, das **alles**
kapselt, was sich zwischen den Bauwegen unterscheidet: der Aufruf, die
Verzeichnisse, wie das Profil hinkommt, wie geprüft, aufgeräumt und abgebrochen
wird. Drei Umsetzungen:

| Ziel | Datei | wann |
|---|---|---|
| `LocalTarget` | `targets.py` | Arch mit archiso |
| `WslExecutionTarget` | `targets.py` + `wsl.py`, `wsl_build.py` | Windows |
| `ContainerExecutionTarget` | `targets.py` + `container.py` | jedes andere Linux, macOS |

**Der Controller kennt den Zieltyp nicht.** Er fragte früher an drei Stellen per
`isinstance` und griff viermal auf `target.wsl` durch. Bei zwei Zielen waren das
drei Sonderfälle, bei einem dritten wären es neun geworden — deshalb wanderten
`prepare`, `deliver_profile`, `discard`, `preflight` und `cancel_run` ins
Protokoll. Ein Test in `tests/test_targets.py` hält fest, dass keine
`isinstance`-Abfrage zurückkehrt.

**`available_targets()`** wählt nach Fähigkeit, nicht nach `sys.platform`. Der
Benutzer wählt nichts; er sieht einen Satz, welcher Weg genommen wird.

**Zum Container:** er läuft mit `--privileged` — nicht wegen mkarchiso, sondern
wegen `pacstrap`, dessen `chroot_setup` acht Dateisysteme einhängt und
`CAP_SYS_ADMIN` braucht. Rootless scheitert an `devtmpfs`, das im Kernel kein
`FS_USERNS_MOUNT`-Flag hat. Beim Bind-Mount sind Host- und Containerpfad
identisch, weshalb mehrere Methoden wörtlich die von `LocalTarget` sind.

**Abbrechen** muss jedes Ziel selbst: `wsl.exe` leitet keine Signale weiter, ein
`terminate()` tötet nur den Windows-Client, während mkarchiso drüben
weiterläuft. Bei podman träfe es den Client statt des Containers.

**Achtung bei Pfaden:** Im WSL-Fall sind alle Pfade Linux-Pfade und dürfen
nicht durch `pathlib.Path` laufen. Unter Windows würde aus `/home/jason` sonst
`\home\jason`. Der Runner führt sie deshalb als Text. Ein Test hält das fest.

### Was nicht automatisch passiert

`wsl --install` braucht Administratorrechte und einen Neustart des Rechners.
Das ist eine Entscheidung des Benutzers, keine, die ein Programm im Hintergrund
treffen sollte. Der Dialog zeigt deshalb die Befehle zum Kopieren und erkennt
danach selbst, dass alles bereitsteht.
