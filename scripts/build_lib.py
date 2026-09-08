"""Shared helpers for build scripts.

Used by kmod/build.py, portshide/build-zip.py, zygisk/build.py,
and scripts/build-version.py.

Stdlib-only on purpose: scripts/build-version.py is invoked from
lsposed/app/build.gradle.kts on every Gradle build, so adding pip/uv
dependencies here would break the APK build for anyone without those
tools available.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


def make_zip(source_dir: Path, output_zip: Path) -> None:
    """Create a zip archive from source_dir contents."""
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in source_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(source_dir)
                zf.write(file_path, arcname)


def version_sort_key(name: str) -> tuple[int, ...]:
    """Sort key that orders strings by their embedded integer runs.

    Used to pick the highest version when multiple toolchain directories
    coexist (NDK 25.0.1 vs 100.0.0, clang-r450b vs clang-r498344b) where
    plain lexicographic sort gives the wrong answer.
    """
    return tuple(int(part) for part in re.findall(r"\d+", name))


def get_build_version(repo_root: Path | None = None) -> str:
    """Get the effective build version for vpnhide artifacts.

    - VPNHIDE_BUILD_VERSION set    -> that exact value
    - HEAD on a tag vX.Y.Z        -> "X.Y.Z"          (release build)
    - N commits after tag vX.Y.Z  -> "X.Y.Z-N-gSHA"   (dev build)
    - working tree dirty          -> additional "-dirty" suffix
    - no git / no matching tag    -> falls back to VERSION file
    """
    override = os.environ.get("VPNHIDE_BUILD_VERSION")
    if override and override.strip():
        return override.strip().removeprefix("v")

    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent

    repo_root = repo_root.resolve()
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repo_root}",
            "describe",
            "--tags",
            "--match",
            "v*",
            "--dirty",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().removeprefix("v")

    version_file = repo_root / "VERSION"
    return version_file.read_text(encoding="utf-8").strip()


def detect_android_ndk() -> str | None:
    """Find an Android NDK for cargo-ndk based builds."""
    android_ndk_home = os.environ.get("ANDROID_NDK_HOME")
    if android_ndk_home and Path(android_ndk_home).is_dir():
        return android_ndk_home

    ndk_base = Path.home() / "Android" / "Sdk" / "ndk"
    if not ndk_base.exists():
        return None
    versions = sorted((d.name for d in ndk_base.iterdir() if d.is_dir()), key=version_sort_key)
    if not versions:
        return None
    return str(ndk_base / versions[-1])


# Maps the module's install-time ABI selector suffix -> (cargo-ndk ABI name,
# Rust target-triple output dir). The kernel-level backends (kmod/kpm/builtin)
# and the ports module are arm64-only — GKI/APatch/KernelPatch kernels and their
# activators only ever run on arm64. Only the zygisk module (userspace, per-app
# ABI) ships an armv7 activator too, and it opts in via `abis=`.
ACTIVATOR_TARGETS: dict[str, tuple[str, str]] = {
    "arm64": ("arm64-v8a", "aarch64-linux-android"),
    "armv7": ("armeabi-v7a", "armv7-linux-androideabi"),
}


def build_activator_bins(
    repo_root: Path,
    bin_name: str,
    *,
    abis: tuple[str, ...] = ("arm64",),
    android_ndk_home: str | None = None,
    target_dir: Path | None = None,
    required: bool = True,
) -> dict[str, Path] | None:
    """Build the requested Android activator binaries for `bin_name`.

    `abis` names the ABI suffixes to build (keys of ACTIVATOR_TARGETS); default is
    arm64 only. Returns {suffix: Path}. `required=False` returns None (instead of
    raising) when Rust/NDK are unavailable — lets the kmod DDK packaging path
    reuse a prebuilt binary inside the kernel-build container.
    """
    ndk = android_ndk_home or detect_android_ndk()
    cargo = shutil.which("cargo")
    if not ndk or not cargo:
        msg = "cargo/Android NDK not available; activator not built"
        if required:
            raise RuntimeError(msg)
        print(f"warning: {msg}")
        return None

    out_target_dir = target_dir or repo_root / "target"
    env = os.environ.copy()
    env["ANDROID_NDK_HOME"] = ndk
    env["CARGO_TARGET_DIR"] = str(out_target_dir)
    target_flags: list[str] = []
    for suffix in abis:
        target_flags += ["-t", ACTIVATOR_TARGETS[suffix][0]]
    subprocess.run(
        [
            "cargo",
            "ndk",
            *target_flags,
            "build",
            "--release",
            # Fail loudly if Cargo.lock is out of sync instead of silently
            # rewriting it — a rewritten lock dirties the tree and stamps every
            # artifact "X.Y.Z-dirty" via git describe --dirty.
            "--locked",
            "-p",
            "vpnhide_activator",
            "--bin",
            bin_name,
        ],
        cwd=repo_root,
        env=env,
        check=True,
    )
    bins: dict[str, Path] = {}
    for suffix in abis:
        artifact = out_target_dir / ACTIVATOR_TARGETS[suffix][1] / "release" / bin_name
        if not artifact.exists():
            raise RuntimeError(f"expected activator artifact {artifact}, not found")
        bins[suffix] = artifact
    return bins


def build_activator_bin(
    repo_root: Path,
    bin_name: str,
    *,
    android_ndk_home: str | None = None,
    target_dir: Path | None = None,
    required: bool = True,
) -> Path | None:
    """Build the single Android arm64 activator binary and return its path.

    The arm64-only path for the kernel backends (kmod/kpm/builtin) and the ports
    module. `required=False` lets the kmod DDK packaging path reuse a prebuilt
    binary when Rust/NDK are unavailable in the kernel-build container.
    """
    bins = build_activator_bins(
        repo_root,
        bin_name,
        abis=("arm64",),
        android_ndk_home=android_ndk_home,
        target_dir=target_dir,
        required=required,
    )
    return None if bins is None else bins["arm64"]


def stage_activator_bins(bins: dict[str, Path], staging: Path) -> None:
    """Copy per-ABI activator binaries into a module staging dir.

    Each lands as `activator.<suffix>` (executable); the module's customize.sh
    keeps the one matching the device $ARCH as `activator` and deletes the rest.
    """
    for suffix, path in bins.items():
        dest = staging / f"activator.{suffix}"
        shutil.copy(path, dest)
        dest.chmod(0o755)
