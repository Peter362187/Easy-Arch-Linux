## 1. [critical/qt-lifetime] motion._laufend never releases an animation that is destroyed instead of finished — stop_all() then calls stop() on a deleted C++ object
DATEI src/archcustomiser/gui/motion.py:133
BESCHREIBUNG animate() registers every QVariantAnimation in the module-level set _laufend and removes it again only from the finished handler (motion.py:128-134). Qt emits QAbstractAnimation::finished only when an animation runs to its end; it is NOT emitted when the animation is stopped, and NOT emitted when the animation object is destroyed. Since the animation is parented to its target widget (animation = QVariantAnimation(ziel), line 121), deleting the target mid-animation destroys the animation silently and leaves a PySide wrapper of a dead C++ object permanently in _laufend.

The codebase deletes animation targets mid-animation in several places, most reliably in AnimatedStack (page_stack.py:104-107): _abbrechen() calls self._laeuft.deleteLater() without stopping the running animation, and it is called both from resizeEvent and from the top of set_current() itself (line 79). Two page changes in
FIX The motion.py half of the proposed fix is correct and I verified it works; the AnimatedStack half is based on a wrong assumption and must be restated.

1) motion.py — deregister on destruction (verified: the `destroyed` handler can still hash/discard the wrapper in PySide6 6.11):

    def _austragen(*_: object) -> None:
        _laufend.discard(animation)

    animation.finished.connect(abschluss)
    animation.destroyed.connect(_austragen)
    _laufend.add(animation)

Optionally also discard on `stateChanged` when the new state is Stopped, so `active_count()` is exact in the window between an explicit `stop()` and the deferred delete rather than merely self-correcting one event-loop pass later.

2) motion.stop_all() — make it defensive; it runs after arbitrary widget teardown:

    for animation in list(_laufend):
        try:
            animation.stop()
        except RuntimeError:
            pass
    _laufend.clear()

(try/except beats `shiboken6.isValid` here — no extra import, and it also covers an object dying between the check and the call.)

3) motion.run() has the identica

## 2. [critical/threading] closeEvent ignores the return value of job.wait() and destroys running QThreads
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/main_window.py:417
BESCHREIBUNG closeEvent cancels the build and calls job.wait(30000), but discards the boolean result and unconditionally continues to event.accept(). 30 s is demonstrably too short: BuildController's cancel handler alone waits up to CANCEL_WAIT_SECONDS = 120 s for _cancel_done (controller.py:56, 278), and WslExecutionTarget.cancel_run needs pkill(30 s) + grace(8 s) + pgrep(30 s) + pkill(30 s) + pgrep(30 s) ~ 128 s in the worst case (targets.py:728-788). BuildJob.wait() also waits the two threads sequentially, so a full 30 s can be burned on _thread before _cancel_thread is even looked at (build_worker.py:227-235). The threads are children of BuildJob -> BuildFlow -> MainWindow, and app.run() drops the MainWindow reference as soon as app.exec() returns (app.py:57-77), so Qt deletes a still-running QThread.
FIX The direction is right (never `event.accept()` while `job.busy`), but the proposed fix has two problems: `job.wait()` must not be called on the GUI thread at all, and repeating the close must not re-ask.

In `MainWindow.closeEvent`:
- Remove the `job.wait(30000)` call entirely. Blocking the GUI thread for up to 60 s is precisely the freeze that `_CancelThread` was introduced to avoid (see its docstring, build_worker.py:106-118) — the window would be "keine Rueckmeldung" under Windows during the very shutdown it is trying to make clean.
- Ask the "Bau laeuft noch / trotzdem beenden?" question and `darf_beenden()` **once**, guarded by a `self._beendet_wird = False` flag; on confirmation call `job.cancel()`, put the window into a visible "Abbruch laeuft noch ..." state (disable the central stack / start button so no second build can be started, and show it in the build page headline), then `event.ignore()`.
- Start a short `QTimer` owned by the window (e.g. 250 ms, `setSingleShot(False)`, created lazily and stopped in the callback) that calls `self.close()` again as soon as `self.build_

## 3. [critical/secrets] GUI startet nicht: Property animationen_reduzieren wird als Funktion aufgerufen
DATEI src/archcustomiser/gui/app.py:48
BESCHREIBUNG `motion.set_reduced(settings.animationen_reduzieren())` ruft ein `@property` mit `()` auf. `Settings.animationen_reduzieren` (settings.py:96-108) ist eine Property und liefert bereits `bool`; der zusaetzliche Aufruf wirft `TypeError: 'bool' object is not callable`. Empirisch verifiziert (offscreen, echte Settings-Instanz). Der Fehler liegt vor `MainWindow(...)` und `fenster.show()`, also startet die Oberflaeche ueberhaupt nicht -- der Benutzer sieht nur die QMessageBox des Absturzhakens aus `__main__._install_crash_handler`. Kein Test ruft `gui.app.run`, deshalb ist das an der Testsuite vorbeigelaufen.
FIX Der vorgeschlagene Fix ist korrekt und liegt im Arbeitsbaum bereits vor: `src/archcustomiser/gui/app.py:58` -> `motion.set_reduced(settings.animationen_reduzieren)` (ohne Klammern), plus Rauchtest `tests/test_gui_window.py::test_the_application_actually_starts`. Offen bleibt nur, dass diese Änderung noch nicht committet ist — HEAD (459bcd2) und 9ae8041 enthalten weiterhin den Absturz. Also: Arbeitsbaum-Fix committen; keine weitere Codeänderung nötig (kein zweiter Property-als-Aufruf im Repo).

## 4. [critical/ux-a11y] GUI crashes on startup: property called as a method
DATEI src/archcustomiser/gui/app.py:48
BESCHREIBUNG `motion.set_reduced(settings.animationen_reduzieren())` calls `Settings.animationen_reduzieren`, which is declared with `@property` (settings.py:96-108) and therefore already evaluates to a `bool`. The trailing `()` calls that bool. No test touches `gui.app.run()` (the only reference is the lazy import in `__main__.py:174`), so nothing catches it. Side effect: the whole reduce-motion accessibility path (system setting on Windows/macOS/GNOME, `design/reduce_motion`) never reaches `motion`.
FIX The parens-drop is right and sufficient to stop the crash:

    motion.set_reduced(settings.animationen_reduzieren)

One refinement worth folding in: `motion.set_reduced` stores its argument as a hard override (`motion.py:65-79`), so passing an explicit `False` also disables `is_reduced()`'s own fallbacks — the `QT_QPA_PLATFORM=offscreen` check and the `ARCHCUSTOMISER_MOTION=off` env var. `settings.py` honours `ARCHCUSTOMISER_MOTION` only in `_linux_ruhe()`, so on Windows/macOS that env override silently stops working once this line runs correctly. Either pass `None` when the user has made no explicit choice, letting `motion` decide:

    from .settings import BEWEGUNG_AUTO
    motion.set_reduced(
        None if settings.reduce_motion == BEWEGUNG_AUTO else settings.animationen_reduzieren
    )

(with the system query then belonging in `motion.is_reduced()`'s auto branch), or move the `ARCHCUSTOMISER_MOTION` check in `settings.py` out of `_linux_ruhe()` into `system_bevorzugt_ruhe()` so it applies on all platforms and the simple parens-drop keeps that override alive.

For the regress

## 5. [critical/tests] gui/app.py:run() has no test at all — and it crashes on every start (property called as a method)
DATEI src/archcustomiser/gui/app.py:48
BESCHREIBUNG No test in the suite imports or calls `archcustomiser.gui.app.run` (grep over tests/ finds zero references; test_main.py covers only `__main__.main`'s CLI branches, never the GUI branch). The untested line is broken: `motion.set_reduced(settings.animationen_reduzieren())` — `Settings.animationen_reduzieren` is a `@property` (settings.py:97), verified with `isinstance(Settings.animationen_reduzieren, property) == True`. It returns a bool, and calling a bool raises TypeError. Every other startup step (ThemeManager.apply, detect_environment, MainWindow construction, `zeige_einmal`, `controller.start()`, the deferred `PySide6.QtSvg` import inside widgets/intro.py) is likewise reached by no test. The GUI test suite constructs `MainWindow` directly from fixtures and therefore never walks the real startup path, so a bug that makes the program unable to launch is invisible to 637 green tests.
FIX The one-line fix is correct — drop the parentheses:

    motion.set_reduced(settings.animationen_reduzieren)

The proposed test, as written, will not run. Four concrete corrections:

1. `run()` does `QApplication(argv)` unconditionally (app.py:25). The existing `qapp` fixture (`tests/test_gui_window.py:26-27`) already holds a singleton via `QApplication.instance() or QApplication([])`, so a second construction raises `RuntimeError: Please destroy the QApplication singleton...`. Monkeypatch `archcustomiser.gui.app.QApplication` with a shim whose constructor returns the live instance and whose `exec` returns 0 — patching only `QApplication.exec` is not enough.

2. There is no seam for "inject a tmp QSettings-backed Settings": `run()` builds `Settings()` itself at line 47 with no argument. Either monkeypatch `archcustomiser.gui.app.Settings` to a factory bound to a tmp `QSettings(path, IniFormat)`, or add a real seam — e.g. `def run(argv=None, *, settings=None)` — which is the better change, since the same seam lets the test also drop in a fake `PackageController`.

3. Reset global stat

## 6. [high/parity] Auto-added options show their reason as red error text, and it never clears
DATEI src/archcustomiser/gui/widgets/cards.py:296
BESCHREIBUNG The old OptionWidget kept two channels strictly apart: the auto-reason lived only in the tooltip (`set_auto` -> `setToolTip(reason ...)`), while the red `note` label was reserved for "not available" reasons; the description stayed grey. The new self-drawn OptionCard merges both into one field `_grund`, and `_zeichne_text` paints it with `p.danger` whenever it is non-empty (line 296/301). Two consequences: (1) every automatically added option now replaces its catalog description with a red line "Automatisch ergaenzt, weil X das benoetigt." - an option that is perfectly fine reads as an error; (2) `set_auto(False, "")` (line 96-107) only assigns `_grund` when `auto` is true, so the string survives the transition, and the following `set_availability(True, "")` (line 109-115) returns early because `self._verfuegbar == verfuegbar` and never clears it. The card is then stuck showing a stale au
FIX The proposal is directionally right but its "at minimum" variant is more work than needed and one step is harmful.

Simplest complete fix: stop letting the auto reason into the description slot at all. Then the stale-text symptom disappears by construction, because `_grund` is written by exactly one writer again.

In cards.py, split the fields and never paint the auto reason in `p.danger`:

    self._auto_grund = ""      # nur Tooltip
    self._fehlgrund  = ""      # rote Zeile im Textfeld

    def set_auto(self, auto, grund=""):
        if self._auto == auto and (not auto or grund == self._auto_grund):
            return
        self._auto = auto
        self._auto_grund = grund if auto else ""     # auch bei False zuruecksetzen
        self.setCursor(...)
        self.setToolTip(self._auto_grund or self._fehlgrund or self._tooltip())
        self.update()

    def set_availability(self, verfuegbar, grund=""):
        grund = grund if not verfuegbar else ""
        if self._verfuegbar == verfuegbar and self._fehlgrund == grund:
            return                                   # 

## 7. [high/threading] The build runs a configuration snapshot taken at preflight time; later edits are silently ignored
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/build_flow.py:197
BESCHREIBUNG BuildFlow._vorabpruefung creates the BuildJob with config.copy() at preflight time, and BuildPage keeps that job until the user presses 'ISO erstellen' (pages/build.py:387, 436-469). Navigation is only locked in _bau_starten (pages/build.py:460), so between the preflight and the actual start the user is free to visit any other step and change anything. Nothing invalidates the stored job or the SEITE_PRUEFUNG page: BuildPage does not react to store.resolutionChanged/selectionChanged, and PageBase.enter() only calls sync_from_store(), which BuildPage does not override -- the stack index, the los_button state and the stale report all survive. The isolation of config.copy() itself is correct (config.py:238-250 copies the mutable containers, and store._recompute() replaces the Resolution object rather than mutating it), but the snapshot is taken at the wrong moment. Note that SecretStore is p
FIX The fix must do both halves of the proposal, and the invalidation half needs a guard the proposal omits.

(a) Move the snapshot to the start. In `build_flow.py:_vorabpruefung`, build the job for the preflight but do not treat it as the build job; in `pages/build.py:_bau_starten` construct a fresh `BuildJob(catalog, store.config.copy(), store.resolution(), store.secrets)` (carrying over `job.controller.target` from the preflight job, which is the only thing the flow discovered) before wiring the signals. This is safe because `controller.run()` re-runs the preflight on the new snapshot and raises `PreflightError` early if the changed configuration no longer fits.

(b) Invalidate the displayed report. Connect `store.resolutionChanged` only — it is emitted by `_recompute()` on every mutation path (`toggle`, `set_selection`, `apply_fix`, `set_field`, `set_extra_packages`, `set_provider_choice`, `replace_config`, `reset`, `set_package_report`), so `selectionChanged` is redundant. The slot should drop the stored report/target, disable `los_button` and return the stack to `SEITE_LEER` with "

## 8. [high/threading] After a failed or cancelled build there is no way to start another one
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/pages/build.py:599
BESCHREIBUNG Only the success path reaches SEITE_ERGEBNIS, which is the sole place carrying a reset control ('Neue ISO' -> _zuruecksetzen, line 350/704). _fehlgeschlagen (574) and _abgebrochen (599) leave the stack on SEITE_BAU, a page that contains nothing but the phase list and the log; cancel_button is hidden by _abschliessen. _pruefung_zuruecksetzen (374) is only wired to flow.abgebrochen, and BuildPage.enter() inherits PageBase.enter(), which does not touch self.stapel. Navigating away and back therefore restores the same dead page. The removed BuildDialog could simply be closed after a failure, returning the user to the wizard where the build button was pressable again (wizard.py:646-697) -- that capability is gone, which violates invariant 8.
FIX Take the button variant; drop the "call _zuruecksetzen from _fehlgeschlagen/_abgebrochen" variant — `_zuruecksetzen` switches to SEITE_LEER, which hides the log widget (it lives on SEITE_BAU) and clears `detail` and `headline`, so it would destroy the very error message the user has to read. The reset must be user-triggered, never automatic.

Concretely, in src/archcustomiser/gui/pages/build.py:

1. Build a `self.retry_button` ("Erneut versuchen") in `_aufbauen`, in the same header row (`kopf`) where `cancel_button` sits, `setVisible(False)` initially. The header stays put while the log scrolls — that is exactly why cancel_button was put there — and the retry button then appears in the slot the cancel button vacates. Add a second "Protokoll oeffnen" button next to it (or move/duplicate `protokoll_button` into that row), because after a failure the existing one on SEITE_ERGEBNIS is unreachable.

2. Show it from `_fehlgeschlagen` and `_abgebrochen` only, and hide it again in `_bau_starten` and `_zuruecksetzen`. Do not show it in `_beendet` — SEITE_ERGEBNIS already has "Neue ISO".

3. D

## 9. [high/threading] The running build is killed before the last chance to abort quitting
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/main_window.py:395
BESCHREIBUNG closeEvent asks about the running build first, cancels it and waits for the threads (lines 396-417), and only afterwards calls self.actions_.darf_beenden() (line 419), which can still return False -- the user picks 'Cancel' in the unsaved-work dialog, or picks 'Save' and then aborts the file dialog (actions.py:235-255). event.ignore() then keeps the application open, but the build has already been terminated and its work directory cleaned up. The order puts the irreversible action before the last cancellation point.
FIX Collect every answer first, then perform the irreversible cancel -- and re-check that the job is still busy, because the build can finish while the modal dialogs are open (same race as build.py:733-736):

def closeEvent(self, event) -> None:
    from PySide6.QtWidgets import QMessageBox

    lief = not self.build_page.darf_schliessen()
    if lief:
        antwort = QMessageBox.question(
            self, "Bau laeuft noch",
            "Es wird gerade eine ISO gebaut. Das Programm zu beenden bricht "
            "den Bau ab.\n\nTrotzdem beenden?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if antwort != QMessageBox.StandardButton.Yes:
            event.ignore()
            return

    # Letzte Abbruchmoeglichkeit -- vor dem unwiderruflichen Schritt.
    if not self.actions_.darf_beenden():
        event.ignore()
        return

    if lief:
        job = self.build_page.job
        # Waehrend der modalen Rueckfragen kann der Bau fertig geworden sein.
        if job is not None and job.busy:


## 10. [high/catalog-driven] ISO-Panel zeigt den Kernelnamen doppelt: "linuxlinux-zen"
DATEI src/archcustomiser/gui/widgets/iso_panel.py:164
BESCHREIBUNG `self.kernel.setze(f"linux{resolution.kernel_suffix}")` behandelt `kernel_suffix` als Suffix. Es ist aber der vollstaendige Paketname: `Resolution.kernel_suffix` (core/resolver.py:126-134) liefert `semantics["kernel_suffix"]`, und der Katalog setzt dort `"linux"`, `"linux-lts"`, `"linux-zen"` (30-kernel.yaml). Der Docstring in core sagt das ausdruecklich: "Der Kernelname, z.B. 'linux-zen'". Zugleich ist das ein Verstoss gegen Invariante 2: die GUI setzt einen Paketnamen aus einem Literal ("linux") und einem Katalogwert zusammen, statt den fertigen Wert anzuzeigen. Kein Test deckt den Panel-Text ab.
FIX The proposed fix is correct and complete for the display bug: `self.kernel.setze(resolution.kernel_suffix)`. It is already present verbatim (with a clarifying comment) in the uncommitted working tree at src/archcustomiser/gui/widgets/iso_panel.py:167 — it only needs to be committed.

Two optional follow-ups that would prevent a recurrence:
1. Add a regression test, since none exists: build a store with the linux-zen option selected, call `window.iso_panel.refresh()`, and assert `window.iso_panel.kernel.inhalt.text() == "linux-zen"` (or at minimum `not text.startswith("linuxlinux")`). Place it next to tests/test_gui_window.py:473.
2. The root cause is the misleading name. `Resolution.kernel_suffix` returns a full package name, and the identical logic is duplicated in core/archiso/settings.py:144. Consider exposing a `kernel_name` property (keeping `kernel_suffix` as an alias) and having settings.py delegate to it, so there is one implementation and the name no longer invites a caller to prepend "linux".

## 11. [high/catalog-driven] Branding-Vorschau umgeht die Rollen-Registry und liest "build.include_memtest" hart
DATEI src/archcustomiser/gui/previews/branding.py:369
BESCHREIBUNG `config.field_bool("build.include_memtest")` nennt Kategorie und Feld namentlich -- genau das, was `preview_role` verhindern soll. Dieselbe Datei loest sechs andere Werte korrekt ueber Rollen auf, darunter zwei aus derselben Kategorie `build` (`boot_timeout`, `uefi_bootloader`). Das Feld `include_memtest` in 90-build.yaml traegt als einziges der drei bootrelevanten Felder keine `preview_role`. `field_bool` hat den Vorgabewert `False`, ein Fehlgriff faellt also nicht auf.
FIX Richtung des Vorschlags stimmt, er ist aber unvollstaendig und der Helfer muss korrekt typisieren.

1. 90-build.yaml, Feld `include_memtest`: `preview_role: "include_memtest"` ergaenzen.
2. previews/registry.py, `PreviewContext` um einen Bool-Helfer erweitern -- `bool(self.wert(...))` genuegt nicht, weil Werte aus Profil/YAML als Strings ankommen koennen; die Koerzierung von `BuildConfig.field_bool` (core/config.py:162) spiegeln:

    def flagge(self, rolle: str, vorgabe: bool = False) -> bool:
        wert = self.wert(rolle, vorgabe)
        if isinstance(wert, bool):
            return wert
        if isinstance(wert, str):
            return wert.strip().lower() in ("1", "true", "yes", "ja", "on")
        return bool(wert)

3. branding.py:369: `if k.flagge("include_memtest"):` -- damit faellt die letzte `config.field_*`-Nutzung mit Literal aus der Vorschau weg.
4. Gleich mitnehmen, sonst bleibt die Invariante nur punktuell erfuellt: die bereits deklarierte Rolle `bios_boot` tatsaechlich verwenden und die Bootmenue-Eintraege aus `bios_boot`/`uefi_bootloader` ableiten statt sie fest

## 12. [high/catalog-driven] Bootmenue-Vorschau ignoriert bios_boot und uefi_boot=none -- die Rolle "bios_boot" wird von keiner Vorschau gelesen
DATEI src/archcustomiser/gui/previews/branding.py:368
BESCHREIBUNG Zeile 368/371 setzen die Menueeintraege unbedingt: `[f"{name} (x86_64, UEFI)", f"{name} (x86_64, BIOS)"]` plus "UEFI-Firmwareeinstellungen". Der Generator entscheidet dagegen katalogabhaengig: `derive_bootmodes` (core/archiso/settings.py:108-140) laesst den BIOS-Zweig weg, wenn `build.bios_boot` falsch ist, und den UEFI-Zweig, wenn `build.uefi_boot == "none"`. "UEFI-Firmware-Einstellungen" existiert ausserdem nur im GRUB-Zweig (core/archiso/bootloader.py:251); `_systemd_boot` schreibt keinen solchen Eintrag. Die dafuer noetige Information liegt bereits als Rolle bereit: 90-build.yaml:31 deklariert `preview_role: "bios_boot"` -- diese Rolle wird im gesamten Projekt von niemandem gelesen (Gegenprobe: `grep -rn bios_boot src/` findet nur core und das YAML). Zusaetzlich ist "x86_64" ein Literal, obwohl `config.architecture` (core/config.py:222) den Wert liefert.
FIX In branding.py refresh(), replace lines 367-372 with role-driven entries:

uefi = k.text("uefi_bootloader", "systemd-boot").lower()   # Vorgabe noetig: "" wuerde UEFI verstecken
self.boot.grub = "grub" in uefi
arch = config.architecture                                  # statt Literal "x86_64"
eintraege: list[str] = []
if uefi not in ("", "none"):
    eintraege.append(f"{name} ({arch}, UEFI)")
if k.wert("bios_boot", True):                               # Vorgabe True wie core.field_bool(..., True)
    eintraege.append(f"{name} ({arch}, BIOS)")
if k.wert("memtest", False):
    eintraege.append("Speichertest (memtest86+)")
if self.boot.grub:
    eintraege.append("UEFI-Firmware-Einstellungen")         # nur GRUB schreibt ihn
eintraege += ["Neu starten", "Ausschalten"]                 # syslinux und GRUB schreiben beide
self.boot.eintraege = tuple(eintraege)

Zusaetzlich noetig, was der Vorschlag auslaesst:
1. k.wert/k.text brauchen die Vorgaben (True bzw. "systemd-boot"), weil eine nicht gebundene Rolle None bzw. "" liefert (registry.py:38-53) und die Eintraege sonst faelschlich verschw

## 13. [high/catalog-driven] Ein Tippfehler im Validatornamen deaktiviert die Pruefung lautlos
DATEI src/archcustomiser/core/validation.py:438
BESCHREIBUNG `validate()` gibt bei unbekanntem Namen kommentarlos `OK` zurueck -- ohne Logeintrag. Der Loader prueft `validator` ebenfalls nicht (loader.py:379 liest den String nur ein), obwohl er unbekannte `widget`-Werte (loader.py:358), unbekannte `layout`-Werte, unbekannte Refs und unbekannte Capabilities mit `CatalogError` ablehnt. Die Schwesterregistry verhaelt sich anders: `choices.get_choices` (core/choices.py:148) protokolliert "Unbekannte Auswahlliste %r im Katalog". Betroffen ist unter anderem `validator: distro_name` in 85-branding.yaml -- der Validator, der laut Kopfkommentar der Datei die Arch-Markenrichtlinie durchsetzt.
FIX Loader-seitige Pruefung als Primaerfix (nicht "oder", sondern beides -- die zweite Haelfte deduplizert):

1. In `core/validation.py` nach dem `_REGISTRY`-Literal:
```python
def registry_names() -> frozenset[str]:
    """Die im Katalog erlaubten Validatornamen -- der Loader prueft dagegen."""
    return frozenset(_REGISTRY)
