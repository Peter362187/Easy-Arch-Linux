"""Einstiegspunkt.

Ohne Argumente startet die grafische Oberflaeche. Mit ``--dry-run`` wird ein
Profil auf der Konsole ausgewertet -- nuetzlich fuer Skripte und um die
Konfiguration ohne Bildschirm zu pruefen.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="archcustomiser",
        description="Erstellt individuelle, auf Arch Linux basierende Live-ISOs.",
    )
    parser.add_argument("--dry-run", metavar="PROFIL", help="Bauplan eines Profils ausgeben")
    parser.add_argument("--check-env", action="store_true", help="Bauumgebung pruefen")
    parser.add_argument(
        "--export-profile",
        metavar="PROFIL",
        help="archiso-Profil aus einem Profil erzeugen",
    )
    parser.add_argument(
        "--out",
        metavar="ZIEL",
        help="Zieldatei (.tar.gz) oder Zielverzeichnis fuer --export-profile",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Keine Paketdaten laden -- Namen gelten dann als nicht pruefbar",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Ausfuehrliche Ausgabe")
    parser.add_argument("--no-log-file", action="store_true", help="Nicht in eine Datei protokollieren")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.out and not args.export_profile:
        print("Fehler: --out ergibt nur zusammen mit --export-profile Sinn", file=sys.stderr)
        return 2
    if args.export_profile and args.dry_run:
        print(
            "Fehler: --export-profile und --dry-run schliessen sich aus",
            file=sys.stderr,
        )
        return 2

    from .core.logging_setup import setup_logging

    log_path = setup_logging(verbose=args.verbose, to_file=not args.no_log_file)

    if args.check_env:
        from .core.environment import detect_environment

        environment = detect_environment()
        print(f"Plattform: {environment.platform}")
        print(f"Build moeglich: {'ja' if environment.can_build else 'nein'}")
        print(f"Rechtemodus: {environment.privilege_mode}")
        print(environment.summary())
        for tool in environment.tools:
            mark = "vorhanden" if tool.found else ("FEHLT" if tool.required else "optional, fehlt")
            print(f"  {tool.name:<22} {mark:<16} {tool.purpose}")
        for hint in environment.hints:
            print(f"\nHinweis: {hint}")
        if environment.install_hint():
            print(f"\nInstallieren mit:\n  {environment.install_hint()}")
        return 0 if environment.can_build else 1

    if args.export_profile:
        if not args.out:
            print("Fehler: --export-profile braucht --out", file=sys.stderr)
            return 2
        return _export_profile(Path(args.export_profile), Path(args.out))

    if args.dry_run:
        return _dry_run(Path(args.dry_run), offline=args.offline)

    if log_path:
        # Unter pythonw.exe gibt es kein stdout -- print() ist dort wirkungslos.
        # Der Pfad steht deshalb zusaetzlich im Protokoll selbst.
        print(f"Protokoll: {log_path}")

    _install_crash_handler(log_path)

    from .gui.app import run

    return run(sys.argv)


def _install_crash_handler(log_path: Path | None) -> None:
    """Macht einen Absturz sichtbar, auch ohne Konsole.

    Die Oberflaeche wird ueber ``pythonw.exe`` gestartet, damit kein schwarzes
    Fenster danebensteht. Der Preis: es gibt weder stdout noch stderr. Ein
    Fehler vor dem Start der Ereignisschleife -- ein fehlendes PySide6 etwa --
    blieb dadurch vollstaendig unsichtbar: das Fenster blitzte auf, und nichts
    geschah. Kein Hinweis, keine Meldung, kein Protokolleintrag.

    Der Haken schreibt deshalb in jedem Fall ins Protokoll und versucht
    zusaetzlich, ein Fenster zu zeigen. Schlaegt auch das fehl, bleibt
    wenigstens die Datei.
    """
    logger = logging.getLogger("archcustomiser")

    def behandeln(art, wert, spur) -> None:
        if issubclass(art, KeyboardInterrupt):
            sys.__excepthook__(art, wert, spur)
            return

        logger.critical("Unbehandelter Fehler", exc_info=(art, wert, spur))

        text = (
            "ArchCustomiser wurde durch einen unerwarteten Fehler beendet." + "\n\n"
            + f"{art.__name__}: {wert}"
        )
        if log_path:
            text += (
                "\n\nEinzelheiten stehen im Protokoll:\n"
                + str(log_path)
            )

        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is None:
                QApplication([])
            QMessageBox.critical(None, "ArchCustomiser", text)
        except Exception:
            # Qt ist womoeglich genau das, was gefehlt hat.
            print(text, file=sys.stderr)

    sys.excepthook = behandeln


def _katalog_oder_fehler():
    """Laedt den Katalog und meldet einen Fehler verstaendlich.

    Auf der Kommandozeile fuehrte ein fehlerhaftes Katalog-Overlay
    bisher zu einem Traceback, waehrend der Weg ueber die Oberflaeche
    denselben Fall sauber meldet.
    """
    from .core.catalog import CatalogError, load_catalog

    try:
        return load_catalog()
    except CatalogError as exc:
        print(f"Fehler: Der Optionskatalog ist fehlerhaft: {exc}", file=sys.stderr)
        return None


def _dry_run(profile_path: Path, *, offline: bool = False) -> int:
    from .core.packages import PackageService
    from .core.plan import build_plan, plan_as_text
    from .core.profiles import ProfileError, ProfileService
    from .core.resolver import Resolver

    catalog = _katalog_oder_fehler()
    if catalog is None:
        return 2
    service = ProfileService(catalog)
    try:
        loaded = service.load(profile_path)
    except ProfileError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2

    for issue in loaded.issues:
        print(f"[{issue.severity}] {issue.message}", file=sys.stderr)

    resolution = Resolver(catalog).resolve(loaded.config)
    if offline:
        # Ein Trockenlauf, der Pakete nachlaedt, ist keiner: der Aufruf
        # oeffnete bisher ungefragt Verbindungen zu einem Spiegelserver
        # (rund 9 MB) oder startete pacman als Unterprozess.
        report = None
    else:
        packages = PackageService()
        packages.load()
        report = packages.validate(resolution.package_names)
    plan = build_plan(catalog, loaded.config, resolution, report)
    print(plan_as_text(plan))
    return 0 if plan.can_build else 1


def _export_profile(profile_path: Path, target: Path) -> int:
    """Erzeugt ein archiso-Profil ohne Oberflaeche.

    Ohne Passwort: auf der Kommandozeile gaebe es keinen sicheren Weg, eines
    entgegenzunehmen, und in einem Argument stuende es fuer jeden lesbar in
    /proc. Das Konto wird gesperrt angelegt und laesst sich spaeter mit
    'passwd' freischalten.
    """
    from .core.archiso import DirectorySink, ProfileGenerator, TarSink
    from .core.archiso.errors import ProfileError
    from .core.profiles import ProfileError as ProfileFileError
    from .core.profiles import ProfileService
    from .core.resolver import Resolver

    catalog = _katalog_oder_fehler()
    if catalog is None:
        return 2
    try:
        loaded = ProfileService(catalog).load(profile_path)
    except ProfileFileError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2

    resolution = Resolver(catalog).resolve(loaded.config)
    try:
        generated = ProfileGenerator(catalog, loaded.config, resolution).generate()
    except ProfileError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    as_archive = target.suffix in (".gz", ".tgz") or target.name.endswith(".tar.gz")
    sink: TarSink | DirectorySink
    if as_archive:
        sink = TarSink(target, root_name=f"{generated.settings.iso_name}-profil")
    else:
        sink = DirectorySink(target, iso_name=generated.settings.iso_name)

    try:
        written = sink.write(generated.tree)
    except ProfileError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print(f"Profil erzeugt: {written}")
    print(f"  {generated.tree.describe()}")
    print(f"  Ergebnis waere: {generated.iso_filename}")
    for entry in generated.added_packages:
        print(f"  + {entry.name}: {entry.reason}")
    for warning in generated.warnings:
        print(f"  Hinweis: {warning}")
    print()
    print("Auf einem Arch-System:")
    print(f"  {generated.build_command()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
