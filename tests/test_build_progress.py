"""Tests der Fortschrittsauswertung.

Die Marken stammen aus dem mkarchiso-Quelltext, nicht aus einer Vermutung.
Diese Tests halten fest, dass sie richtig zugeordnet werden -- und decken vor
allem die beiden Fallstricke ab, an denen ein naiver Parser scheitert.
"""

from __future__ import annotations

from archcustomiser.core.build.progress import (
    MESSAGE,
    STAGES,
    ProgressParser,
    split_lines,
    summarise_failure,
)


def feed(parser: ProgressParser, *lines: str):
    state = parser.state
    for line in lines:
        state = parser.feed(line)
    return state


def info(text: str) -> str:
    return f"[mkarchiso] INFO: {text}"


# ---------------------------------------------------------------------------
# Zeilentrennung
# ---------------------------------------------------------------------------


def test_split_handles_carriage_returns() -> None:
    """mksquashfs und xorriso beenden ihre Zeilen mit \\r, nicht mit \\n.

    Wer nur an Zeilenumbruechen trennt, sieht waehrend der laengsten Bauphase
    ueberhaupt nichts.
    """
    chunk = "erste\rzweite\rdritte\n"
    assert split_lines(chunk) == ["erste", "zweite", "dritte"]


def test_split_ignores_empty_pieces() -> None:
    assert split_lines("a\r\n\r\nb") == ["a", "b"]


def test_message_format() -> None:
    match = MESSAGE.match("[mkarchiso] INFO: Creating ISO image...")
    assert match is not None
    assert match.group("level") == "INFO"
    assert match.group("text") == "Creating ISO image..."


# ---------------------------------------------------------------------------
# Grobstufen
# ---------------------------------------------------------------------------


def test_stages_are_ordered_and_within_range() -> None:
    for stage in STAGES:
        assert 0.0 <= stage.start <= stage.end <= 1.0, stage.key


def test_typo_in_upstream_message_is_tolerated() -> None:
    """Im Original steht 'live-enviroment' -- deshalb nur Praefixvergleich."""
    parser = ProgressParser()
    state = feed(parser, info("Creating a list of installed packages on live-enviroment..."))
    assert state.stage_key == "pkglist"


def test_progress_never_moves_backwards() -> None:
    """Ein zurueckspringender Balken sieht nach einem Fehler aus."""
    parser = ProgressParser()
    feed(parser, info("Creating ISO image..."))
    high = parser.state.fraction
    # Eine spaete Zeile, die zu einer frueheren Stufe gehoert
    feed(parser, info("Copying custom airootfs files..."))
    assert parser.state.fraction >= high


def test_completed_steps_are_collected() -> None:
    parser = ProgressParser()
    feed(
        parser,
        info("Copying custom airootfs files..."),
        info("Done!"),
        info("Installing packages to '/work/'..."),
        info("Done! Packages installed successfully."),
    )
    assert "Systemdateien kopiert" in parser.state.completed
    assert "Pakete werden installiert" in parser.state.completed


def test_grub_and_systemd_boot_share_a_position() -> None:
    for marker in ("Setting up systemd-boot for UEFI booting...", "Setting up GRUB for UEFI booting..."):
        parser = ProgressParser()
        state = feed(parser, info(marker))
        assert 0.70 <= state.fraction <= 0.73


# ---------------------------------------------------------------------------
# Feinfortschritt
# ---------------------------------------------------------------------------


def test_pacman_counter_moves_the_bar() -> None:
    parser = ProgressParser()
    feed(parser, info("Installing packages to '/work/'..."))
    start = parser.state.fraction

    feed(parser, "Packages (100) base-1-2  linux-6.9-1")
    feed(parser, ":: Retrieving packages...")
    feed(parser, " ( 50/100) downloading paket-50.pkg.tar.zst")
    middle = parser.state.fraction
    assert middle > start

    feed(parser, ":: Processing package changes...")
    feed(parser, " (100/100) installing paket-100")
    assert parser.state.fraction > middle
    assert "installiert" in parser.state.detail


def test_download_and_install_are_two_passes() -> None:
    """Beide Durchlaeufe zaehlen dieselbe Menge -- zusammen doppelt so viele Schritte."""
    parser = ProgressParser()
    feed(parser, info("Installing packages to '/work/'..."), "Packages (10) a b")
    feed(parser, ":: Retrieving packages...", " ( 10/10) downloading paket-10.pkg.tar.zst")
    after_download = parser.state.fraction

    stage = next(s for s in STAGES if s.key == "packages")
    halfway = stage.start + (stage.end - stage.start) * 0.5
    assert abs(after_download - halfway) < 0.01, "nach dem Laden sollte etwa die Haelfte erreicht sein"


def test_squashfs_percentage_from_carriage_return_output() -> None:
    parser = ProgressParser()
    feed(parser, info("Creating SquashFS image, this may take some time..."))
    start = parser.state.fraction
    for state in parser.feed_all("[===|   ] 4900/9800  50%\r"):
        pass
    assert parser.state.fraction > start
    assert parser.state.detail == "50 %"


def test_xorriso_percentage() -> None:
    parser = ProgressParser()
    feed(parser, info("Creating ISO image..."))
    for _ in parser.feed_all("xorriso : UPDATE :  75.50% done\r"):
        pass
    assert parser.state.detail == "76 %"
    assert parser.state.fraction > 0.94


def test_tool_output_outside_a_known_stage_is_ignored() -> None:
    parser = ProgressParser()
    before = parser.state.fraction
    parser.feed("irgendein Text 42%")
    assert parser.state.fraction == before


# ---------------------------------------------------------------------------
# Fehler und Warnungen
# ---------------------------------------------------------------------------


def test_errors_and_warnings_are_collected_separately() -> None:
    parser = ProgressParser()
    parser.feed("[mkarchiso] ERROR: Validating 'bios.syslinux': The 'syslinux' package is missing!")
    parser.feed("[mkarchiso] WARNING: Cannot change permissions of '/etc/x'.")
    assert len(parser.errors) == 1
    assert len(parser.warnings) == 1


def test_failure_summary_skips_the_counting_line() -> None:
    """mkarchiso meldet am Ende nur die Anzahl -- die Ursache steht davor."""
    summary = summarise_failure(
        [
            "Validating 'bios.syslinux': The 'syslinux' package is missing from the package list!",
            "1 errors were encountered while validating the profile. Aborting.",
        ]
    )
    assert "syslinux" in summary
    assert "errors were encountered" not in summary


def test_failure_summary_without_errors_points_to_the_log() -> None:
    assert "Protokoll" in summarise_failure([])


def test_finish_sets_full_progress_on_success() -> None:
    parser = ProgressParser()
    state = parser.finish(True)
    assert state.fraction == 1.0
    assert state.finished


def test_finish_keeps_progress_on_failure() -> None:
    parser = ProgressParser()
    feed(parser, info("Creating SquashFS image, this may take some time..."))
    partial = parser.state.fraction
    state = parser.finish(False)
    assert state.fraction == partial
    assert state.finished