```

2. In `core/catalog/loader.py::_parse_fields`, direkt neben der bestehenden `widget`-Pruefung (Zeile 358), damit es dieselbe Fehlerform hat:
```python
from .. import validation
...
validator = _str(data, "validator", spot)
if validator and validator not in validation.registry_names():
    raise CatalogError(
        f"{spot}: validator {validator!r} unbekannt; "
        f"erlaubt: {sorted(validation.registry_names())}"
    )
```
Das ist der wichtigere Teil, weil es AUCH den zweiten Konsumenten `gui/widgets/fields.py:221` (`VERZEICHNIS_VALIDATOREN`) mit abdeckt, den ein Log in `validate()` nicht erreicht -- und weil es beim Umbenennen einer Funktion in `validation.py` sofort beim Laden knallt statt irgendwann im Log.

Zu beachten: der Loader liest auch Benutzer

## 14. [high/secrets] Splash-Vorschau liest die komplette Benutzerdatei in den Oberflaechenfaden
DATEI src/archcustomiser/gui/previews/branding.py:70
BESCHREIBUNG `_splash_warnung` macht `kopf = Path(pfad).read_bytes()[:32]` -- es liest die ganze Datei und wirft alles ausser 32 Byte weg. Der Pfad kommt aus einem Katalogfeld mit `preview_role: splash` (85-branding.yaml:103), also aus einem frei eingetippten Feld oder aus einem von Hand bearbeiteten Profil. Aufgerufen wird es aus `BrandingPreview.refresh()` (Zeile 373), das an `store.fieldChanged` haengt und nur 80 ms entprellt ist (NEUZEICHNEN_MS). `fieldChanged` feuert bei jedem Feld der Seite -- auch bei `set_secret`. Waehrend `_bild()` einen Cache nach Pfad und mtime hat, hat `_splash_warnung` gar keinen: jeder Refresh liest die Datei erneut, im GUI-Faden. Der Validator `splash_image` prueft nur Existenz und Endung `.png` und blockiert ohnehin nichts, was den Feldwert betrifft.
FIX Der vorgeschlagene Fix ist richtig, aber unvollstaendig -- er behebt nur eine von drei Stellen derselben Klasse in derselben Datei.

(a) `_splash_warnung` wie vorgeschlagen: erst `datei = Path(pfad); if not datei.is_file(): return "Datei nicht lesbar."` (haelt Geraetedateien und FIFOs vom Oeffnen fern, weil ein FIFO schon beim `open` blockiert), dann `with datei.open("rb") as f: kopf = f.read(32)`, und das Ergebnis nach `(str(datei), mtime, st_size)` zwischenspeichern.

(b) `_bild()` (Zeile 39-62) hat dieselbe Luecke fuer dieselben Benutzerpfade (splash, wallpaper, logo): nach `stat()` folgt ungebremst `QPixmap(str(datei))`. Ein 3-GB-"PNG" wird dort komplett gelesen und dekodiert -- im selben GUI-Faden, nur eben in `paintEvent`. Also auch hier `datei.is_file()` und eine Groessenobergrenze; sinnvollerweise `MAX_ASSET_BYTES` aus `core/archiso/branding.py` importieren, damit Vorschau und Generator dieselbe Grenze nennen (der Generator lehnt >16 MB ohnehin ab -- die Vorschau darf nicht zeigen, was der Bau nachher verweigert).

(c) Der Zwischenspeicher in `_bild()` ist praktisch wirkungsl

## 15. [high/ux-a11y] Step list is mouse-only - keyboard and screen readers lose step navigation
DATEI src/archcustomiser/gui/widgets/sidebar.py:47
BESCHREIBUNG `StepSidebar` sets `Qt.FocusPolicy.NoFocus` and resolves clicks itself in `mousePressEvent`/`_schritt_bei` (lines 85-118). There is no key handling, no focusable child, and no accessible name/role for any row. The deleted `gui/widgets/step_sidebar.py` built one `QToolButton` per step (git show 9ae8041^ - it imports `QToolButton` and keeps `self._buttons: dict[str, QToolButton]`), i.e. every step used to be tab-reachable, activatable with Space/Return and visible to assistive technology. The new forward-jumping feature - the headline improvement of the rewrite - is therefore reachable only with a pointing device. `main_window._kuerzel()` adds Ctrl+O/S/F/Return/D but nothing for step selection.
FIX The direction is right but the fix as written is both over- and under-specified.

1. Focus + keys (keep, refine). In `StepSidebar.__init__` use `Qt.FocusPolicy.StrongFocus` and keep a `self._fokus_index: int`. Add `keyPressEvent`: Up/Down move the index, Home/End jump to first/last, Space/Return/Enter emit `stepClicked` — but only when the row is in `self._anklickbar`, mirroring `mousePressEvent`; otherwise `event.ignore()`/beep and leave focus put. Up/Down should skip rows whose status is UEBERSPRUNGEN or GESPERRT (same set `aktualisieren()` already computes), so the ring never lands on a dead row. Nothing else is needed for safety: `main_window._sprung` re-checks `model.anklickbar`, which already keeps a running build (`model.locked`) sealed off. Also reset `_fokus_index` to the current step in `focusInEvent`/`aktualisieren()`, so focus enters at "you are here" and does not point at a row that has since become uebersprungen (steps appear/disappear at runtime — that is the whole point of `anwendbar`).

2. Focus ring (keep). Paint it in `_zeichne_zeile` as a 1.5-2 px `p.accent` outli

## 16. [high/ux-a11y] Disabled-but-checked checkbox/radio renders as unchecked (QSS rule order)
DATEI src/archcustomiser/gui/design/qss.py:149
BESCHREIBUNG `QCheckBox::indicator:checked, QRadioButton::indicator:checked` (line 145) sets `background: accent`, then `QCheckBox::indicator:disabled, QRadioButton::indicator:disabled` (line 149) sets `background: surface_alt` for the same element with the same CSS specificity (type + one pseudo-class + subcontrol). Equal specificity means source order wins, so the later `:disabled` rule overrides the checked fill. Because the indicator is fully style-sheet drawn (border+background set, no `image:`), the checked state is conveyed by the fill colour alone - once that is overwritten there is no glyph left to show it. `CatalogFormPage._sichtbarkeit()` (form.py:196-201) disables rows via `enabled_when`, and 80-user.yaml declares `sudo` and `autologin` as `widget: bool, default: true, enabled_when: "field:user.create"`.
FIX Part one of the proposed fix is right; insert after line 152 of src/archcustomiser/gui/design/qss.py:

QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
    background: {p.border_strong};
    border-color: {p.border_strong};
}}

Two pseudo-classes plus the pseudo-element give this 0x31 against 0x21, so it wins on specificity and does not depend on staying below the `:disabled` block.

Part two ("give the checked indicator a real glyph via image:") should be dropped or reworked. The bundled SVGs (src/archcustomiser/assets/icons/check.svg) are single-colour files that rely on `currentColor`; Qt resolves `image: url(...)` through QSvgRenderer with no palette context, so the mark would render black - invisible on the accent fill in dark mode - and it would not follow a theme or accent change. The sheet is also a single generated string with no file path in it anywhere, so a hardcoded path would be the one theme-blind element in a module whose whole premise is "Farben werden nie eingebrannt". If a glyph is genuinely wanted, render it per theme the way widg

## 17. [high/ux-a11y] Focus ring on the primary button is invisible (accent border on accent background)
DATEI src/archcustomiser/gui/design/qss.py:65
BESCHREIBUNG `QPushButton:focus { border-color: {p.accent}; }` is the only focus indicator for buttons, and `QPushButton[variant="primary"]` (line 68) paints the background with the same `{p.accent}`. The primary rule only overrides background/color/font-weight, so the focused primary button ends up with an accent border on an accent fill - zero contrast, no visible ring. The module docstring at line 13 explicitly promises 'Der Fokusrahmen ist immer da.'
FIX Use the colour the palette already computes as readable on the fill (tokens.lesbare_schrift), not p.text, and cover the danger variant too. In src/archcustomiser/gui/design/qss.py, after the primary/danger blocks:

QPushButton[variant="primary"]:focus {{
    border-color: {p.accent_text};
}}
QPushButton[variant="danger"]:focus {{
    border-color: {p.danger_text};
}}

p.accent_text already exists (tokens.py, lesbare_schrift(akzent) = black or white, whichever wins) and is accent-proof: measured 5.51:1 for #1793d1, 8.64:1 for #e0a53a, 7.90:1 for #a4a8ad, 10.59:1 for #3b3f45. For danger, add a computed field to Palette next to accent_text - `danger_text=lesbare_schrift(danger)` in _palette() for both branches - which yields 7.4:1 on the dark #f0736a and 6.5:1 on the light #b3261e; it also lets qss.py L93 drop the hardcoded `color: #ffffff` on the danger button (only 2.85:1 on the dark danger fill) in favour of {p.danger_text}, keeping the "no hardcoded colours" rule of the module.

Verified by re-rendering with the patched stylesheet: focused primary now paints a real 2 px #111111 ring

## 18. [high/ux-a11y] Hard-coded white on the danger button fails AA in the default dark palette
DATEI src/archcustomiser/gui/design/qss.py:91
BESCHREIBUNG `QPushButton[variant="danger"] { background: {p.danger}; color: #ffffff; }` bypasses `lesbare_schrift()`. Computed with the module's own `kontrast()`: white on the dark palette's `danger` `#f0736a` = 2.85:1 - below AA (4.5) and even below the 3:1 large-text/UI threshold. (Light palette is fine: white on `#b3261e` = 6.54:1.) Dark is the default theme (settings.py:70). tokens.py:126-131 introduced `lesbare_schrift()` for exactly this mistake ('Frueher stand auf einem Abzeichen fest weisse Schrift; auf hellem Orange erfuellte das kein AA-Verhaeltnis'), and the tokens docstring claims both palettes are checked against 4.5:1.
FIX The direction is right but the patch as written does not compile — `qss.py` imports only `Tokens`. Complete fix:

