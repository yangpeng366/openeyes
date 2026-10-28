"""Native automation surfaces: prefer the software's own interface.

The 2026 computer-use consensus has a level above "read the DOM": if the
application already ships a way to be driven - a command line, a headless
mode, a URL scheme - then that is a *better* surface than any screen
scraping, because it is deterministic, needs no coordinates and costs no
image budget.

This module finds such a surface for a given application. It does not
guess: it looks, and it reports what it actually found.

Four shapes exist in the wild, and each needs its own probe:

``cli``
    The application ships a command line (``7z.exe`` next to ``7zFM.exe``,
    ``es.exe`` for Everything). It is often *not* on PATH - it sits in the
    GUI's own directory, which is where the probe looks first.
``url-scheme``
    The application registers a protocol handler (``evernote:``,
    ``obsidian://``, ``vscode://``). A URL can carry a command, so this is
    an automation surface even though it is not a CLI.
``app-path``
    The executable is registered under
    ``HKLM\\...\\App Paths``, so it can be started by name.
``msix``
    The application is a Microsoft Store (MSIX) package; its AUMID starts
    it through the shell.

The report is deliberately explicit: a surface that was *looked for and
not found* says so, because "I found nothing" and "I did not look" are
different answers and an agent must be able to tell them apart.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

#: Kinds of native surface this module can find.
SURFACE_CLI = "cli"
SURFACE_URL_SCHEME = "url-scheme"
SURFACE_APP_PATH = "app-path"
SURFACE_MSIX = "msix"


@dataclass
class NativeSurface:
    """One native way to drive an application."""

    kind: str
    #: How to invoke it: the executable, the scheme, or the AUMID.
    target: str
    #: The full command line an agent would run.
    command: str = ""
    #: Where it was found (a path, a registry key, ...).
    location: str = ""
    #: A short note on what it can and cannot do.
    note: str = ""
    #: Extra detail, e.g. the protocol handler command line.
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "target": self.target,
            "command": self.command,
            "location": self.location,
            "note": self.note,
            "evidence": self.evidence,
        }


@dataclass
class NativeReport:
    """What native surfaces exist for an application - and what was looked for."""

    app: str
    surfaces: list[NativeSurface] = field(default_factory=list)
    #: The searches that ran, so "nothing found" is distinguishable from
    #: "nothing looked for".
    probed: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def found(self) -> bool:
        return bool(self.surfaces)

    def to_dict(self) -> dict:
        return {
            "app": self.app,
            "found": self.found,
            "count": len(self.surfaces),
            "surfaces": [s.to_dict() for s in self.surfaces],
            "probed": self.probed,
            "detail": self.detail,
        }


# --------------------------------------------------------------- discovery
def _run_where(name: str) -> str:
    """Resolve an executable on PATH, without running it."""
    return shutil.which(name) or ""


def _gui_directories(app_names: list[str]) -> list[str]:
    """Directories that plausibly hold a GUI application's own executables.

    The App Paths registry is the authoritative source for an installed
    application's location; the installer's own ``InstallLocation`` is the
    fallback.
    """
    directories: list[str] = []
    try:
        import winreg
    except ImportError:
        return directories
    # winreg wants the sub-key path without the "HKLM:" style prefix, and
    # each hive has its own root.
    registry_roots = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, root in registry_roots:
        try:
            key = winreg.OpenKey(hive, root)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    subkey = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(key, subkey) as entry:
                        try:
                            display, _ = winreg.QueryValueEx(entry, "DisplayName")
                        except OSError:
                            continue
                        if not any(name.lower() in str(display).lower()
                                   for name in app_names):
                            continue
                        try:
                            location, _ = winreg.QueryValueEx(entry, "InstallLocation")
                        except OSError:
                            location = ""
                        if location:
                            directories.append(str(location))
                except OSError:
                    continue
        finally:
            winreg.CloseKey(key)
    return [d for d in dict.fromkeys(directories) if os.path.isdir(d)]


def find_cli_surface(app_names: list[str],
                    cli_names: list[str] | None = None) -> NativeSurface | None:
    """Find a command line the application ships.

    The application's own directory is searched first: a CLI that lives
    next to its GUI (``7z.exe`` beside ``7zFM.exe``) is usually not on
    PATH, and PATH alone would miss it.
    """
    probed: list[str] = []
    candidates = list(cli_names or app_names)

    for directory in _gui_directories(app_names):
        probed.append(directory)
        for candidate in candidates:
            for suffix in (".exe", ".cmd", ".bat", ""):
                path = os.path.join(directory, candidate + suffix)
                if os.path.isfile(path):
                    return NativeSurface(
                        kind=SURFACE_CLI,
                        target=path,
                        command='"' + path + '"',
                        location=directory,
                        note="ships with the application; run it instead of "
                             "driving the window",
                        evidence={"found_by": "gui-directory"},
                    )

    for candidate in candidates:
        probed.append("PATH:" + candidate)
        found = _run_where(candidate)
        if found:
            return NativeSurface(
                kind=SURFACE_CLI,
                target=found,
                command='"' + found + '"',
                location="PATH",
                note="on PATH",
                evidence={"found_by": "path"},
            )
    return None


def find_url_scheme(scheme: str) -> NativeSurface | None:
    """Find a registered URL scheme handler for an application."""
    try:
        import winreg
    except ImportError:
        return None
    for hive, prefix in ((winreg.HKEY_CURRENT_USER, r"SOFTWARE\Classes"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Classes")):
        # "shell\open\command" is a subkey, not a value: it has to be
        # opened level by level.
        command = ""
        url = ""
        try:
            with winreg.OpenKey(hive, prefix + "\\" + scheme) as key:
                try:
                    with winreg.OpenKey(key, r"shell\open\command") as cmd_key:
                        command, _ = winreg.QueryValueEx(cmd_key, None)
                except OSError:
                    pass
                try:
                    with winreg.OpenKey(key, r"shell\open\url") as url_key:
                        url, _ = winreg.QueryValueEx(url_key, None)
                except OSError:
                    pass
        except OSError:
            continue
        if command or url:
            return NativeSurface(
                kind=SURFACE_URL_SCHEME,
                target=scheme + ":",
                command=str(command or url),
                location=prefix + "\\" + scheme,
                note="a URL can carry a command; cheaper and more reliable "
                     "than clicking",
                evidence={"handler": str(command or url)},
            )
    return None


def find_app_path(exe_name: str) -> NativeSurface | None:
    """Find an executable registered under App Paths."""
    try:
        import winreg
    except ImportError:
        return None
    for hive, prefix in ((winreg.HKEY_LOCAL_MACHINE,
                          r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
                         (winreg.HKEY_CURRENT_USER,
                          r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")):
        try:
            with winreg.OpenKey(hive, prefix + "\\" + exe_name) as key:
                command, _ = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        return NativeSurface(
            kind=SURFACE_APP_PATH,
            target=str(command),
            command='"' + str(command) + '"',
            location=prefix + "\\" + exe_name,
            note="registered executable; can be started by name",
            evidence={"registered": str(command)},
        )
    return None


def _shell_apps() -> list[tuple[str, str]]:
    """The applications the shell knows, as (display name, path-or-AUMID).

    The shell is the authority on what is installed: it covers Store
    packages, desktop programs and protocol handlers alike, and it
    answers in milliseconds - no PowerShell subprocess, no registry walk.
    """
    try:
        import win32com.client
    except ImportError:
        return []
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        folder = shell.Namespace("shell:AppsFolder")
    except Exception:
        return []
    apps: list[tuple[str, str]] = []
    try:
        items = folder.Items()
    except Exception:
        return []
    for item in items:
        try:
            name = item.Name
            path = item.Path
        except Exception:
            continue
        if name:
            apps.append((str(name), str(path)))
    return apps


def _looks_like_path(value: str) -> bool:
    """Whether the shell gave a real filesystem path.

    The shell sometimes reports a parse id (``{308046B0AF4A39CB}``) or a
    GUID-prefixed pseudo path. Neither can be executed, and presenting
    them as a command would be a lie an agent would act on.
    """
    if not value or value.startswith("{") or value.endswith("}"):
        return False
    if not os.path.isabs(value):
        return False
    return os.path.exists(value)


#: Executables that are never the application itself.
_AUXILIARY_STEMS = (
    "uninstall", "unins", "setup", "install", "installer", "update",
    "updater", "crashhandler", "crashreporter", "reporter", "helper",
    "cleanup", "repair", "elevate", "launcher", "quicklaunch",
)

#: The executable of an application is the one whose stem matches the
#: display name; when several match, the shortest wins (the application
#: itself is named more briefly than its helpers).


def _strip_separators(value: str) -> str:
    """Drop the decoration from a name ("7-Zip" -> "7zip")."""
    return "".join(ch for ch in value if ch.isalnum())


def _similar(display_name: str, stem: str) -> bool:
    """Whether an executable stem plausibly belongs to a display name.

    "元宝" / "yuanbao" share no characters, so a latin display name is
    compared against the romanised form; a non-latin one can only be
    matched loosely, which is why the caller insists on a *unique*
    candidate and refuses to guess between several.
    """
    left = display_name.strip().lower()
    right = stem.strip().lower()
    if not left or not right:
        return False
    if any(marker in right for marker in _AUXILIARY_STEMS):
        return False
    if left == right:
        return True
    if left.isascii():
        # Separators are decoration: "7-Zip" and "7z" are the same name.
        bare_left = _strip_separators(left)
        bare_right = _strip_separators(right)
        if bare_left == bare_right:
            return True
        return bare_left in bare_right or bare_right in bare_left
    # A non-latin display name: accept only a distinctive latin stem.
    return len(right) >= 4 and not any(c.isdigit() for c in right)


def find_msix_surface(app_name: str) -> NativeSurface | None:
    """Find how the shell starts an application.

    The shell reports either an AUMID (a Store package, e.g.
    ``AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App``) or a plain executable
    path. Either way this is the native way to start the app without
    touching its window.
    """
    wanted = app_name.strip().lower()
    for name, path in _shell_apps():
        if name.lower() != wanted:
            continue
        if "!" in path:
            return NativeSurface(
                kind=SURFACE_MSIX,
                target=path,
                command=f'explorer.exe shell:AppsFolder\\{path}',
                location="shell:AppsFolder",
                note="Store application; start it through the shell",
                evidence={"display_name": name},
            )
        if _looks_like_path(path):
            return NativeSurface(
                kind=SURFACE_APP_PATH,
                target=path,
                command='"' + path + '"',
                location="shell:AppsFolder",
                note="registered application; start it directly",
                evidence={"display_name": name},
            )
        # The shell reports some entries as a parse id or a GUID-prefixed
        # path. The App Paths registry often knows the real executable for
        # the same application; when it does, that path is the answer.
        for exe_name in (name + ".exe", name.replace(" ", "") + ".exe"):
            registered = find_app_path(exe_name)
            if registered is not None:
                registered.note = ("the shell reports this application as an "
                                   "opaque id; the registered executable is "
                                   "the runnable path")
                registered.evidence["display_name"] = name
                return registered
        # A display name rarely matches the executable name (Yuanbao is
        # "yuanbao.exe"), so the application's own directory is searched
        # for an executable that looks like it.
        for directory in _gui_directories([name]):
            try:
                entries = os.listdir(directory)
            except OSError:
                continue
            candidates = []
            for entry in entries:
                if not entry.lower().endswith(".exe"):
                    continue
                stem = entry[:-4]
                if stem and _similar(name, stem):
                    candidates.append(os.path.join(directory, entry))
            # Several plausible executables means the name alone does not
            # identify one - report nothing rather than guess.
            if len(candidates) == 1:
                full = candidates[0]
                return NativeSurface(
                    kind=SURFACE_APP_PATH,
                    target=full,
                    command='"' + full + '"',
                    location=directory,
                    note="the shell reports this application as an "
                         "opaque id; the executable in its own directory "
                         "is the runnable path",
                    evidence={"display_name": name},
                )
        # Handing an agent a fake path is worse than handing it nothing.
        return NativeSurface(
            kind=SURFACE_APP_PATH,
            target=path,
            command="",
            location="shell:AppsFolder",
            note="the shell knows this application but reports it as an "
                 "opaque id, not a runnable path; drive the window instead",
            evidence={"display_name": name, "opaque": True},
        )
    return None


def discover(app: str, *, cli_names: list[str] | None = None,
             schemes: list[str] | None = None,
             exe_names: list[str] | None = None) -> NativeReport:
    """Look for every native surface an application offers.

    ``app`` is the display name; ``cli_names`` / ``schemes`` /
    ``exe_names`` name the specific entry points when they differ from the
    application name.
    """
    report = NativeReport(app=app)
    names = [app]

    cli = find_cli_surface(names, cli_names)
    report.probed.append("cli")
    if cli:
        report.surfaces.append(cli)

    for scheme in (schemes or []):
        report.probed.append("url-scheme:" + scheme)
        found = find_url_scheme(scheme)
        if found:
            report.surfaces.append(found)

    for exe_name in (exe_names or []):
        report.probed.append("app-path:" + exe_name)
        found = find_app_path(exe_name)
        if found:
            report.surfaces.append(found)

    if not report.surfaces:
        msix = find_msix_surface(app)
        report.probed.append("msix")
        if msix:
            report.surfaces.append(msix)

    if report.surfaces:
        report.detail = (f"{len(report.surfaces)} native surface(s) for {app}")
    else:
        report.detail = (f"no native surface found for {app}; drive it "
                         "through the window (uia / cdp / vision)")
    return report


def describe_app(app: str, **kwargs: Any) -> NativeReport:
    """Alias of :func:`discover` with a name that reads better at a call site."""
    return discover(app, **kwargs)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("app", help="the application display name")
    parser.add_argument("--cli-name", action="append", default=[],
                        help="a CLI name to look for (repeatable)")
    parser.add_argument("--scheme", action="append", default=[],
                        help="a URL scheme to look for (repeatable)")
    parser.add_argument("--exe", action="append", default=[],
                        help="an App Paths entry to look for (repeatable)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    report = discover(args.app,
                       cli_names=args.cli_name or None,
                       schemes=args.scheme or None,
                       exe_names=args.exe or None)
    import json
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.found else 2


if __name__ == "__main__":
    raise SystemExit(main())
