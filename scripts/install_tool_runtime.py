"""Build a relocatable Windows x64 CPython distribution and its tool dependencies.

The official embeddable distribution includes the interpreter, DLLs and stdlib.
No venv, conda prefix, host site-packages or absolute launcher paths are shipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

if __package__:
    from ._runtime_patches import apply_runtime_patches
else:
    # The embedded interpreter's _pth file does not include the script directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _runtime_patches import apply_runtime_patches

logger = logging.getLogger(__name__)
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.13.12"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_SHA256 = "76f238f606250c87c6beac75dccd35ee99070a13490555936abb6cb64ecce3d0"
PIP_VERSION = "25.3"
NO_WINDOW = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


def validate_output(path: Path) -> Path:
    """Validate user-selected write paths with the plugin's shared guard."""
    sys.path.insert(0, str(PLUGIN_ROOT))
    from tools._path_safety import is_path_safe

    resolved = path.resolve()
    safe, reason = is_path_safe(resolved)
    if not safe:
        raise ValueError(reason)
    return resolved


def clean_environment() -> dict[str, str]:
    """Keep installer configuration, excluding Python environment injection."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PYTHON")
        and key.upper() not in {"VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV"}
    }


def download(url: str, destination: Path, expected_sha256: str | None = None) -> None:
    """Download with bounded retries and verify the upstream checksum."""
    cache = PLUGIN_ROOT / ".runtime-downloads" / destination.name
    if expected_sha256:
        cache.parent.mkdir(parents=True, exist_ok=True)
        if (
            cache.is_file()
            and hashlib.sha256(cache.read_bytes()).hexdigest() == expected_sha256
        ):
            shutil.copy2(cache, destination)
            return
    target = cache if expected_sha256 else destination
    curl = shutil.which("curl.exe") if sys.platform == "win32" else None
    if curl:
        subprocess.run(
            [
                curl,
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--retry",
                "2",
                "--connect-timeout",
                "15",
                "--max-time",
                "300",
                "--continue-at",
                "-",
                "--output",
                str(target),
                url,
            ],
            check=True,
            timeout=1000,
            **NO_WINDOW,
        )
    else:
        for attempt in range(3):
            try:
                with (
                    urllib.request.urlopen(url, timeout=60) as response,
                    target.open("wb") as output,
                ):
                    shutil.copyfileobj(response, output)
                break
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    raise
                logger.warning("Download interrupted; retrying %s", url)
                time.sleep(1)
    if expected_sha256:
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected_sha256:
            raise ValueError(f"Checksum mismatch for {destination.name}: {actual}")
        shutil.copy2(target, destination)


def extract_archive(archive: Path, destination: Path) -> None:
    """Extract only archive members that stay inside the intended directory."""
    root = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            target = (root / member.filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"Unsafe archive entry: {member.filename}")
        source.extractall(root)


def run(python: Path, *args: str, capture: bool = False) -> str:
    """Run the new interpreter with a timeout and isolated Python imports."""
    result = subprocess.run(
        [str(python), "-I", *args],
        env=clean_environment(),
        check=True,
        timeout=600,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        **NO_WINDOW,
    )
    return result.stdout if capture else ""


def bootstrap_pip(python_root: Path, cache: Path) -> None:
    """Vendor a pinned pip wheel; the embedded distribution has no ensurepip."""
    metadata = cache / "pip.json"
    download(f"https://pypi.org/pypi/pip/{PIP_VERSION}/json", metadata)
    wheel = next(
        item
        for item in json.loads(metadata.read_text(encoding="utf-8"))["urls"]
        if item["filename"].endswith("-py3-none-any.whl")
    )
    archive = cache / wheel["filename"]
    download(wheel["url"], archive, wheel["digests"]["sha256"])
    extract_archive(archive, python_root / "Lib" / "site-packages")


def bundle_native_dlls(python_root: Path) -> None:
    """Ship the C++ runtime adjacent to native tools, avoiding system installs."""
    destinations = [
        python_root / "Lib/site-packages/clang_format/data/bin",
        python_root / "Lib/site-packages/cppcheck/Cppcheck",
    ]
    dlls = [
        path
        for path in python_root.glob("*.dll")
        if path.name.lower().startswith(("msvcp140", "vcruntime140", "concrt140"))
    ]
    if not (python_root / "msvcp140.dll").is_file():
        raise ValueError("MSVC runtime wheel did not supply msvcp140.dll")
    for destination in destinations:
        if not destination.is_dir():
            raise ValueError(f"Native tool package is incomplete: {destination}")
        for source in dlls:
            shutil.copy2(source, destination / source.name)
    for destination, executable in zip(
        destinations, ["clang-format.exe", "cppcheck.exe"]
    ):
        subprocess.run(
            [str(destination / executable), "--version"],
            env=clean_environment(),
            check=True,
            timeout=30,
            **NO_WINDOW,
        )


def build_runtime(destination: Path, requirements: Path) -> None:
    """Build and validate in staging before replacing a previous runtime."""
    destination = validate_output(destination)
    if destination == PLUGIN_ROOT or PLUGIN_ROOT.is_relative_to(destination):
        raise ValueError("The runtime must not replace the plugin or its ancestors")
    if destination.exists() and not (destination / "manifest.json").is_file():
        raise ValueError(
            f"Refusing to replace an unrecognized runtime directory: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    # WHY: all cleanup targets are private temporary directories under this parent.
    with tempfile.TemporaryDirectory(
        prefix=".spcode-build-", dir=destination.parent
    ) as work:
        staging = Path(work)
        python_root = staging / "runtime" / "python"
        python_root.mkdir(parents=True)
        archive = staging / "python.zip"
        logger.info("Downloading CPython %s (Windows x64)", PYTHON_VERSION)
        download(PYTHON_URL, archive, PYTHON_SHA256)
        extract_archive(archive, python_root)
        (python_root / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
        (python_root / "python313._pth").write_text(
            "python313.zip\n.\nLib/site-packages\nimport site\n", encoding="utf-8"
        )
        bootstrap_pip(python_root, staging)
        python = python_root / "python.exe"
        logger.info("Installing bundled tools")
        run(
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "-r",
            str(requirements),
        )
        run(python, "-m", "pip", "check")
        patches = apply_runtime_patches(python_root)
        run(
            python,
            "-c",
            "import vivado_mcp, mcp, ruff, clang_format, cpplint, cppcheck, crap4py, lizard, send2trash, win32api",
        )
        bundle_native_dlls(python_root)
        # WHY: pip's Windows entry-point exe files embed the staging Python path.
        # Native binaries inside package data and python -m are relocatable.
        scripts = python_root / "Scripts"
        if scripts.is_dir():
            for entry in scripts.iterdir():
                if (
                    entry.is_file()
                    and entry.suffix.lower() != ".dll"
                    and entry.name.lower() != "ruff.exe"
                ):
                    entry.unlink()
        run(python, "-m", "ruff", "--version")
        run(python, "-m", "cpplint", "--version")
        frozen = run(python, "-m", "pip", "freeze", "--all", capture=True)
        (staging / "runtime" / "requirements.lock").write_text(frozen, encoding="utf-8")
        manifest = {
            "schema": 1,
            "platform": "win-amd64",
            "python": PYTHON_VERSION,
            "standalone": True,
            "python_archive_sha256": PYTHON_SHA256,
            "requirements": frozen.splitlines(),
            "source_patches": patches,
        }
        (staging / "runtime" / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        backup = None
        if destination.exists():
            backup = destination.with_name(
                f"{destination.name}.previous-{time.time_ns()}"
            )
            destination.rename(backup)
            logger.info("Previous runtime preserved at %s", backup)
        try:
            (staging / "runtime").rename(destination)
        except OSError:
            if backup is not None:
                backup.rename(destination)
            raise
    logger.info("Standalone runtime installed: %s", destination)


def package_runtime(runtime: Path, archive: Path) -> None:
    """Produce an offline ZIP with paths relative to the plugin root."""
    archive = validate_output(archive)
    if archive.is_relative_to(runtime.resolve()):
        raise ValueError("The ZIP must be outside the runtime it packages")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(runtime.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                output.write(path, Path("runtime") / path.relative_to(runtime))
    logger.info("Offline archive: %s", archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )


def main() -> None:
    """Build the Windows runtime and optionally its offline deployment ZIP."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PLUGIN_ROOT / "runtime")
    locked = Path(__file__).with_name("runtime-requirements.lock")
    default_requirements = (
        locked
        if locked.is_file()
        else Path(__file__).with_name("runtime-requirements.txt")
    )
    parser.add_argument("--requirements", type=Path, default=default_requirements)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This builder currently targets Windows x64; run it on Windows.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    build_runtime(args.output, args.requirements.resolve())
    if args.archive:
        package_runtime(args.output, args.archive)


if __name__ == "__main__":
    main()