1. `src/archcustomiser/gui/design/qss.py:20` — `from .tokens import Tokens, lesbare_schrift`
2. Line 93 — `color: {lesbare_schrift(p.danger)};` (yields `#111111` / 6.62:1 on the dark `#f0736a`, and keeps `#ffffff` / 6.54:1 on the light `#b3261e`, so no light-mode regression).
3. Add the missing state rules for this variant, which the proposal omits. `QPushButton[variant="danger"]` is declared *after* the generic `QPushButton:hover` (line 55), `:pressed` (58) and `:disabled` (61) and has equal CSS2.1 specificity (an attribute selector and a pseudo-class both count in the same class-level term), so the later danger rule wins in all three states: the cancel button gets no hover/press feedback, and — more relevant here — after `build.py:736` disables it, it still paints as a fully saturated, apparently-live danger button. Mirror the primary variant:
   `QPushButton[variant="danger"]:hover {{ background: {heller(p.danger, 0.08)}; color: {lesbare_schrift(heller(p.danger, 0.08))

## 19. [high/ux-a11y] Accent colour used as body text fails AA in the light palette
DATEI src/archcustomiser/gui/design/tokens.py:179
BESCHREIBUNG The light palette keeps `accent` at the raw user value (default `#1793d1`) for text use, while only hover/pressed are darkened. Computed contrasts: accent on `surface` (#ffffff) = 3.43:1, on `surface_alt` (#eef0f3) = 3.00:1, on `bg` = 3.20:1 - all below the 4.5:1 the module docstring claims for both palettes. It is used for real running text, not just decoration: `free_packages._farbe()` returns `p.accent` for GROUP/PROVIDES_UNIQUE rows (free_packages.py:64-65, painted into the results tree which uses alternating `surface`/`surface_alt` rows), `summary.py:229` colours the 'Verknuepfung' column with it, and `toast.py:112` paints the action label with it on `surface_alt` (3.00:1). Additionally `accent_text` on `accent_pressed` is 3.00:1 in the light palette (pressed primary button).
FIX Two separate derivations in `_palette()` (tokens.py), both palettes, not light-only:

1. Text-safe accent. Add a `accent_on_surface` token derived against the WORST background the accent is ever painted on as text - that is min over {surface, surface_alt, bg}, not just surface. Direction must follow the palette: darken for light, lighten for dark.

    def _text_akzent(akzent: str, gruende: tuple[str, ...], dunkel: bool, ziel: float = 4.5) -> str:
        schritt = 0.02 if dunkel else -0.02
        farbe = akzent
        for _ in range(50):
            if min(kontrast(farbe, g) for g in gruende) >= ziel:
                return farbe
            farbe = heller(farbe, schritt)
        return farbe  # bzw. p.text als letzter Rueckfall

   Call it with `(surface, surface_alt, bg)`. Because `heller()` converges to white/black the loop always terminates; keep the iteration cap and a fallback to `text` so an unusable user accent degrades to plain body colour rather than to an invisible one. Then use `accent_on_surface` at exactly the three text sites - free_packages.py:65 (`_farbe` GROUP/PR

## 20. [high/tests] The motion-idle test is tautological: active_count() is structurally always 0 under the test environment
DATEI tests/test_gui_window.py:456
BESCHREIBUNG `test_nothing_animates_while_the_window_just_sits_there` asserts `motion.active_count() == 0`. The module sets `QT_QPA_PLATFORM=offscreen` (line 16) and `ARCHCUSTOMISER_MOTION=off` (line 19), so `motion.is_reduced()` (motion.py:74-79) returns True unconditionally. Under that flag `animate()` returns before ever adding to `_laufend` (motion.py:114-119) and `run()` returns before ever adding (motion.py:145-151). `_laufend` can therefore never be non-empty, in this or any other GUI test. The test would pass if the window started fifty animations, and it would pass if `_laufend.add()` were deleted from motion.py entirely. Invariant 7 (`active_count() == 0` when nothing happens) has no real coverage, and neither has the counter's bookkeeping: the `_laufend.discard()` in `animate`'s `abschluss` and in `run`'s `abschluss` is executed by no test, so a leaked animation registration would go unnot
FIX The fix must make the assertions run with motion ENABLED; otherwise the replacement is as blind as the original.

1. Do not replace the idle test with a motion-off `findChildren(QTimer)` scan alone. Under `is_reduced()` the two repeating timers (checkmark 33 ms, skeleton 40 ms) can never start, so that scan still cannot catch the "hidden spinner keeps ticking" regression it is meant to catch, and it additionally misses `QTimer.singleShot` timers (pages/build.py:413/571/626/700, widgets/intro.py:53, widgets/common.py:61 — Qt6 parents those to an internal QSingleShotTimer, not to the widget tree) and the worker-thread `_flush` timer (build_worker.py:162). Instead, wrap the idle test itself in `motion.set_reduced(False)` (try/finally restoring `motion.set_reduced(None)` AND calling `motion.stop_all()`), then assert BOTH `motion.active_count() == 0` and `[t for t in window.findChildren(QTimer) if t.isActive()] == []` after `window.show(); window.grab()`. Note this is only meaningful because no timer currently starts at construction — verify it stays green before committing.

2. Counter-b

## 21. [high/tests] test_a_non_cancellable_wait_dialog_ignores_escape passes whether or not reject() is overridden
DATEI tests/test_gui.py:299
BESCHREIBUNG The assertion is `assert not dialog.isHidden() or dialog.result() == 0`. The dialog is never shown, so `isHidden()` is True and the first clause is False; everything rests on `dialog.result() == 0`. A freshly constructed QDialog has `result() == 0`, and `QDialog::Rejected` is also `0` — so if `WaitDialog.reject()` (wait_dialog.py:92-102) were deleted and `QDialog.reject()` ran, `result()` would still be 0 and the test would still pass. The test verifies nothing about the behaviour named in its own docstring ("Der Schliessknopf war entfernt, reject() aber nicht ueberschrieben"). It also never checks the mirror case (with `cancellable=True`, reject must work), so an over-eager fix that makes `reject()` a no-op for every dialog would also pass.
FIX The proposed fix works (verified). A slightly stronger variant avoids showing the dialog at all and is immune to the queued _fertig/accept() race, since it observes the signals QDialog.reject() would emit:

def test_a_non_cancellable_wait_dialog_ignores_escape(qapp) -> None:
    from archcustomiser.gui.widgets.wait_dialog import WaitDialog

    dialog = WaitDialog(lambda: None, "laeuft", cancellable=False)
    gesehen: list[object] = []
    dialog.rejected.connect(lambda: gesehen.append("rejected"))
    dialog.finished.connect(lambda code: gesehen.append(("finished", code)))
    dialog.reject()
    assert gesehen == []          # ohne Ueberschreibung: ['rejected', ('finished', 0)]

def test_a_cancellable_wait_dialog_honours_escape(qapp) -> None:
    from archcustomiser.gui.widgets.wait_dialog import WaitDialog

    dialog = WaitDialog(lambda: None, "laeuft", cancellable=True)
    gesehen: list[object] = []
    dialog.rejected.connect(lambda: gesehen.append("rejected"))
    dialog.reject()
    assert gesehen == ["rejected"]

If you prefer the visibility form from the proposal, it is c

## 22. [high/tests] The search-filter test contains a literal `or True` and passes if filtering is a no-op
DATEI tests/test_gui_window.py:414
BESCHREIBUNG `assert all("steam" in k.lower() or True for k in sichtbar)` is `all(True for ...)` — a tautology that ruff's B011/B015-adjacent rules do not catch because it is a real `assert` on a truthy generator. The only load-bearing assertion left is `assert sichtbar`, i.e. "at least one card is visible". If `_filtern()` (selection.py:181-201) hid nothing at all, every card would be visible, `sichtbar` would be non-empty, and both assertions would pass. The test therefore cannot detect a broken search, and specifically cannot detect the behaviour its docstring claims to protect — that `_trifft` (selection.py:275-282) searches `option.packages` and not just `option.label`.
FIX Two changes in `C:/Users/uih48523/Desktop/codes/jasontool/tests/test_gui_window.py`, plus one lint change.

(a) Replace the tautology at line 414 with a discriminating check, and include `description` in the matched fields so it mirrors `_trifft`:

```python
def test_the_search_filters_by_package_name(window) -> None:
    """Wer "steam" sucht, denkt nicht an "Spieleplattform"."""
    seite = window._pages["apps"]
    seite.enter()
    seite.search.edit.setText("steam")
    seite._filtern()
    sichtbar = {
        kennung for kennung, karte in seite._karten.items() if not karte.isHidden()
    }
    assert sichtbar, "die Suche findet nichts"
    assert len(sichtbar) < len(seite._karten), "die Suche blendet nichts aus"
    for kennung in sichtbar:
        option = seite._karten[kennung].option
        felder = " ".join(
            str(feld)
            for feld in (option.label, option.description, option.id, *option.packages)
            if feld
        ).lower()
        assert "steam" in felder, f"{kennung} passt nicht zum Suchbegriff"
```

(b) Add the test that actually dies when `

## 23. [high/tests] The autoscroll-pause test's visibility assertion is dead (`or page.isHidden()` is always true)
DATEI tests/test_build_page.py:358
BESCHREIBUNG `assert page.ans_ende.isVisible() or page.isHidden()` — the `page` fixture (test_build_page.py:92-99) never calls `show()`, so `page.isHidden()` is always True and the disjunction is always satisfied. The "Zum Ende springen" button therefore has no coverage: `_scroll_geaendert` (build.py:522-533) could stop calling `self.ans_ende.setVisible(not am_ende and not self._done)` and nothing would fail. Only `assert not page._folgen` and the follow-up `page._ans_ende_springen(); assert page._folgen` are real. Missing entirely: that autoscroll actually *follows* while `_folgen` is True (i.e. `_zeilen` sets the scrollbar to `maximum()`, build.py:519-520), that new lines arriving while paused do **not** move the viewport, and that the button hides again once the build is done (`not self._done` clause).
FIX Do not reach for `show()`; assert on `isHidden()` of the button itself. `setVisible(True)` clears WA_WState_Hidden even while every ancestor is hidden (verified: `b.isHidden()` is False after `setVisible(True)` under a never-shown parent, True again after `setVisible(False)`). So `not page.ans_ende.isHidden()` is a real, platform-independent assertion on exactly what `_scroll_geaendert` controls, and it needs no fixture change.

In tests/test_build_page.py:

  # line 358, replace the dead disjunction
  assert not page.ans_ende.isHidden()

  def test_the_view_follows_the_tail_while_not_paused(page, tmp_path) -> None:
      job = vorbereiten(page, tmp_path)
      page._bau_starten()
      job.linesReceived.emit([f"Zeile {n}" for n in range(500)])
      balken = page.log.verticalScrollBar()
      assert balken.value() == balken.maximum() and balken.maximum() > 0

  def test_new_lines_do_not_move_the_view_while_paused(page, tmp_path) -> None:
      job = vorbereiten(page, tmp_path)
      page._bau_starten()
      job.linesReceived.emit([f"Zeile {n}" for n in range(500)])
      balken = p

## 24. [high/tests] Welcome-page reset semantics: the two branches that caused the data loss are untested
DATEI tests/test_gui_window.py:275
BESCHREIBUNG `test_going_back_to_the_welcome_page_keeps_the_selection` covers the leer→leer no-op and `test_changing_the_welcome_choice_asks_before_discarding` covers only the *No* answer. Untested: (a) `markiere_extern_geladen` (welcome.py:363-370) — the method exists precisely so a profile loaded via the header button is not wiped when the user later revisits the welcome page, and while `test_a_loaded_profile_marks_every_step_as_visited` (line 297) happens to invoke `_profil_geladen()`, it never afterwards calls `window.welcome.leave()` and checks the store survived; (b) the *Yes* branch of `_darf_verwerfen` (welcome.py:312-327) — that answering Yes really does call `store.reset()` and move `_angewendet`; (c) the `datei` branch (welcome.py:288-298), which has no `_angewendet` short-circuit at all, so leaving the welcome page a second time with "Eigenes Profil laden" still selected re-opens `QFileDi
FIX Four tests in tests/test_gui_window.py, next to the existing pair. Two corrections matter versus the proposal.

(1) The marker makes the page ASK, not stay silent — assert that, plus its contrast:

def test_a_profile_loaded_from_the_header_is_not_discarded(window, store, monkeypatch):
    seite = window.welcome
    store.toggle("desktop.kde", True)
    window._profil_geladen()                       # setzt markiere_extern_geladen()
    gefragt = []
    monkeypatch.setattr(QMessageBox, "question",
        lambda *a, **k: gefragt.append(1) or QMessageBox.StandardButton.No)
    assert not seite.leave()
    assert gefragt, "das geladene Profil waere wortlos verworfen worden"
    assert "kde" in store.selected("desktop")

Pin the contrast so the marker cannot be dropped unnoticed — a fresh page with `_angewendet is None` resets without asking:

def test_the_first_pass_resets_without_asking(window, store):
    store.toggle("desktop.kde", True)
    assert window.welcome.leave()
    assert "kde" not in store.selected("desktop")

(2) Reach the Yes branch through the leer path, NOT by mirrorin

## 25. [high/tests] ProfileActions is entirely untested, including the unsaved-work regression its docstring documents
DATEI src/archcustomiser/gui/actions.py:214
BESCHREIBUNG No test file references `ProfileActions` (grep over tests/ finds only the substring "actions" in unrelated contexts). This 258-line module owns save, load, export and the quit prompt. Specifically uncovered: `fingerprint()`/`hat_ungesicherte_arbeit()` and the documented regression they fix — before, a "saved once" flag was never reset, so everything changed after the first save was lost on exit without a prompt; `darf_beenden()`'s three branches (Cancel → False, Save → save then re-check, Discard → True), including the subtle "Save was itself cancelled, therefore do not quit" path at actions.py:252-254; `speichern()`'s OSError branch; `lade_datei()`'s issue and `secret_fields` notices; `exportieren()`'s archive-vs-directory suffixing (actions.py:156-160) and its `fenster.setEnabled(False)` lock, which is only released in `_export_fertig`/`_export_fehler` — an uncaught path there would le
FIX Add `tests/test_actions.py`. Keep the reviewer's core set, drop the unfounded export-lock framing, and add the two guards the fix omitted.

Fixture (no MainWindow, no theme):

    @pytest.fixture
    def actions(qapp, catalog, store, settings):
        from PySide6.QtWidgets import QWidget
        from archcustomiser.core.profiles import ProfileService
        from archcustomiser.gui.actions import ProfileActions
        fenster = QWidget()
        yield ProfileActions(catalog, store, ProfileService(catalog), settings, fenster)
        fenster.deleteLater()

`tests/conftest.py:185` already redirects HOME/LOCALAPPDATA/XDG_*, so `speichern()` writing under `user_profiles_dir()` is sandboxed — no extra monkeypatching of paths is needed.

Keep, as proposed:
- `test_opening_and_closing_without_changes_asks_nothing` — `assert not actions.hat_ungesicherte_arbeit()` straight after construction (the catalog defaults applied in `SelectionStore.__init__` must not count as work).
- `test_the_saved_state_moves_with_every_save` — monkeypatch `QFileDialog.getSaveFileName` to return `(str(tmp_path /

## 26. [medium/parity] Category icons from the catalog are no longer rendered anywhere
DATEI src/archcustomiser/gui/widgets/sidebar.py:170
BESCHREIBUNG The deleted step_sidebar.py drew a per-step SVG next to each entry: `symbol = load_icon(category.icon)` / `button.setIcon(symbol)`, and `_natural_width()` even reserved 16 px plus a gap for it. The new self-drawn StepSidebar draws only a status ring and the text; there is no `icon` reference in sidebar.py at all. `navigation.Step` still carries an `icon` field and `NavigationModel.aus_katalog` still fills it from `kategorie.icon` (navigation.py:52, 87), but nothing reads it. `widgets/icons.py::load_icon` now has zero callers in the whole tree (only `design/theme.py` calls `icons.cache_leeren()`), so the 16 shipped SVGs under assets/icons plus the whole colourising/DPI/cache machinery are dead code. This is a catalog-driven capability (`Category.icon`, `Option.icon`) that silently disappeared - and it is exactly the capability whose absence icons.py's own docstring was written to fix.
FIX The "render it" half of the proposed fix is correct in direction but needs more than a one-line insert; the "or remove it" half is wrong as written and would break the build.

Render option (preferred), in src/archcustomiser/gui/widgets/sidebar.py:
- In `_zeichne_zeile`, load with `load_icon(schritt.icon, farbe, 16)` and paint via `symbol.paint(maler, kasten.toRect(), Qt.AlignCenter)` — QIcon.paint takes a QRect, not the QRectF used everywhere else in this file.
- The 16 px slot at `zeile.left() + space.sm` is already occupied by `_zeichne_marke`'s status ring. Both the ring rect in `_zeichne_marke` and the `textkasten` offset in `_zeichne_zeile` (currently the literal `26 + space.sm`, appearing twice — as left offset and in the width subtraction) must shift by the icon width plus a gap; the bare `26` literals should become one module-level constant so the three sites cannot drift apart.
- `_natuerliche_breite` must add the same amount. Since `load_icon` returns None for a missing/invalid SVG, compute once (in `__init__` / after theme change) whether any step actually resolves an ico

## 27. [medium/parity] The build plan is no longer written to the log before an export or a build
DATEI src/archcustomiser/gui/build_flow.py:67
BESCHREIBUNG The old wizard logged the full resolved plan at two points - `_export()` and `accept()` both did `plan = page.plan() if isinstance(page, SummaryPage) else None` followed by `log.info("Bauplan:\n%s", plan_as_text(plan))`. `BuildFlow.start(plan=None)` still contains that log statement, but no caller ever passes a plan: `BuildPage._pruefung_starten` (pages/build.py:372) calls `self.flow.start()` with no argument, and `ProfileActions.exportieren` (actions.py:135-172) dropped the logging entirely. The `plan` parameter is dead, and the build log no longer records what was actually being built. `SummaryPage.plan()` still exists and is still reachable from MainWindow (`_ist_baubereit` already looks the summary page up), so the data is available - it just never reaches the log.
FIX Do not route this through the pages. The proposed fix (hand BuildPage a callable to fetch `SummaryPage.plan()`) is wrong on two counts:

1. Unnecessary coupling and a None hole. `BuildFlow` already owns `self.catalog` and `self.store`, and `build_plan(catalog, config, resolution, report=None)` has an optional report (core/plan.py:229-234). So build the plan where it is logged:

   - build_flow.py: drop the `plan` parameter entirely and do, at the top of `start()`:
     `log.info("Bauplan:\n%s", plan_as_text(build_plan(self.catalog, self.store.config, self.store.resolution())))`
   - actions.py `exportieren()`: same two lines after the file dialog returns a target (import `build_plan`/`plan_as_text` from `..core.plan`).

   This also covers the case `SummaryPage.plan()` cannot: a user who never lands on the summary page still gets a logged plan, whereas `page.plan()` would hand back `None` — the same hole the old wizard had.

2. Wrong log destination for the stated scenario. `log.info` goes to the application log (`log_file_path()`), but the file the user is handed after a build is th

## 28. [medium/qt-lifetime] BuildFlow creates a new BuildJob per preflight, parented to the flow and never released; BuildPage never disconnects the previous job
DATEI src/archcustomiser/gui/build_flow.py:197
BESCHREIBUNG _vorabpruefung() constructs `BuildJob(..., self)` on every run (build_flow.py:197-203) — parented to the BuildFlow, which is parented to the main window. Nothing ever deletes these jobs, and nothing drops the reference when BuildPage._pruefung_zeigen overwrites `self.job` (build.py:387). Every click on "Bauumgebung pruefen" / "Erneut pruefen" therefore adds a permanent QObject subtree to the window: a BuildJob, its BuildController, its QTimer _flush (build_worker.py:162), and — for any job that actually ran — a finished _BuildThread and possibly a _CancelThread, both QThread children that are only deleteLater()d on the build path (build_worker.py:259) and never for the cancel thread.

Symmetrically, BuildPage._bau_starten connects six of the job's signals to page slots (build.py:440-445) and there is no matching disconnect anywhere; _zuruecksetzen (build.py:704-717) sets `self.job = None
FIX The proposed fix is directionally right but has one dangerous step and misses the part that actually matters.

1. Do NOT use a blanket `self.job.disconnect(self)`. In PySide6 `QObject.disconnect(receiver)` raises `RuntimeError` when it finds nothing to disconnect, which is exactly the state after `_zuruecksetzen` has already run once, and the tests drive `_pruefung_zeigen` with a `FakeJob` that was never connected (tests/test_build_page.py:116-119). Disconnect the six signals explicitly and defensively, in one helper called from both `_pruefung_zeigen` (before the reassignment at build.py:391) and `_zuruecksetzen` (build.py:772):

    def _job_loesen(self) -> None:
        job = self.job
        if job is None:
            return
        for signal, slot in (
            (job.stepChanged, self._schritt),
            (job.progressChanged, self._fortschritt),
            (job.linesReceived, self._zeilen),
            (job.finished, self._beendet),
            (job.failed, self._fehlgeschlagen),
            (job.cancelled, self._abgebrochen),
        ):
            try:
                

## 29. [medium/threading] A cancel that fails is invisible: the UI stays on 'Wird abgebrochen ...' with the button disabled
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/build_worker.py:129
BESCHREIBUNG _CancelThread.run() swallows every exception into the log and has no failure signal. LocalTarget.cancel_run deliberately raises BuildError when terminate() fails with EPERM and pkexec is unavailable (targets.py:337-344) -- precisely the case its own comment describes as 'der Benutzer war fuer die restliche Bauzeit eingesperrt'. BuildPage has already disabled cancel_button and set the headline to 'Wird abgebrochen ...' (pages/build.py:672-674), and a second click returns early because job.cancelling stays True (line 652), so there is no retry and no message. The build keeps running to completion and only then raises BuildCancelled in the runner (runner.py:265-267).
FIX The direction is right, but the proposed placement of the message would be invisible and the state reset is incomplete.

1. build_worker.py `_CancelThread`: add `failed = Signal(object)`; in run(), keep the log and `self.failed.emit(exc)` in the except branch (keep catching bare Exception).
2. `BuildJob`: add `cancelFailed = Signal(object)`; in `cancel()` connect `thread.failed` to a slot that sets `self._cancel_requested = False` (so a retry is possible) and re-emits `cancelFailed`. Also connect `thread.finished` to clear `self._cancel_thread` and `deleteLater()` it — otherwise a retry orphans the old thread and `busy`/`wait()` only track the newest one.
   Important caveat the fix must carry: resetting `_cancel_requested` does NOT undo `BuildController._cancelled` (controller.py:326), which is set before `runner.cancel()` is called. The build is therefore already doomed to end as `BuildCancelled` and have its output deleted, even though it keeps running. The user-facing text must say that: the build could not be stopped, it keeps running, and its result will be discarded.
3. pages/

## 30. [medium/threading] A cancel arriving during the SHA-256 pass silently discards the checksum and is never reported
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/build_worker.py:102
BESCHREIBUNG The checksum is computed in _BuildThread after controller.run() returned (correct: it is off the UI thread), but the build page does not know about this phase -- cancel_button is only hidden in _abschliessen, which runs on the finished/failed/cancelled signal. During the multi-second hash of a 3 GB ISO the button is therefore still visible and enabled, the progress bar reads 'Fertig', and a click sets controller._cancelled, which makes _pruefsumme abandon the digest and return ''. The result page then shows 'nicht berechnet' and disables 'Pruefsumme speichern' (pages/build.py:560-563), while the headline jumps from 'Wird abgebrochen ...' straight to 'Fertig'. _abgebrochen never runs, so the user gets no explanation. The ISO itself is complete -- the very artefact the docstring calls indispensable for spotting a silent USB write error is dropped without a word.
FIX The proposed fix is right in direction but incomplete on both ends. Concretely:

(a) Make the cancel unable to destroy a digest for a build that already succeeded. Do not read `controller.cancelled` in `_pruefsumme`. Give `_BuildThread` its own flag, e.g. `self._hash_abbruch = threading.Event()`, and reuse the existing core helper instead of the duplicated loop: `from ...core.build.verify import sha256_von` and `return sha256_von(pfad, abbruch=self._hash_abbruch.is_set)` — `sha256_von(pfad, *, abbruch=None)` (core/build/verify.py:176-194) already implements exactly this block loop with an abort callback, so build_worker.py:98-108 and the local `HASH_BLOCK` constant are dead duplication. Set that event only from an explicit "give up on the checksum" path (window closing / app shutdown), never from `BuildJob.cancel()`.

(b) Make `BuildJob.cancel()` a no-op once `run()` has returned. Add a flag set in `_BuildThread.run` right after `controller.run(...)` returns (before `_pruefsumme`) and have `BuildJob.cancel()` skip starting the `_CancelThread` in that state — otherwise the click still

## 31. [medium/threading] WSL distribution probing runs on the UI thread, outside run_with_wait
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/build_flow.py:165
BESCHREIBUNG status.find_arch(probe=_ist_arch) is called after run_with_wait(wsl.detect, ...) has returned, i.e. on the UI thread with no dialog and no progress. _ist_arch runs WslTarget.is_arch(), which issues wsl.exe calls with DEFAULT_TIMEOUT = 60 s each and falls back to a second call via has_command('pacman') (wsl.py:47, 431-442), for every distribution whose name does not look like Arch (wsl.py:138-139). Each probe starts a stopped distribution. This contradicts the module docstring, which states that everything expensive goes through run_with_wait. (Pre-existing: wizard.py:626 did the same before the rewrite.)
FIX The direction is right (do the probe inside the worker), but the proposed lambda breaks the code that follows it: `run_with_wait` returns `None` for both "user cancelled" and "failed", and the existing `if status is None` / `status.installed` / `WslSetupDialog(status, …)` all still need the WslStatus object. Returning a bare tuple would make `status` a tuple and crash on cancel. Use an explicit helper instead:

```python
def _wsl_ziel(self):
    from ..core.build import wsl
    from ..core.build.targets import WslExecutionTarget

    def _suchen():
        status = wsl.detect()
        return status, status.find_arch(probe=_ist_arch)

    ergebnis, fehler = run_with_wait(
        _suchen,
        "Linux-Untersystem wird geprueft ...\n\n"
        "Das kann einen Moment dauern, wenn die Verteilungen erst "
        "starten muessen.",
        parent=self.fenster,
    )
    if fehler is not None:
        ...unveraendert...
        return _ABGEBROCHEN
    if ergebnis is None:
        return _ABGEBROCHEN          # vom Benutzer abgebrochen
    status, gefunden = ergebnis
    if status.inst

## 32. [medium/threading] closeEvent freezes the UI thread for up to 60 seconds while waiting for the threads
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/main_window.py:417
BESCHREIBUNG job.wait(30000) blocks the UI thread, and BuildJob.wait waits the build thread and the cancel thread one after the other with the same budget each (build_worker.py:227-235), so up to 60 s of a completely frozen, unpainted window. _CancelThread exists precisely so that the cancel does not block the UI ('legte dann das Fenster fuer bis zu anderthalb Minuten vollstaendig still', build_worker.py:111-123); the shutdown path re-introduces exactly that behaviour, and there is no wait dialog because the window is in the middle of closing.
FIX Do not hand-roll a polling close. Reuse the existing helper that was written for exactly this ("Eine Arbeit im Hintergrund erledigen, ohne dass das Fenster einfriert"): `run_with_wait` / `WaitDialog` in C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/widgets/wait_dialog.py. It runs the callable on a QThreadPool thread and exec()s a modal, indeterminate-progress dialog, so the event loop keeps running and the window keeps painting. `QThread::wait()` is safe to call from a thread other than the one being waited on, and build_flow.py:207 already uses run_with_wait for the same class of WSL-slow work.

In main_window.py, replace line 417:

    job.cancel()
    fertig, _ = run_with_wait(
        lambda: job.wait(60000),
        "Der Bau wird abgebrochen -- das kann eine Minute dauern.",
        parent=self,
        cancellable=False,
    )
    if not fertig:
        log.warning("Bau-/Abbruchfaden nicht beendet -- Fenster wird trotzdem geschlossen.")

Three things this fixes that a naive port would not:

1. cancellable=False is required. WaitDialog.reject() is overridden t

## 33. [medium/catalog-driven] Zwei Felder mit derselben preview_role: erstes gewinnt, ohne Hinweis
DATEI src/archcustomiser/gui/previews/registry.py:95
BESCHREIBUNG `rollen_aus_katalog` sammelt katalogweit mit `rollen.setdefault(spec.preview_role, spec.binding)`. Der zweite Anbieter einer Rolle wird stillschweigend verworfen. Die Reihenfolge ist `catalog.categories`, also nach `(step, id)` sortiert (loader.py:920-925) -- der Gewinner ist damit das Feld in der Kategorie mit der kleinsten Schrittnummer, was von aussen weder sichtbar noch dokumentiert ist. Der Loader validiert `preview_role` gar nicht (loader.py:386 liest nur den String), obwohl er fuer strukturell gleichartige Mehrdeutigkeiten sonst hart abbricht ("Alias %r ist mehrdeutig", loader.py; "Mehrere Kategorien mit step=").
FIX Zwei Aenderungen, beide als Warnung, keine als harter Abbruch:

1. `core/catalog/loader.py`, in `load_catalog` direkt nach dem Duplikatscheck der Schrittnummern (nach Zeile 935), also einmal pro Ladevorgang statt einmal pro Seitenaufbau:

```python
    rollen_gesehen: dict[str, str] = {}
    for category in categories:
        for spec in category.fields:
            if not spec.preview_role:
                continue
            erster = rollen_gesehen.setdefault(spec.preview_role, spec.binding)
            if erster != spec.binding:
                log.warning(
                    "preview_role %r ist mehrfach vergeben: %s und %s -- "
                    "die Vorschau benutzt %s (kleinste Schrittnummer gewinnt)",
                    spec.preview_role, erster, spec.binding, erster,
                )
```

Bewusst `log.warning` und kein `CatalogError`: ein Benutzer-Overlay unter `user_catalog_dir()` darf die Anwendung nicht startunfaehig machen, nur weil eine Vorschaurolle doppelt vergeben ist. Das entspricht der bereits vorhandenen weichen Behandlung bei undeklarierten Capabilities (l

## 34. [medium/catalog-driven] Bauseite dupliziert das Katalogfeld build.keep_work_dir und schreibt die Aenderung nie zurueck
DATEI src/archcustomiser/gui/pages/build.py:241
BESCHREIBUNG Die Bauseite baut eine eigene QCheckBox mit hart kodierter deutscher Beschriftung (Zeile 241-243), fuellt sie aus `self.store.config.field_bool("build.keep_work_dir")` (Zeile 426) und uebergibt beim Start `keep_work_dir=self.keep_work.isChecked()` (Zeile 468). Der Wert geht nie ueber `store.set_field` zurueck in die BuildConfig. Damit existiert dasselbe Katalogfeld zweimal in der Oberflaeche -- einmal katalog-gerendert auf der Formularseite "ISO-Einstellungen" (90-build.yaml:114, mit eigenem `label` und `help`), einmal als Python-Literal --, und die beiden koennen auseinanderlaufen. Anders als `build.work_dir`/`build.output_dir` (Infrastruktur, nur gelesen) wird hier eine Benutzereingabe entgegengenommen und verworfen.
FIX Do not look the spec up by literal id. The catalog already has the mechanism for "the GUI asks by role, the YAML says which field plays it": `FieldSpec.preview_role` plus `rollen_aus_katalog` (gui/previews/registry.py:84-96), documented in core/catalog/models.py:196-208 as the way to avoid naming a field.

1. 90-build.yaml, field `keep_work_dir`: add a role, e.g. `preview_role: "keep_work_dir"` (or introduce a parallel `role:`/`build_role:` key if reusing `preview_role` for a non-preview purpose is unwanted — the loader's allow-list is in core/catalog/loader.py:336/420).
2. gui/previews/registry.py: add a sibling to `rollen_aus_katalog` that returns the `FieldSpec`, not just the binding, e.g. `def feld_mit_rolle(catalog, rolle) -> FieldSpec | None`. `rollen_aus_katalog` only yields bindings, which is not enough — label and help are needed.
3. gui/pages/build.py `_pruefseite`: replace the literal `QCheckBox(...)` with a `FieldRow(spec)` (gui/widgets/fields.py) built from that spec, so label, help and default all come from YAML. If the role is absent from the catalog, render nothing ra

## 35. [medium/catalog-driven] "Schritt 'Benutzerkonto'" ist an zwei Stellen als Literal in die GUI geschrieben
DATEI src/archcustomiser/gui/actions.py:100
BESCHREIBUNG Die Meldung nach dem Laden eines Profils nennt den Schritt namentlich: "Bitte das Passwort im Schritt 'Benutzerkonto' neu eingeben." Das ist der `title` der Kategorie `user` aus 80-user.yaml:19. Derselbe Block steht ein zweites Mal in src/archcustomiser/gui/pages/welcome.py:354. Die Information liegt dabei bereits strukturiert vor: `ProfileLoadResult.secret_fields` (core/profiles.py:93) enthaelt die Bindings der betroffenen Geheimfelder, aus denen sich Kategorie und deren Titel ableiten lassen.
FIX Der Befund stimmt, der vorgeschlagene Weg ist an einer Stelle falsch.

Nicht `binding.split('.', 1)[0]` verwenden. `FieldSpec.binding` ist in YAML frei setzbar (core/catalog/loader.py:371 -- `binding=_str(data, "binding", spot) or f"{category_id}.{field_id}"`); nur der Default traegt die Kategorie-ID. Ein Overlay mit explizitem `binding:` -- genau der Fall, den der Fix abdecken soll -- wuerde falsch oder gar nicht aufgeloest.

Stattdessen ueber den Katalog rueckwaerts suchen, gemeinsame Funktion z.B. in gui/widgets/common.py oder einem neuen gui/profile_hinweise.py:

    def kategorien_mit_geheimnis(catalog: Catalog, bindings: Sequence[str]) -> tuple[str, ...]:
        gesucht = set(bindings)
        titel: list[str] = []
        for kategorie in catalog.categories:
            if any(spec.secret and spec.binding in gesucht for spec in kategorie.fields):
                if kategorie.title not in titel:
                    titel.append(kategorie.title)
        return tuple(titel)

    def passwort_hinweis(catalog: Catalog, bindings: Sequence[str]) -> str:
        titel = kategorien_mi

## 36. [medium/catalog-driven] ISO-Panel zeigt "multilib" als vollstaendige Repository-Liste und haelt "core, extra" als Literal
DATEI src/archcustomiser/gui/widgets/iso_panel.py:166
BESCHREIBUNG `", ".join(resolution.repositories) or "core, extra"` behandelt `Resolution.repositories` als die komplette Liste. Sie enthaelt aber nur die *zusaetzlichen* Repos aus `option.repos` (core/resolver.py:550, `repositories.update(option.repos)`); im ganzen Katalog steht dort ausschliesslich `multilib`. Die Basisrepos sind in core als `pacman_conf.BASE_REPOSITORIES = ("core", "extra")` definiert und werden von `render_pacman_conf` immer vorangestellt -- die GUI schreibt denselben Inhalt noch einmal als Zeichenkette hin, statt ihn zu importieren. Das Modul-Docstring des Panels sagt ausdruecklich, es rechne nichts selbst aus, "sonst gaebe es zwei Wahrheiten".
FIX Befund stimmt, der vorgeschlagene Fix ist aber nur halb richtig: `BASE_REPOSITORIES + tuple(resolution.repositories)` dupliziert, sobald eine Katalogoption `repos: [core]` o.ae. nennt (der Resolver macht nur `repositories.update(option.repos)`, filtert nichts), und die Reihenfolge weicht von der erzeugten pacman.conf ab -- `render_pacman_conf` sortiert die Zusatzrepos ueber `OPTIONAL_ORDER` (`core-testing, extra-testing, multilib, multilib-testing`), die Resolution liefert sie alphabetisch sortiert.

Besser die eine Quelle wirklich teilen: in `src/archcustomiser/core/archiso/pacman_conf.py` die vorhandene Reihenfolgen-/Dedup-Logik aus `render_pacman_conf` in eine oeffentliche Funktion herausziehen, z.B.

    def effective_repositories(extra_repositories: Sequence[str] = ()) -> tuple[str, ...]:
        wanted = list(BASE_REPOSITORIES)
        for name in OPTIONAL_ORDER:
            if name in extra_repositories and name not in wanted:
                wanted.append(name)
        for name in extra_repositories:
            if name not in wanted:
                wanted.append(name)
     

## 37. [medium/catalog-driven] Verzeichnis- statt Dateidialog wird am Validatornamen erkannt
DATEI src/archcustomiser/gui/widgets/fields.py:50
BESCHREIBUNG `VERZEICHNIS_VALIDATOREN = frozenset({"writable_dir"})` und `waehle_pfad` (Zeile 221) entscheiden anhand des Validatornamens, ob ein Verzeichnis- oder ein Dateidialog aufgeht. Der Validator ist eine Pruefregel, keine Widget-Beschreibung; die GUI kennt hier einen Katalogstring namentlich und trifft daran eine Darstellungsentscheidung. Der Loader kennt bereits acht Widget-Arten (`_VALID_WIDGETS`, loader.py:47) -- "dir" ist keine davon, obwohl genau das gemeint ist.
FIX The direction is right but the proposed fix is incomplete — applied as written it makes things worse. Four additions:

1. **The browse button disappears.** `_erzeuge_widget` (fields.py:194) creates the Durchsuchen button only under `if spec.widget == "path"`. A field with `widget: dir` falls through every branch to the plain `QLineEdit` and gets `browse = None` — no button at all. Must become `if spec.widget in ("path", "dir")`.

2. **Do not delete `VERZEICHNIS_VALIDATOREN` outright.** User overlays loaded from `user_catalog_dir()` (loader.py:914) may already use `widget: path` + `validator: writable_dir` for a directory. Keep the validator-name branch as a documented compatibility fallback in `waehle_pfad` (`spec.widget == "dir" or spec.validator in VERZEICHNIS_VALIDATOREN`), ideally with a `log.debug`/deprecation note. Removing it silently regresses those overlays to a file dialog — invariant 8.

3. **A test pins the old coupling.** `tests/test_gui_window.py:352` `test_a_directory_field_opens_a_directory_dialog` asserts `spec.validator == "writable_dir"`. Change that assertion to `

## 38. [medium/catalog-driven] Loader prueft confirm_field nicht -- ein Tippfehler sperrt den Weiter-Knopf dauerhaft
DATEI src/archcustomiser/core/catalog/loader.py:385
BESCHREIBUNG `confirm_field` wird als freier String eingelesen; es gibt keine Pruefung, dass ein Feld dieses Namens in derselben Kategorie existiert. In `CatalogFormPage._wert_von` (gui/pages/form.py:264-277) liefert ein unbekannter Name `""` zurueck, und `_alles_pruefen` (Zeile 236-244) vergleicht die Eingabe dagegen. Die Fehlermeldung wandert per `self._rows.get(spec.confirm_field, zeile)` an das Ausgangsfeld, weil es das Wiederholungsfeld nicht gibt -- der Benutzer liest "Die beiden Eingaben stimmen nicht ueberein" an einem Feld, das er nur einmal ausgefuellt hat.
FIX Prüfung in `_parse_category` (loader.py, neben den bestehenden Gruppen-/`default_selection`-Prüfungen, ca. Z. 651-665) statt in `_parse_fields`, weil dort schon die referenziellen Checks stehen und `where` die Quelldatei nennt. Sie muss mehr abdecken als nur die Existenz des Namens:

```python
field_ids = {f.id: f for f in fields}
for spec in fields:
    if not spec.confirm_field:
        continue
    ziel = field_ids.get(spec.confirm_field)
    if ziel is None:
        raise CatalogError(
            f"{where}: Feld {spec.id!r} verweist mit confirm_field auf "
            f"{spec.confirm_field!r}, das es in {category_id!r} nicht gibt"
        )
    if ziel.id == spec.id:
        raise CatalogError(f"{where}: Feld {spec.id!r} bestaetigt sich selbst")
    if ziel.secret != spec.secret:
        raise CatalogError(
            f"{where}: {spec.id!r} und {ziel.id!r} muessen dasselbe 'secret' haben"
        )
    if ziel.confirm_field:
        raise CatalogError(f"{where}: Wiederholungsfeld {ziel.id!r} darf kein confirm_field haben")
```

Warum die Zusatzprüfungen nötig sind:
- `secret`-U

## 39. [medium/secrets] Redaktionsfilter erfasst exc_info nicht -- Tracebacks landen ungefiltert im Protokoll
DATEI src/archcustomiser/core/logging_setup.py:85
BESCHREIBUNG `SecretRedactionFilter.filter` maskiert `record.msg` und `record.args`, laesst `record.exc_info`/`record.exc_text` aber unberuehrt. Der Formatter haengt den Traceback erst nach dem Filter an, also geht er unmaskiert in die rotierende Logdatei. Empirisch geprueft: ein registriertes Secret 'geheim123' und ein crypt-Hash '$6$...' erscheinen im Traceback im Klartext, waehrend dieselben Werte in `%s`-Argumenten korrekt zu *** werden. Genau die Wege, die im Fehlerfall greifen, protokollieren mit exc_info: `build_worker._BuildThread.run` (log.exception, Zeile 81), `profile_worker._ExportTask.run` (Zeile 78) und `__main__._install_crash_handler` (`logger.critical(..., exc_info=...)`, Zeile 199), der ausdruecklich 'in jedem Fall ins Protokoll' schreibt. Damit haelt die Zusicherung des Moduldocstrings ('auch Hashes gehoeren nicht ins Log') nur fuer die halbe Menge der Log-Ausgaben.
FIX Der vorgeschlagene Fix ist richtig, aber unvollstaendig -- er kuriert nur einen von zwei Zweigen derselben Ursache: maskiert wird der *Record vor der Interpolation*, nicht die *fertige Ausgabe*.

Zweite, mindestens ebenso wahrscheinliche Luecke (selbst nachgestellt): `_scrub_any` (Z. 76-83) gibt alles zurueck, was nicht str/list/tuple/dict ist. Ein Ausnahme- oder Pfadobjekt als Argument bleibt darum unmaskiert, weil die Interpolation erst im Formatter passiert:
  log.warning("Fehler: %s", OSError("pw=geheim123 hash=$6$abcd$..."))  ->  vollstaendig im Klartext im Log
  log.warning("Pfad: %s", Path("/tmp/geheim123"))                      ->  ebenso
Solche Aufrufe gibt es reichlich, u.a. `logging_setup.py:140` selbst (`root.warning("Logdatei konnte nicht angelegt werden: %s", exc)`).

Empfohlen statt (bzw. zusaetzlich zu) der exc_text-Reparatur im Filter: die Maskierung ans Formatieren haengen.

  class _RedactingFormatter(logging.Formatter):
      def format(self, record):
          return _filter._scrub(super().format(record))

und in `setup_logging` beide Handler damit bestuecken (`c

## 40. [medium/secrets] Der neue Anzeigen-Schalter fuer Passwoerter ist unsichtbar und wird nie zurueckgesetzt
DATEI src/archcustomiser/gui/widgets/fields.py:200
BESCHREIBUNG `_anzeigeschalter` legt eine QAction nur mit `setText('Anzeigen')` an und haengt sie mit `edit.addAction(..., TrailingPosition)` ins Feld. QLineEdit zeichnet fuer solche Aktionen ausschliesslich das Icon, nie den Text -- und ein Icon ist nicht gesetzt (in assets/icons gibt es auch keines fuer 'Auge'). Empirisch nachgemessen: der Knopfbereich (22x18 px am rechten Rand) enthaelt ohne Icon genau eine Farbe, also nichts Gezeichnetes; mit Icon zwei. Der im Commit angekuendigte Schalter existiert damit nur als unsichtbare Klickflaeche, auffindbar allein ueber den Tooltip. Zweitens wird der aufgedeckte Zustand nie zurueckgenommen: nicht beim Seitenwechsel, nicht bei Fokusverlust, nicht beim Start des Baus, und `FieldRow.leeren()` (nach dem Laden eines Profils) setzt nur den Text zurueck, nicht den EchoMode. Drittens schaltet Qt im Normal-Modus das Kopieren im Feld frei -- im Password-Modus ist 
FIX Drei Luecken im Vorschlag:

a) Icon-Weg: `icons.cache_leeren()` in `design/theme.py:67` leert nur den Zwischenspeicher, es setzt kein bereits an einer QAction haengendes QIcon neu. Wer ueber `load_icon()` geht, muss - wie die Sidebar - an `ThemeManager.themeChanged` haengen und `aktion.setIcon(load_icon("eye", farbe, 16))` erneut aufrufen, sonst bleibt das Auge nach einem Designwechsel in der alten Farbe (im Dunkelmodus dann faktisch wieder unsichtbar). Einfacher und zur bestehenden Struktur passender: statt der QAction einen checkbaren `QPushButton` mit deutschem Text ("Anzeigen"/"Verbergen", `setProperty("variant", "ghost")`) neben das Feld legen - genau der Weg, den `FieldRow` fuer `browse` schon hat (`fields.py:69-78`, `_erzeuge_widget` gibt `(widget, browse)` zurueck). Das braucht kein neues Asset, keine Neufaerbung, ist im Tab-Fokus erreichbar (der QLineEditIconButton ist `NoFocus`) und bleibt in `gui/design/` sauber, weil das QSS die Variante schon kennt.

b) Reset an der falschen Stelle: `FieldRow.leeren()` wird in `pages/form.py:196` nur aufgerufen, wenn `not self.store.has_

## 41. [medium/secrets] Profil speichern lockert die Rechte des gewaehlten Zielverzeichnisses auf 0755
DATEI src/archcustomiser/core/profiles.py:201
BESCHREIBUNG `_write_atomic` ruft bedingungslos `ensure_dir(path.parent, mode=0o755)`. `ensure_dir` (paths.py:107-115) legt das Verzeichnis nicht nur an, sondern macht unter POSIX in jedem Fall `path.chmod(mode)` -- auch wenn es laengst existiert. Der Pfad kommt in der neuen `ProfileActions.speichern` (actions.py:112-121) aus `QFileDialog.getSaveFileName`, ist also ein beliebiges Benutzerverzeichnis. Das Programm aendert damit Rechte an einem Verzeichnis, das ihm nicht gehoert; ueberall sonst benutzt der Code bewusst die restriktive Vorgabe 0700.
FIX The proposed fix is right in direction but its first variant is dangerous; take the second.

Do NOT change `ensure_dir` to "chmod only when newly created". Four other call sites (history.py:88, logging_setup.py:128/170, cache.py:87/267, backend_pacman.py:220) rely on the current repair-on-every-call behaviour to keep the app's own state/cache/history directories at 0700 even when they already exist from an earlier version or were created with a loose umask. Making the chmod create-only would silently stop repairing those. The "mkdir without exist_ok in try/except FileExistsError" variant additionally breaks `parents=True`: intermediate directories would get the umask default instead of `mode`.

Instead:

1. core/profiles.py:201 — do not touch the permissions of a directory the program does not own:
   `path.parent.mkdir(parents=True, exist_ok=True)`
   (drop the `ensure_dir` import if it becomes unused). The profile file itself is written via NamedTemporaryFile + os.replace, so it lands at the tempfile's 0600; if a readable profile is wanted, set the file mode explicitly (`os.chmod(h

## 42. [medium/ux-a11y] text_subtle fails the 4.5:1 text threshold in both palettes
DATEI src/archcustomiser/gui/design/tokens.py:155
BESCHREIBUNG `text_subtle` = `#7c8087` (dark) / `#8a9099` (light). Computed: dark on `surface` 4.07:1, on `surface_alt` 3.68:1; light on `surface` 3.22:1, on `surface_alt` 2.82:1, on `bg` 3.00:1. It is used for enabled, informative text - not only for disabled controls (which would be exempt): sidebar labels of UEBERSPRUNGEN and GESPERRT steps (sidebar.py:188-189, drawn on the `surface` rectangle painted at line 128), the branding preview tile captions and the entire key column of the Identitaet tile (branding.py:104-111, 306), and `QPalette.PlaceholderText` (theme.py:134), i.e. every placeholder in every input field.
FIX The finding is real, but the proposed hex values are wrong and the single-token fix has a side effect the reviewer missed.

1) The proposed light value fails its own criterion. `#6b7079` against light `surface_alt` (#eef0f3) is 4.36:1, i.e. below the >= 4.5 the fix demands. Minimum acceptable is about `#686d76` (4.56 on surface_alt, 5.20 on surface); `#666b73` (4.70 / 5.36) leaves headroom. The proposed dark value `#8f949b` is fine (4.78 surface_alt, 5.28 surface, 5.87 bg).

2) Simply darkening/lightening `text_subtle` also repaints every disabled control, because the same token feeds qss.py:62 (QPushButton:disabled), :81 (primary:disabled), :113 (QLineEdit/QComboBox/... :disabled) and theme.py:136-144 (QPalette Disabled Text/ButtonText). Those are legitimately exempt from 4.5:1 and are supposed to read as inactive; raising them to 4.5-5.4:1 puts them within a hair of `text_muted` (5.13-6.75) and destroys the enabled/disabled distinction.

Correct fix - split the token:
- Add `text_disabled` to `Palette` (tokens.py:28-48) keeping today's values (#7c8087 dark / #8a9099 light) and poin

## 43. [medium/ux-a11y] Self-drawn cards report no checked state to assistive technology
DATEI src/archcustomiser/gui/widgets/cards.py:67
BESCHREIBUNG `OptionCard` is a bare `QWidget` that only calls `setAccessibleName(option.label)` / `setAccessibleDescription(option.description)`. There is no accessible role, no checked/unchecked state, no group membership, and the name is never updated when the state changes. The same holds for `welcome._Auswahlkarte` (welcome.py:60-62) and `search.Chip` (search.py:38). The replaced `gui/widgets/option_widget.py` used a real `QCheckBox`/`QRadioButton` (`self.button: QAbstractButton = QCheckBox() if mode is SelectionMode.MULTI else QRadioButton()`), which gave Qt's accessibility bridge the role, the checked state and the radio-group relationship for free. The class docstring at cards.py:14-15 claims 'ein Name fuer Vorlesewerkzeuge' - the name is there, the state is not.
FIX Do the state sync properly, and do not let it clobber the description.

1) Preferred: one QAccessibleWidget subclass per card type, registered once via QAccessible.installFactory in gui/app.py (next to the existing style/theme setup, guarded by try/except so a headless offscreen run cannot fail). Keep a module-level reference to the factory callable -- a Python factory that gets garbage-collected takes the bridge down with it.
   - OptionCard -> Role.CheckBox for SelectionMode.MULTI, Role.RadioButton otherwise; state.checked = self._checked or self._auto; state.checkStateMixed = False; state.disabled = self._auto or not self._verfuegbar; state.focusable = True.
   - welcome._Auswahlkarte -> Role.RadioButton, state.checked = self._aktiv.
   - search.Chip -> Role.CheckBox (or Role.Button with state.checked), state.checked = self._aktiv.

2) Regardless of (1), notify after every state change. Setting a property alone leaves an AT on cached text. At the end of set_checked, set_auto and set_availability (and setze_aktiv in welcome/search) call:
     ereignis = QAccessibleStateChangeEvent(

## 44. [medium/ux-a11y] Focus ring on cards is drawn on the widget boundary and clipped to ~1px
DATEI src/archcustomiser/gui/widgets/cards.py:207
BESCHREIBUNG `flaeche` is `QRectF(self.rect()).adjusted(1, 1, -1, -1)`; the focus ring is then drawn at `flaeche.adjusted(-1, -1, 1, 1)`, which is exactly `self.rect()`. A 2px pen centred on that path puts one pixel outside the widget, where QPainter clips it - only about one pixel of ring survives, and it is `p.accent`, the very same colour and position as the 2px selected-state border drawn in `_zeichne_rand` (line 239-242). `welcome._Auswahlkarte` repeats the identical geometry (welcome.py:140-144). `search.Chip` is worse: `if self._aktiv or self.hasFocus()` draws one and the same 1.5px accent ring (search.py:86-91), so an active chip shows no focus change at all.
FIX The "make it visually distinct" half of the proposal is right; the geometry half is wrong in detail and would not fix it.

Why `flaeche.adjusted(1, 1, -1, -1)` with a 2 px pen is not enough: that path is at (2, 2, w-2, h-2), so a 2 px pen covers pixels 1..3 - it still abuts the 2 px accent selection border at 0..2, in the same colour, and just reads as a slightly fatter accent edge. It also keeps `radius + 1`, which is backwards: an *inset* ring needs a *smaller* radius.

cards.py (and identically welcome.py:141-145) - draw a two-tone ring inset from the selection border, with a visible gap:

    if self.hasFocus():
        maler.setBrush(Qt.BrushStyle.NoBrush)
        # Trennspur: hebt den Ring vom 2px-Auswahlrand ab
        maler.setPen(QPen(QColor(p.surface), 2.0))
        maler.drawRoundedRect(flaeche.adjusted(2, 2, -2, -2), radius - 2, radius - 2)
        maler.setPen(QPen(QColor(p.text), 2.0))
        maler.drawRoundedRect(flaeche.adjusted(4, 4, -4, -4), radius - 4, radius - 4)

Points that matter: (a) inset, never `adjusted(-1,-1,1,1)`, so nothing is clipped; (b) `p.text`, not

## 45. [medium/ux-a11y] Severity of banners and toasts is signalled by colour only
DATEI src/archcustomiser/gui/widgets/issue_banner.py:64
BESCHREIBUNG `_Zeile.paintEvent` maps error/warning/info onto `p.danger`/`p.warning`/`p.info` and renders them as a 4px left stripe plus a 12%-alpha tint - the message text itself is identical `p.text` in all three cases, with no icon, no prefix word and no shape difference. `toast.Toast.paintEvent` (toast.py:85-99) does exactly the same for INFO/ERFOLG/WARNUNG/FEHLER. Reinforcing this, `build.py:429-431` instructs by colour alone: 'Der Bau kann so nicht starten. Die rot markierten Punkte muessen zuerst behoben werden.' (WCAG 1.4.1). The neighbouring `AnimatedCheck` does it right - check / cross / exclamation mark are distinct shapes (checkmark.py:138-156).
FIX Scope the fix to the banner, and add what the proposal omits:

1. `issue_banner.py` — in `_Zeile.__init__`, prepend the severity word to the visible text and expose it to assistive tech, which the proposed fix does not mention:
   `wort = {"error": "Fehler", "warning": "Warnung", "info": "Hinweis"}.get(issue.severity, "Hinweis")`
   then `text = QLabel(f"{wort}: {issue.message}")` (or a separate bold prefix label so it can be styled), plus
   `self.setAccessibleName(wort)` and `self.setAccessibleDescription(issue.message)`. Without the accessible-* calls a screen reader still gets only the bare message — colour and a painted glyph are both invisible to it, so painting a glyph in the stripe is a *supplement*, not a substitute.
2. `toast.py` — same prefix in the `Art` → colour map area is worth adding pre-emptively, but note only `Art.INFO` and `Art.ERFOLG` are ever constructed today (main_window.py:225/350/373/382) and both texts already read unambiguously; prefixing "Erfolg:"/"Hinweis:" on those would be noise. Better: leave the two used variants alone and add the prefix only for `Ar

## 46. [medium/ux-a11y] Welcome cards position text in hard-coded pixels - overlap and clipping at large fonts
DATEI src/archcustomiser/gui/pages/welcome.py:63
BESCHREIBUNG `_Auswahlkarte` is the only widget in the new UI that ignores `QFontMetricsF` entirely: `setFixedHeight(self._hoehe())` with `space.md * 2 + 20 + (zeilen - 1) * 18` (line 65-68), marker at `mitte_y = space.md + 9`, title baseline at `mitte_y + 5`, description baseline at `mitte_y + 24` (lines 147-171). Both `drawText` calls use the point overload, so there is neither a clipping rectangle, nor word wrap, nor eliding. Everything else in the codebase (`sidebar.Kopfzeile._hoehe_setzen`, `cards.OptionCard.sizeHint`, `common.CodeBlock`) derives its box from the font - the typo.py docstring is explicitly about this class of bug.
FIX Rebuild `_Auswahlkarte` the way `OptionCard` already works, and keep the wrapping the old `_Choice`/`HintLabel` had - do not settle for eliding, or the Minimal template's description ("... Guter Ausgangspunkt fuer Server.") loses its second half.

1. Height from metrics, not constants:
   `titel = QFontMetricsF(schrift(BODY, fett=True)); klein = QFontMetricsF(schrift(CAPTION))`
   `hoehe = werte.space.md*2 + titel.height() + (werte.space.xs + klein.height()*2 if self.text else 0)`
   (two caption lines, matching `OptionCard.sizeHint`, so a wrapped description still fits).
2. Marker: centre it on the title line - `mitte_y = werte.space.md + titel.height()/2`, and replace the magic `+16` gap with `werte.space.sm + 8` so the text column is token-derived too.
3. Title: draw into `QRectF(links, oben, breite, titel.height())` with `AlignLeft|AlignVCenter` and `titel.elidedText(self.titel, Qt.TextElideMode.ElideRight, breite)`, where `breite = self.width() - links - werte.space.md`.
4. Description: draw into `QRectF(links, oben + titel.height() + werte.space.xs, breite, klein.height()*2)` w

## 47. [medium/ux-a11y] Toast text is single-line, capped at 520px and never elided
DATEI src/archcustomiser/gui/widgets/toast.py:55
BESCHREIBUNG `Toast.setFixedSize(self._gemessene_groesse())` clamps the width to `min(max(breite, 220), 520)` (line 65) and the height to exactly one line (line 66). `paintEvent` then draws the message with `AlignLeft | AlignVCenter` and no `TextWordWrap`, no `elidedText` (lines 108-121), so anything longer than the box is cut off flush, without an ellipsis. The longest current message plus its action ('Profil geladen -- alle Schritte sind eingestellt.' + 'Zur Zusammenfassung', main_window.py:350-355) already measures close to 520px at the default font.
FIX The finding is real, but the proposed fix has one part that cannot work and one part that would leave the bug in place at large fonts.

(a) Drop `setToolTip` as the fallback. Line 51 sets `WA_TransparentForMouseEvents` whenever `aktion` is empty — i.e. exactly for the truncating `"Profil gespeichert: ..."` toast — so the widget receives no mouse events and no tooltip can ever appear. Toasts also disappear after ANZEIGEDAUER_MS = 4500 ms, so hover-to-reveal is not a real recovery path for any toast.

(b) The cap must become font- and host-relative, not a new hard number. Replace `min(max(breite, 220.0), 520.0)` with something like:
    zeichen = metrik.averageCharWidth()
    hoechstens = min(self.parent().width() - werte.space.xl * 2, zeichen * 70)
    breite = min(max(breite, zeichen * 26), max(hoechstens, zeichen * 26))
so 520 stops being a pixel constant that a 11pt GNOME font or Windows 125% text scaling walks straight through, and the toast can never be wider than its host (ToastHost._anordnen line 200 computes x = fenster.width() - toast.width() - space.xl and would push it off-

## 48. [medium/ux-a11y] Toast action is mouse-only and invisible to assistive technology
DATEI src/archcustomiser/gui/widgets/toast.py:51
BESCHREIBUNG `Toast` sets `WA_TransparentForMouseEvents` to false when it has an action but never sets a focus policy, never handles key events, and has no accessible name; the action fires from `mousePressEvent` only (line 73-76) and disappears after `ANZEIGEDAUER_MS = 4500`. The whole widget is the hit target even though only the right-hand accent-coloured word looks clickable. This is the sole offered path to the summary after loading a profile (main_window.py:350-355), and the alternative - clicking the sidebar - is mouse-only too (see the sidebar finding).
FIX The finding is real but the proposed fix is partly wrong — `StrongFocus` on a toast is the wrong instrument.

Why the proposed fix fails: `StrongFocus` includes `TabFocus`, so a toast that appears for 4.5 s would be spliced into and then ripped out of the window's tab order, and `_schliessen` → `deleteLater` (toast.py:180-187) would destroy a focused widget, dumping focus somewhere unpredictable. Worse, "do not auto-dismiss while it has focus" is circular: a keyboard user cannot know an unannounced widget appeared in the bottom-right corner, so they cannot tab to it within 4.5 s to trigger the no-dismiss rule. And the click target cannot be narrowed with `WA_TransparentForMouseEvents` (all-or-nothing) — `setMask` would also clip the painting.

Do this instead:

1. Announce it to AT, which is what actually makes it visible to a screen reader. In `Toast.__init__` set `setAccessibleName(f"{text} {aktion}".strip())`, and in `ToastHost.zeige`, right after `toast.show()`, fire an alert so it is read as a live region: `QAccessible.updateAccessibility(QAccessibleEvent(toast, QAccessible.Even

## 49. [medium/ux-a11y] Password reveal toggle has no icon and is therefore invisible
DATEI src/archcustomiser/gui/widgets/fields.py:200
BESCHREIBUNG `_anzeigeschalter` builds a `QAction` with `setText("Anzeigen")` and a tooltip, then adds it with `edit.addAction(aktion, QLineEdit.ActionPosition.TrailingPosition)`. The side widget Qt creates for a line-edit action renders the action's *icon*, not its text; with a null icon the button occupies space inside the field but paints nothing. The module docstring (line 12-14, 201) promises 'Ein Auge im Feld, mit dem sich das Passwort kurz zeigen laesst', and `widgets/icons.py` already provides colourised SVG loading via `load_icon()`.
FIX The direction is right (the action needs an icon), but the proposed fix as written is buggy and incomplete:

1. Crash: `load_icon()` returns `None` for a missing or invalid SVG, and `QAction.setIcon(None)` raises `TypeError: called with wrong argument types` (verified on PySide6 6.11.2). `aktion.setIcon(load_icon("eye", groesse=16))` would blow up the whole "Benutzerkonto" page the moment the asset is absent or the renderer fails. Guard it:
   `if (symbol := load_icon("eye", groesse=16)) is not None: aktion.setIcon(symbol)`.

2. Stale colour after a theme switch. `icons.load_icon()` bakes the token colour into the pixmap; `ThemeManager.apply()` (design/theme.py:65-68) clears the icon cache and emits `themeChanged`, but **nothing in the repo connects to `themeChanged`** (grep: one `Signal` declaration, one `emit`, zero consumers). A QIcon set once on the QAction therefore keeps the old colour and becomes near-invisible again after hell/dunkel. The fix must re-load the icon on `themeChanged` (FieldRow connects and re-sets it) or reload it inside `umschalten()` plus on theme change.

3.

## 50. [medium/ux-a11y] Post-build warnings never get their warning colour (property set after polish)
DATEI src/archcustomiser/gui/pages/build.py:568
BESCHREIBUNG `self.ergebnis_hinweise.setProperty("rolle", "warnung")` is executed in `_beendet`, long after the label was created and polished in `_ergebnisseite()`. Qt only re-evaluates `QLabel[rolle="warnung"]` from qss.py:292 after an `unpolish`/`polish` cycle - which every other site in the codebase performs (`SummaryPage._verdict_rolle`, summary.py:171-174; `FieldRow.zeige_meldung`, fields.py:104-106; `_Abschnitt.setze`, iso_panel.py:65-68). This one call site forgot it, so the warnings render in ordinary body colour.
FIX Factor the repolish into one helper and use it at all four sites; also drive visibility and role from whether there are hints.

1) `src/archcustomiser/gui/widgets/common.py` (build.py already imports from it at build.py:50):

    def setze_rolle(widget: QWidget, rolle: str) -> None:
        """Eine geaenderte ``rolle`` wirkt erst nach einem Neupolieren."""
        if widget.property("rolle") == rolle:
            return
        widget.setProperty("rolle", rolle)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

2) `build.py:587-588` — replace with:

        self.ergebnis_hinweise.setText("\n".join(f"- {h}" for h in hinweise))
        setze_rolle(self.ergebnis_hinweise, "warnung" if hinweise else "")
        self.ergebnis_hinweise.setVisible(bool(hinweise))

Clearing the role (not just hiding) matters because `_zuruecksetzen` reuses the same page for a second build; leaving `rolle="warnung"` on a then-empty label leaves stale state. The `!=` guard mirrors `iso_panel._Abschnitt.setze` and avoids a needless repolish.

3) Optional but better than the reviewer

## 51. [medium/ux-a11y] Help link is not reachable by keyboard and hard-codes 'Arch-Wiki'
DATEI src/archcustomiser/gui/pages/base.py:119
BESCHREIBUNG `add_help_link()` creates a `QLabel` with an `<a href>` and `setOpenExternalLinks(True)` but leaves the default `textInteractionFlags`, which for QLabel is `Qt.LinksAccessibleByMouse` only. Without `LinksAccessibleByKeyboard` the label never takes focus and the link cannot be followed with the keyboard. Separately, the caption 'Weitere Informationen im Arch-Wiki' is hard-coded for whatever `category.help_url` the catalog supplies, so a YAML pointing anywhere else is mislabelled.
FIX Two independent changes in `src/archcustomiser/gui/pages/base.py:116-124`.

1) Keyboard access (fix as proposed, add `Qt` to the imports from `PySide6.QtCore`):

    link.setTextInteractionFlags(
        Qt.TextInteractionFlag.LinksAccessibleByMouse
        | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    )

Verified: this alone yields focusPolicy StrongFocus, so the label enters the Tab chain and QLabel paints a focus rect on the anchor; Enter then activates it through `setOpenExternalLinks(True)`. Give it an `setAccessibleName(...)` too, matching what `free_packages.py:114` already does for its editor.

2) Caption. Prefer the no-schema-change route: drop the source name and keep it German and catalog-neutral, e.g.

    from urllib.parse import urlparse
    host = urlparse(self.category.help_url).netloc.removeprefix("www.")
    link = QLabel(f'<a href="{self.category.help_url}">Weitere Informationen ({host})</a>')

That fixes the "Zusaetzliche Pakete" mislabel immediately and stays correct for any future URL. If a curated label is wanted instead, `help_label` is a three-file c

## 52. [medium/ux-a11y] Documented narrow-window layout does not exist; preview cannot be collapsed
DATEI src/archcustomiser/gui/pages/form.py:51
BESCHREIBUNG `SCHMAL_AB = 1100` with the comment 'Unter dieser Fensterbreite steht die Vorschau ueber statt neben dem Formular' is defined and never read anywhere in the tree (grep: single hit, the definition). `_aufbauen` always builds a horizontal `QSplitter` and additionally sets `setChildrenCollapsible(False)` (line 115), so the preview can never be pushed away. Both children are `QScrollArea`s with `ScrollBarAlwaysOff` (line 102), which means content wider than the viewport is clipped rather than scrollable. The window minimum is `min(1000, 0.9*screen)` (main_window.py:86) and the body additionally carries the sidebar and the 240-360px ISO panel (iso_panel.py:82-83).
FIX The proposed fix is directionally right but has one bug and misses the root cause.

Bug in the proposal: `resizeEvent` on `CatalogFormPage` reports the PAGE width, not the window width. The page width is the window width minus the sidebar (~214), the ISO panel (240-360) and ~64 px of margins/spacing — 350-500 px less. Comparing that against `SCHMAL_AB = 1100` would leave the page permanently in narrow mode on every realistic window size. Either compare `self.window().width()` against 1100, or retune the constant to a page-width value (~700) and rename it so the comment matches what is measured.

Also missing from the proposal: an idempotence guard. `setOrientation` re-triggers layout, so the handler must return early when the orientation is already correct, and must re-apply `setSizes` afterwards (splitter sizes are per-orientation and are not carried across a flip).

Better root-cause fix, smallest first:
1. `widgets/fields.py:64-67`: `self.label.setWordWrap(True)`. The QFormLayout already uses `RowWrapPolicy.WrapAllRows`, so the label owns its own row and wrapping costs nothing — b

## 53. [medium/ux-a11y] Fixed row heights and column widths in self-painted widgets clip at 125-150% font scaling
DATEI src/archcustomiser/gui/widgets/sidebar.py:28
BESCHREIBUNG Several self-painted widgets scale their fonts through `typo.schrift()` but keep their boxes in constant pixels: `ZEILENHOEHE = 34` for sidebar rows whose labels use `schrift(BODY)`; the sidebar notice box is `metrik.height() * 3` tall and anchored to the bottom (sidebar.py:263-277) with no eliding; `Kopfzeile._hoehe_setzen` fixes the subtitle to exactly two caption lines (sidebar.py:296-301); `branding._Kachel` uses 18/19/20/21/22px row constants and a 118px key column (branding.py:105-131, 300-321); `build.py:268` caps the phase list at `setMaximumWidth(260)` with non-eliding `QLabel`s; `summary.py:70,87` sets 280px/420px tree column widths. `common.passende_mindestgroesse` and `CodeBlock` show the intended pattern (measure, don't guess).
FIX Re-anchor to previews/branding.py:105 (_Kachel._rahmen) as the primary site, with sidebar.py:296 (Kopfzeile) second; drop the sidebar ZEILENHOEHE, build.py:268 and summary.py:70/87 items entirely, and re-file the sidebar notice separately as a plain "notice does not fit its box, at any scale" defect.

1. branding.py: replace every pixel constant with a metric. In _rahmen compute kopf_h = QFontMetricsF(schrift(CAPTION, fett=True)).height() and hinweis_h = QFontMetricsF(schrift(CAPTION)).height(), use those for the header/hint rects AND for the inner-rect subtraction, and elide self.titel/self.hinweis to the rect width. In _Bootmenue derive the title box from QFontMetricsF(schrift(BODY, fett=True)).height(), the entry row from QFontMetricsF(schrift(CAPTION)).height() and the advance from that + space.xs, and derive the 32/26/22 insets from the same numbers instead of literals. In _Identitaet derive row height and advance the same way, and size the key column from max(QFontMetricsF(schrift(CAPTION)).horizontalAdvance(k) for k, _ in self.zeilen) clamped to ~45% of the inner width, elidin

## 54. [medium/ux-a11y] Field error messages repeat the label in the banner
DATEI src/archcustomiser/gui/pages/form.py:322
BESCHREIBUNG `_alles_pruefen` writes the row message as `f"{spec.label} wird benoetigt."` (line 232), and `_feldfehler_melden` then builds the banner text as `f"{zeile.spec.label}: {zeile.meldung.text()}"` (line 322), prefixing the label a second time.
FIX Stop reading the message back out of the QLabel; keep the reason as data next to `_valid`, and let each surface compose its own wording.

In `CatalogFormPage`:
- add `self._gruende: dict[str, str] = {}` beside `self._valid`;
- in `_alles_pruefen`, whenever a field is marked invalid, record the bare reason under the id that is marked invalid, and show the same bare reason on the row:
  - required: `grund = "Dieses Feld wird benoetigt."` (row shows it unprefixed; the label already stands directly above the row in `FieldRow`);
  - confirm mismatch: record the reason under the id whose `_valid` entry is set — either set `self._valid[spec.confirm_field] = False` so the invalid id and the row carrying the message are the same, or store `self._gruende[spec.id] = "Die beiden Eingaben stimmen nicht ueberein."` explicitly;
  - validator: `self._gruende[spec.id] = ergebnis.message`;
  - clear `self._gruende.pop(spec.id, None)` on the valid/inactive branches so no stale reason survives.
- in `_feldfehler_melden`, build `message=f"{zeile.spec.label}: {self._gruende.get(spec_id, 'Diese Eingabe ist

## 55. [medium/ux-a11y] German orthography and register are inconsistent between dialogs
DATEI src/archcustomiser/gui/widgets/wsl_dialog.py:74
BESCHREIBUNG `wsl_dialog.py` is the only file under `gui/` that contains real umlauts (grep for [aouAOUss] with diacritics: 11 hits, all in this file); every other user-visible string in the GUI uses ASCII transliteration ('fuer', 'Zurueck', 'oeffnen', 'anschliessend'). The mixture is visible inside one window: the title 'Linux fuer den ISO-Bau' (line 61) and the status text 'Linux-Untersystem wird geprueft ...' (line 146) sit next to 'Windows kann das nicht leisten', 'ausführen', 'Anschließend in Arch:' and 'Zurück hierher' (lines 76-81, 221-238). The same file also carries the only informal address in the application - 'die fertige ISO landet anschließend wieder in deinem Windows-Ordner' (line 268) - while the rest of the UI is impersonal ('Die bisherige Auswahl wird dabei zurueckgesetzt.').
FIX The finding stands; the proposed fix has the wrong scope and would make things worse.

Why the proposal fails: converting "gui/ only" to proper umlauts leaves the catalog untouched, and per project invariant 2 the catalog supplies most of the visible text. `src/archcustomiser/data/catalog/**/*.yaml` contains zero umlauts and hundreds of transliterations ("Eine vollstaendige Arbeitsumgebung...", "Aufgeraeumt und stark Wayland-zentriert", "Vertrautes Layout mit klassischem Startmenue", "gut fuer aeltere Hardware" — 26 such lines in 20-desktop.yaml alone, across 15 category files). The result would be umlaut chrome ("Zurück", "Größe") wrapped around transliterated card labels — a more prominent mixture than today's.

Recommended (cheap, one file, zero risk) — normalize the outlier to the established ASCII convention:
- wsl_dialog.py lines 76, 78, 221, 228, 234-236, 248, 256, 267-268: "für"->"fuer", "löst"->"loest", "ausführen"->"ausfuehren" (matching line 198 in the same file), "öffnet/öffnen"->"oeffnet/oeffnen", "Zurück hierher"->"Zurueck hierher" (matching main_window.py:182), "läuft"

## 56. [medium/tests] The build lock is only tested by calling the private setter directly; the signal wiring and closeEvent are untested
DATEI tests/test_gui_window.py:236
BESCHREIBUNG `test_a_locked_build_leaves_only_the_build_step_reachable` calls `window._sperre_setzen(True)` by hand. On the page side, `test_starting_the_build_locks_the_navigation` (test_build_page.py:161) checks that `BuildPage` *emits* `laufendGeaendert(True)` — against a `FakeFlow`/`FakeJob`, on a page that is not the window's. Nothing tests the connection between them (`main_window.py:204`), so deleting that `connect` line leaves both tests green while a running build no longer locks navigation. Likewise untested: `MainWindow.closeEvent` (main_window.py:395-426) in its entirety — that a running build makes the window refuse to close, that answering Yes calls `job.cancel()` and `job.wait(30000)`, that `actions_.darf_beenden()` can veto, and that `motion.stop_all()` / `toasts.schliesse_alle()` / `controller.cancel()` run on the way out. `BuildPage.darf_schliessen` is tested in isolation (test_buil
FIX The direction is right (test the signal, not the setter), but the proposed fix as written does not run and one assertion is weak. Corrected:

1. `FakeJob` has no `wait()`. main_window.py:417 calls `job.wait(30000)`, so `test_confirming_the_close_cancels_the_job` dies with AttributeError. When moving the doubles to conftest.py, add `def wait(self, ms=30000): self.wait_calls.append(ms); return True` and assert `job.wait_calls == [30000]` — that pins the "don't let Qt destroy a running thread" fix, which is the whole point of the branch.

2. Grab the right page. `window._pages["build"]` is the *category* form page (test_gui_window.py:331 already uses it that way); the BuildPage is `window.build_page` == `window._pages["iso"]` (navigation.py:29 `BUILD_ID = "iso"`). A test that grabs `_pages["build"]` silently asserts nothing.

3. Assert the property, not a proxy. `not window.btn_weiter.isEnabled()` is also true when the page is incomplete or the step is last. Assert `window.model.locked` plus `not window.model.anklickbar(window.model.step("apps"))` (keep the button check as a secondary, 

## 57. [medium/tests] A category whose page_type has no builder becomes an unreachable step, and the render test hides it
DATEI src/archcustomiser/gui/main_window.py:200
BESCHREIBUNG `_seiten_erzeugen` does `seite = factory.create(...); if seite is None: continue` — the step stays in `self.model.steps` with no widget behind it. `_gehe_zu` then logs "Kein Widget fuer Schritt" and returns (main_window.py:302-305) *after* `self.model.betreten(step_id)` has already moved `current_id`: the model now points at a step whose page does not exist while the stack still shows the previous page, `_aktualisieren()` is never called, and the header caption is stale. `test_every_page_renders_offscreen` (test_gui_window.py:465) cannot catch this — it calls `window._gehe_zu(schritt.id)` and then grabs `window.stack.currentWidget()`, which is still the *previous* page and grabs fine, so the assertion passes for a step that rendered nothing. `PageFactory.create`'s None branch (factory.py:38-41) has no test at all.
FIX Keep parts 2 and 3 of the proposal, replace part 1, and add the code fix the proposal omits.

1. WRONG AS PROPOSED — do not make the step silently disappear. `test_a_category_without_a_builder_gets_no_step` (monkeypatch `create` → None, assert the id is absent from `model.steps`) drives the code toward dropping a catalog-declared category from the UI behind a `log.error`. That trades one silent failure for another and collides with invariant 8 ("never remove a feature silently"): the user loses a whole category with no visible sign. A missing builder is a programming error, not a runtime condition. Guard it statically instead, which catches the exact failure scenario at the commit that introduces it and needs no monkeypatching and no behaviour change:

    def test_every_page_type_has_a_builder(store, paketdienst) -> None:
        from archcustomiser.core.catalog import PageType
        from archcustomiser.gui.packages_worker import PackageController
        from archcustomiser.gui.pages.factory import PageFactory
        fabrik = PageFactory(store, PackageController(paketdienst))
  

## 58. [medium/tests] Untested new GUI modules: toast, sidebar, page_stack, intro, issue_banner, export_dialog, free_packages
DATEI src/archcustomiser/gui/widgets/toast.py:169
BESCHREIBUNG Grepping tests/ for each new module name returns nothing for `widgets/toast.py`, `widgets/sidebar.py` (the five-state step list, the headline replacement for the deleted step_sidebar.py), `widgets/page_stack.py`, `widgets/intro.py`, `widgets/issue_banner.py`, `widgets/export_dialog.py`, `pages/free_packages.py` and `pages/factory.py`. Two of these carry regressions the commit message explicitly claims to have fixed, with no test to hold them: (a) the toast clock, which the code comment at toast.py:164-168 says must be parented to the toast rather than fired via `QTimer.singleShot` so it cannot touch a deleted C++ object after the window closes — nothing verifies the parenting; (b) the sidebar's `Status.UEBERSPRUNGEN` rendering, which is verified only at model level (test_navigation.py) and never as a visible state. `IntroOverlay`/`zeige_einmal` additionally carries a module-level `_gezei
FIX Narrow the finding to what is actually uncovered, and fix the tests so they match the code.

Scope: drop free_packages.py and factory.py (covered — see test_the_refresh_button_comes_back_after_a_failure, test_summary_produces_a_plan, test_every_page_renders_offscreen), drop the invariant-8 paragraph (test_build_page.py guards preflight/checkmarks/log; test_navigation.py + test_gui_window.py guard the sidebar; test_gui.py guards the option-widget behaviour), and drop the intro-poisoning rationale (intro.py:123 returns before setting `_gezeigt` whenever motion.is_reduced(), which motion.py:74-79 forces true offscreen and under ARCHCUSTOMISER_MOTION=off). Severity: low, and add actions.py to the list — it is the genuinely untested module the reviewer missed.

tests/test_widgets.py, reusing the qapp/settings/theme fixtures of test_gui_window.py:

1. test_a_toast_outlives_nothing — `host = ToastHost(w); t = host.zeige("x", ToastArt.INFO)`; assert `any(isinstance(c, QTimer) and c.parent() is t for c in t.children())` (this is the actual regression guard for toast.py:158-163); then `w.setPa

## 59. [low/parity] Sidebar step numbers start at 2 because the welcome step is counted
DATEI src/archcustomiser/gui/widgets/sidebar.py:134
BESCHREIBUNG The old StepSidebar numbered only the catalog categories: `for position, category in enumerate(categories, start=1)` over `self._order`, which contained categories exclusively, giving "1. Grundkonfiguration", "2. Desktop", ... The new sidebar enumerates `self.model.steps`, whose first element is the welcome step (navigation.py:78), so the first category receives `nummer == 2`. `_zeichne_zeile` only prints the number for `Art.CATEGORY` (line 203), so the visible list reads "Start", "2. Grundkonfiguration", "3. Desktop" ... - the number 1 is never shown and every category is off by one. `_natuerliche_breite` uses the same enumeration, so the width is consistent but equally offset.
FIX The proposed fix is right in substance; make it precise:

In `paintEvent`, do not derive the number from the model index. Keep a counter that only advances on category steps:

    oben = self._listenanfang()
    nummer = 0
    for schritt in self.model.steps:
        if schritt.art is Art.CATEGORY:
            nummer += 1
        zustand = self._zustaende.get(schritt.id, Status.OFFEN)
        self._zeichne_zeile(maler, werte, p, schritt, nummer, oben, zustand)
        oben += ZEILENHOEHE

In `_natuerliche_breite`, mirror it and also drop the prefix for non-category rows, otherwise "Start"/"ISO erstellen" are still measured as if numbered:

    nummer = 0
    breiten = []
    for schritt in self.model.steps:
        if schritt.art is Art.CATEGORY:
            nummer += 1
            breiten.append(metrik.horizontalAdvance(f"{nummer}. {schritt.titel}"))
        else:
            breiten.append(metrik.horizontalAdvance(schritt.titel))
    breite = max(breiten, default=160.0)

Do not "fix" the remaining asymmetry between the numbering and the progress line: the counter must keep numberin

## 60. [low/parity] A category without a registered page type becomes an unreachable phantom step
DATEI src/archcustomiser/gui/main_window.py:293
BESCHREIBUNG The old `_build_pages` treated "factory returned None" as "this category does not exist": the category was not registered with `setPage`, not added to `self._pages`, and crucially not appended to `self._order`, so it never appeared in the sidebar or in `visible_after`. The new split breaks that coupling: `NavigationModel.aus_katalog` (navigation.py:77-92) adds a Step for every visible category regardless, while `_seiten_erzeugen` (main_window.py:195-201) skips the ones the factory cannot build. The step then exists in the model - it is drawn in the sidebar, counted by `progress()`, considered clickable, and returned by `naechster()`. Worse, `_gehe_zu` calls `self.model.betreten(step_id)` (line 300) *before* looking the widget up (line 302-306), so the model's `current_id` moves to a step that has no widget: the stack keeps showing the previous page, `Kopfzeile` keeps the previous title, 
FIX The direction is right but both halves need adjusting. (1) In _gehe_zu, hoist the lookup above EVERY side effect, including the leave() call, not just above betreten - otherwise a jump to a phantom still runs leave() on the current page and can consume a file dialog for a navigation that never happens:

    def _gehe_zu(self, step_id: str, *, animiert: bool = True) -> None:
        seite = self._pages.get(step_id)
        if seite is None:
            log.error("Kein Widget fuer Schritt %r", step_id)
            return
        aktuell = self._pages.get(self.model.current_id)
        if aktuell is not None and step_id != self.model.current_id:
            if not aktuell.leave():
                return
        richtung = self.model.richtung(step_id)
        self.model.betreten(step_id)
        seite.enter()
        ...

(2) In _seiten_erzeugen, do not remove from self.model.steps while iterating it. Collect the ids and prune afterwards (NavigationModel is a plain dataclass and steps is a plain list, so reassigning is fine even though Step is frozen):

    fehlend: list[str] = []
    fo

## 61. [low/parity] "Eigenes Profil laden" on the welcome page now opens in the bundled template directory
DATEI src/archcustomiser/gui/pages/welcome.py:289
BESCHREIBUNG The old WelcomePage.validatePage opened the file dialog at `str(Path.home())` for the "Eigenes Profil laden ..." choice - deliberately the user's own files, since built-in templates are already offered as cards directly above. The new `leave()` uses `start = str(self.profiles.builtin_dir)`, i.e. the application's own read-only template folder. Note that the header button path (`ProfileActions.laden`, actions.py:64) additionally remembers `settings.letzter_profilordner`, but the welcome page does not consult or update that setting, so the two entry points to the same operation now start in different, both arguably wrong places for a user profile.
FIX The fix as written (`self.settings.letzter_profilordner or str(user_profiles_dir())`) does not compile in place and has two further traps:

1. `WelcomePage` has no `self.settings` — it is built as `WelcomePage(self.store, self.profiles, environment)` (main_window.py:191). That line raises `AttributeError` inside `leave()`, i.e. inside a navigation handler.
2. `user_profiles_dir()` need not exist on a first run. `ProfileActions.speichern` calls `ensure_dir(user_profiles_dir(), mode=0o755)` before its dialog for exactly that reason; `QFileDialog` given a nonexistent start path silently falls back to an arbitrary directory.
3. "Route the welcome page's load through `ProfileActions`" is the right instinct but must not be applied to the template branch: `ProfileActions.lade_datei` unconditionally sets `letzter_profilordner = str(pfad.parent)` (actions.py:94). Sending a builtin template load through it would remember the bundled package folder and re-break the header button. Naive routing also double-fires: `WelcomePage._laden` emits `profileLoaded`, and `ProfileActions.lade_datei` emits i

## 62. [low/parity] The "ISO erstellen" action lost its keyboard mnemonic
DATEI src/archcustomiser/gui/pages/build.py:247
BESCHREIBUNG The old wizard set mnemonics on every button, with an explicit comment explaining why ("ohne sie ueberschreiben eigene Beschriftungen die Akzeleratoren, die Qt sonst selbst vergibt"): `CustomButton1..3`, `NextButton` "&Weiter >", `BackButton` "< &Zurueck", `CancelButton` "&Beenden" and `FinishButton` "&ISO erstellen". MainWindow preserves the first five (main_window.py:154-183), but the Finish action moved to the build page, where none of the buttons carry an ampersand: "Bauumgebung pruefen" (build.py:203), "ISO erstellen" (build.py:247), "Erneut pruefen", "Abbrechen", "Ordner oeffnen", "Protokoll oeffnen". The single most important action of the program is now mouse-only for Alt-navigation.
FIX The proposed letters are safe but the fix is incomplete in two ways.

1. Letters proposed are collision-free (header/footer use l, s, x, B, Z, W; `ISO-Uebersicht` and `Hell / Dunkel` have none):
   - build.py:203 -> `QPushButton("Bauumgebung &pruefen")`
   - build.py:247 -> `QPushButton("&ISO erstellen")`
   `Erneut pruefen` (:252) may also take `p` — it lives on a different QStackedWidget sub-page (`_pruefseite`) than the start button (`_leerseite`), and Qt only dispatches mnemonics to visible, enabled widgets, so the two never coexist.

2. MISSED by the proposed fix: `cancel_button` (build.py:162) is in the build page *header*, i.e. visible at the same time as the footer's `&Beenden`. Qt mnemonic matching is case-insensitive, so `A&bbrechen` would make Alt+B ambiguous (Qt then merely cycles focus between the two instead of triggering). Use a free letter, e.g. `Abbre&chen` (c). Aborting a 40-minute build is the second most important key on that page.

3. Also give the result-page buttons mnemonics so the page is completable by keyboard: `&Ordner oeffnen` (o), `Pro&tokoll oeffnen` (t

## 63. [low/parity] Summary is no longer a commit page - Zurueck from the build step is possible
DATEI src/archcustomiser/gui/pages/summary.py:45
BESCHREIBUNG The old SummaryPage ended its constructor with `self.setCommitPage(True)` and an explicit comment: "Nach dieser Seite beginnt der Build -- Qt blendet den Zurueck-Knopf dann aus. Das ist gewollt: eine halb gestartete ISO-Erzeugung laesst sich nicht durch Zurueckblaettern rueckgaengig machen." The new SummaryPage has no equivalent, and MainWindow enables Zurueck whenever `self.model.voriger() is not None and not self.model.locked` (main_window.py:325). The safety goal is largely preserved by a different mechanism - `BuildPage.laufendGeaendert` sets `model.locked`, and `NavigationModel.anklickbar` then restricts everything to `BUILD_ID` - so a *running* build can no longer be navigated away from. What changed is the state before and after a build: the build step is now freely reachable and leavable, and after a finished build the user can walk back into the configuration pages while the res
FIX Drop the parity framing (there is nothing to document: the old commit page disabled no button, because SummaryPage was the last wizard page and the build ran in modal dialogs launched from `accept()`). Fix the actual defect instead, and treat it as medium, not low, because of the preflight case.

In `src/archcustomiser/gui/pages/build.py`, invalidate the whole page — not just the result view — whenever the configuration underneath it changes:

1. In `BuildPage.__init__`, connect `store.resolutionChanged` to a new `_konfiguration_geaendert` slot.
2. `_konfiguration_geaendert` must do nothing while a build is running (`if self.laeuft or (self.job is not None and self.job.busy): return`) — the running build deliberately works on `config.copy()` — and otherwise call `_zuruecksetzen()` whenever the stack is on SEITE_PRUEFUNG or SEITE_ERGEBNIS. This kills both stale states: the stale preflight `job` (which would otherwise build the old snapshot) and the stale result view.
3. Extend `_zuruecksetzen` to also clear `self._work_dir`, `self._out_dir` and `self._log_path`, and to re-enable `self

## 64. [low/threading] _zuruecksetzen drops the job reference without checking whether its threads are still busy
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/pages/build.py:706
BESCHREIBUNG 'Neue ISO' sets self.job = None unconditionally. From that moment darf_schliessen() returns True (line 719-722) and a pending _freigeben_wenn_ruhig tick unlocks navigation (line 623-628), regardless of whether the previous BuildJob's _CancelThread is still running -- and closeEvent only ever cancels and waits for self.build_page.job, so the abandoned job's threads are simply destroyed with the window. Today the reachable window is small (a cancel issued after controller.run() returned finds _runner already None, so _CancelThread finishes almost instantly), but the code relies on that accident, while BuildJob.cancel() is documented as returning immediately with the work continuing in the background.
FIX The proposed fix is right in direction but wrong in one detail and incomplete in two.

Wrong detail: do NOT let a winding-down job make `darf_schliessen()` return False. That method feeds main_window.py:397-410, which asks the user "Es wird gerade eine ISO gebaut ... Trotzdem beenden?" - a false statement for a background pkill, and it would make the user confirm something they cannot influence. Keep `darf_schliessen()` about `self.job` only.

Correct shape:
1. In `_zuruecksetzen` (build.py:771), before dropping the reference:
   `alt = self.job` / `if alt is not None and alt.busy: self._abklingend.append(alt); alt.cancelled.disconnect(...)` etc. - disconnect the six signals connected in `_bau_starten` (build.py:445-450) so a late emit from the abandoned job cannot overwrite the freshly reset headline/stack. Initialise `self._abklingend: list[BuildJob] = []` in `__init__`.
2. Give BuildPage a `warte_auf_reste(ms: int = 30000) -> None` that iterates `self._abklingend`, calls `job.wait(ms)` and `job.deleteLater()`, then clears the list; call it unconditionally in `MainWindow.closeEvent

## 65. [low/threading] Abandoned BuildJobs, WaitDialogs and clock timers accumulate for the life of the program
DATEI C:/Users/uih48523/Desktop/codes/jasontool/src/archcustomiser/gui/build_flow.py:197
BESCHREIBUNG Every preflight run creates a BuildJob parented to the BuildFlow and never releases it; 'Erneut pruefen' and each new build add another. Each abandoned job keeps a BuildController alive with its config copy, its Resolution, its line deque and a reference to the shared SecretStore. Likewise every run_with_wait creates a WaitDialog parented to the main window that is never deleteLater'd (wait_dialog.py:127-129), and _bau_starten creates a fresh QTimer parented to the page on each build without disposing of the previous one (pages/build.py:462). If the user cancels the wait dialog, its background task keeps running in the global QThreadPool and later calls accept() on the abandoned dialog.
FIX Do not use the proposed deleteLater() calls verbatim - both are unsafe.

1) WaitDialog: `dialog.deleteLater()` at the end of run_with_wait destroys the C++ object while a cancelled _Task is still running; when that task finishes it emits done/failed into the still-connected bound method and `self.accept()` raises RuntimeError ("Internal C++ object already deleted") inside a slot called from a pool thread. Instead drop the Qt ownership and let Python refcounting decide:
    dialog = WaitDialog(...); dialog.exec(); dialog.setParent(None)
    return dialog.result_value, dialog.error
The _Task's signal connection holds the last reference, so the dialog dies exactly when the background work is done, never before. Apply the same `setParent(None)` to the WslSetupDialog at build_flow.py:170, which the finding missed.

2) BuildJob: hold at most one in BuildFlow and only release it when it is idle, otherwise the pool thread running `lambda: job.preflight(...)` (after a cancelled wait) touches a job whose Qt object is gone:
    alt = getattr(self, "_job", None)
    if alt is not None and not al

## 66. [low/catalog-driven] Splash-Groesse als zweite Konstante in der GUI dupliziert
DATEI src/archcustomiser/gui/previews/branding.py:35
BESCHREIBUNG `SPLASH_GROESSE = (640, 480)` dupliziert `core.archiso.branding.SPLASH_SIZE` (core/archiso/branding.py:33), aus dem dieselbe Datei bereits `png_dimensions` importiert. Der Warntext in Zeile 77 schreibt "640x480" zusaetzlich als Literal in den f-String, waehrend core seine Meldung aus der Konstanten formatiert. `_splash_warnung` liest ausserdem mit `Path(pfad).read_bytes()[:32]` die gesamte Datei ein, um 32 Bytes zu brauchen -- und das bei jedem 80-ms-Neuzeichnen, waehrend das eigentliche Bild ueber `_bild` gecacht wird.
FIX 1. Konstante entdoppeln (das ist der eigentliche Befund):
   - Zeile 26: `from ...core.archiso.branding import SPLASH_SIZE, png_dimensions`
   - Zeile 34/35 loeschen (`SPLASH_GROESSE`); keine weitere Referenz im Repo, nicht in `__all__`.
   - Zeile 76/77: `if masse != SPLASH_SIZE:` und `return f"{masse[0]}x{masse[1]} statt {SPLASH_SIZE[0]}x{SPLASH_SIZE[1]} -- wird nicht angezeigt."` -- das Literal im f-String muss mit weg, sonst ist nur die halbe Duplikation beseitigt.

2. Vollstaendigkeit der Aenderung: Die Groesse steht ausserdem als Text in data/catalog/categories/85-branding.yaml:108 und im Docstring core/validation.py:379. Der Katalogtext bleibt bewusst autorisierter Text (Invariante 2: Texte gehoeren ins YAML), gehoert aber in denselben Arbeitsschritt.

3. Dateizugriff: `with open(pfad, "rb") as f: kopf = f.read(32)` statt `Path(pfad).read_bytes()[:32]` ist richtig -- aber ohne den vorgeschlagenen (Pfad, mtime)-Speicher. `_splash_warnung` laeuft nur aus dem entprellten Einzelschuss-Timer, nicht aus `paintEvent`; ein zweiter Cache waere Zustand ohne Nutzen.

4. Statt des vorgesc

## 67. [low/catalog-driven] Widget-Art tag_list wird vom Loader akzeptiert, aber von keiner Oberflaeche umgesetzt
DATEI src/archcustomiser/core/catalog/loader.py:57
BESCHREIBUNG `tag_list` steht in `_VALID_WIDGETS` und im Kommentar von `FieldSpec.widget` (core/catalog/models.py:179), hat aber in `_erzeuge_widget` (gui/widgets/fields.py:151-197) keinen Zweig -- der Fall faellt durch bis zum `QLineEdit` am Ende. Auch die alte Fassung kannte ihn nicht (Gegenprobe an 9ae8041^:gui/pages/form.py). Kein Katalogeintrag benutzt ihn. Das ist derselbe Anti-Fall, den `FileEntry` im Docstring benennt: "Ein Feld, das aussieht als taete es etwas und nichts tut, ist schlimmer als keines."
FIX Vorzugsweise entfernen statt implementieren -- kein Katalog benutzt `tag_list`, und ein halbherziges Tag-Widget wäre neue ungetestete Oberfläche:

1. `src/archcustomiser/core/catalog/loader.py:47-58`: `"tag_list",` aus `_VALID_WIDGETS` streichen. Damit meldet der Loader `widget: tag_list` ab sofort mit der bestehenden `CatalogError` samt Ortsangabe -- laut statt still, genau die gewünschte Wirkung. Das ist keine stille Feature-Entfernung (Invariante 8), weil nie ein Verhalten dahinterstand; ein CHANGELOG-Satz analog zum `source`/`template`-Präzedenzfall gehört trotzdem dazu.
2. `src/archcustomiser/core/catalog/models.py:179`: `| tag_list` aus dem Kommentar zu `FieldSpec.widget` entfernen, damit Kommentar und erlaubte Menge wieder deckungsgleich sind.
3. Statt eines Warn-Logs im Sammelzweig (das dort `line`/`password`/`path` fälschlich träfe) die beiden Listen dauerhaft aneinander binden: in `src/archcustomiser/gui/widgets/fields.py` eine explizite Menge der bedienten Arten pflegen, z. B. `_UMGESETZTE_WIDGETS = frozenset({"line", "combo", "editable_combo", "password", "path", "int", "

## 68. [low/catalog-driven] Optionssuche durchsucht die repr() von PackageRef statt der Paketnamen
DATEI src/archcustomiser/gui/pages/selection.py:281
BESCHREIBUNG `felder = [option.label, option.description, option.id, *option.packages]` haengt `PackageRef`-Objekte in die Liste; `str(feld).lower()` erzeugt daraus "packageref(name='steam', when=..., reason='')". Der Treffer auf den Paketnamen funktioniert nur zufaellig als Teilstring, dafuer matchen jetzt auch die Feldnamen der Dataclass. `option.package_groups` und `option.aur_packages` werden gar nicht durchsucht, obwohl der Docstring "der Paketname ist oft das, was der Benutzer im Kopf hat" als Begruendung nennt.
FIX The proposed fix is correct as far as it goes; use it, with two additions.

selection.py:275-282:

    def _trifft(option: Option, begriff: str) -> bool:
        """Sucht in Beschriftung, Beschreibung UND Paketnamen."""
        felder = (
            option.label,
            option.description,
            option.id,
            *(paket.name for paket in option.packages),
            *option.package_groups,
            *option.aur_packages,
            *option.tags,
        )
        return any(begriff in feld.lower() for feld in felder if feld)

Additions beyond the reviewer's version:
- drop the `str(...)` wrapper: every element is now a real `str`, and keeping `str()` would hide a future type regression of exactly this kind.
- include `option.tags` (core/catalog/models.py:229) — it is a free-form, catalog-driven keyword list and is the natural place a curator puts search synonyms; leaving it out repeats the same "the catalog knows more than the search does" gap.
- `option.aliases` is deliberately NOT included: it holds old refs for renamed options (models.py:263 next to `deprecat

## 69. [low/ux-a11y] Required fields: the asterisk never reaches the accessible name
DATEI src/archcustomiser/gui/widgets/fields.py:65
BESCHREIBUNG `FieldRow` renders the required marker into the visible label (`spec.label + (" *" if spec.required else "")`) and hangs the 'Pflichtfeld' tooltip on the *label* (line 68), while the input widget receives `setAccessibleName(spec.label)` without the marker (line 96) and no tooltip. The label is also not connected via `setBuddy`, so no Alt-mnemonic and no Qt LabelledBy relation exist. The '* Pflichtfeld' legend (form.py:203-211) is a separate label at the bottom of the page.
FIX In FieldRow.__init__ (fields.py:65-98): keep the label tooltip AND additionally set it on the input (self.widget.setToolTip("Pflichtfeld")) instead of moving it, so hovering the visible "*" still explains itself. Set the accessible name as spec.label + (" (Pflichtfeld)" if spec.required else ""). Compose the description from both parts rather than help alone, e.g. teile = [t for t in (("Pflichtfeld" if spec.required else ""), spec.help or "") if t]; if teile: self.widget.setAccessibleDescription(", ".join(teile)). Additionally close the sibling gap the claim misses: extend zeige_meldung()/verstecke_meldung() (L101-110) to re-set self.widget.setAccessibleDescription() with the current message text appended (and removed again on hide), so the validation error ("<Feld> wird benoetigt.") is announced to a screen reader instead of living only in an unrelated QLabel. setBuddy(self.widget) is optional and gives no mnemonic here since the label text contains no "&"; it may be added for completeness but is not what fixes the finding.

## 70. [low/tests] The .sha256 writer is only covered on its happy path
DATEI tests/test_build_page.py:325
BESCHREIBUNG `test_the_checksum_can_be_saved_next_to_the_iso` correctly pins the exact `sha256sum -c` format (`f"{sha}  {name}\n"`) and the `.iso.sha256` name — good. But the three guards in `_sha_speichern` (build.py:684-702) are untested: the early return when `self.sha256` is empty, when `outcome` is None, or when `outcome.iso_path` is None; the `OSError` branch that shows a warning instead of crashing; and the button's temporary "Gespeichert" label with its 1500 ms single-shot restore (another timer that outlives a test — it is parented to the page, which the fixture never really destroys, see the fixture-leak finding). Nor is the pairing in `_beendet` covered: `sha_speichern.setEnabled(bool(sha256) and pfad is not None)` and the `sha_titel`/`sha_block` visibility toggles (build.py:560-563) — i.e. that a build which produced no checksum hides the SHA block rather than showing "nicht berechnet" ne
FIX Fix the line references first: `_sha_speichern` is build.py:749-769, the `_beendet` pairing is build.py:565-569, and the OSError is caught in core/build/verify.py:206-209 (build.py only reacts to a `None` return).

Drop the invented failure scenario — `schreibe_pruefsumme` already refuses an empty checksum and already swallows `OSError`, both independently of the GUI guard, so neither a zero-length file nor a propagating exception is reachable. Keep this purely as "these branches have no test".

Three tests, at the right layer:

1) GUI, empty checksum (tests/test_build_page.py) — assert enabledness and hidden-ness, not `isVisible()`:
```python
def test_no_checksum_means_no_file_and_a_disabled_button(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    ausgang = ergebnis(tmp_path)
    job.finished.emit(ausgang, "")

    assert not page.sha_speichern.isEnabled()
    assert page.sha_titel.isHidden() and page.sha_block.isHidden()

    page._sha_speichern()
    datei = ausgang.iso_path.with_name(ausgang.iso_path.name + ".sha256")

