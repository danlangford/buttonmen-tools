"""Download and select a verified BMAIR release executable."""

import hashlib
import json
import logging
import os
import platform
import re
import ssl
import stat
import tempfile
import urllib.request
from pathlib import Path

try:
  import certifi
except ImportError:  # Normal system trust remains available without requests.
  certifi = None


LOGGER = logging.getLogger("bmaibagels.bmair_release")
LATEST_RELEASE_URL = (
    "https://api.github.com/repos/danlangford/bmai/releases/latest")
DOWNLOAD_URL_PREFIX = (
    "https://github.com/danlangford/bmai/releases/download/")
DEFAULT_INSTALL_DIR = Path(__file__).resolve().parent / ".bmair"
METADATA_FILE = "current.json"


class BmairReleaseError(RuntimeError):
  """Raised when no safe BMAIR executable can be selected."""


def platform_name(system=None, machine=None):
  """Translate Python's OS and architecture names to BMAIR asset names."""
  system = (system or platform.system()).casefold()
  machine = (machine or platform.machine()).casefold()
  operating_systems = {
      "darwin": "macos",
      "linux": "linux",
      "windows": "windows",
  }
  architectures = {
      "x86_64": "x86_64",
      "amd64": "x86_64",
      "arm64": "arm64",
      "aarch64": "arm64",
  }
  if system not in operating_systems or machine not in architectures:
    raise BmairReleaseError(
        f"BMAIR has no release build for {system or 'unknown OS'}/"
        f"{machine or 'unknown architecture'}")
  return f"{operating_systems[system]}-{architectures[machine]}"


def sha256_bytes(contents):
  return hashlib.sha256(contents).hexdigest()


def sha256_file(path):
  digest = hashlib.sha256()
  with path.open("rb") as executable:
    for block in iter(lambda: executable.read(1024 * 1024), b""):
      digest.update(block)
  return digest.hexdigest()


def fetch(url, timeout=15):
  """Fetch one GitHub API response or release asset."""
  request = urllib.request.Request(
      url, headers={"Accept": "application/vnd.github+json",
                    "User-Agent": "BMAIBagels-BMAIR-updater"})
  context = ssl.create_default_context(
      cafile=certifi.where() if certifi is not None else None)
  with urllib.request.urlopen(
      request, timeout=timeout, context=context) as response:
    return response.read()


def read_metadata(install_dir):
  path = install_dir / METADATA_FILE
  try:
    with path.open(encoding="utf-8") as metadata_file:
      return json.load(metadata_file)
  except (FileNotFoundError, json.JSONDecodeError, OSError):
    return None


def verified_installed_binary(install_dir):
  """Return the recorded executable only when its checksum still matches."""
  metadata = read_metadata(install_dir)
  if not metadata:
    return None
  asset = metadata.get("asset")
  expected = metadata.get("sha256")
  if not asset or not expected or Path(asset).name != asset:
    return None
  executable = install_dir / asset
  if not executable.is_file() or sha256_file(executable) != expected:
    return None
  return executable


def release_assets(release, target_platform):
  """Select the executable and checksum assets for this machine."""
  version = release.get("tag_name", "").removeprefix("bmair-v")
  suffix = ".exe" if target_platform.startswith("windows-") else ""
  executable_name = f"bmair-{version}-{target_platform}-release{suffix}"
  checksum_name = f"bmair-{version}-{target_platform}-release.sha256"
  assets = {asset.get("name"): asset for asset in release.get("assets", [])}
  try:
    executable = assets[executable_name]
    checksum = assets[checksum_name]
  except KeyError as error:
    raise BmairReleaseError(
        f"Latest BMAIR release has no complete {target_platform} build") from error
  for asset in (executable, checksum):
    if not asset.get("browser_download_url", "").startswith(DOWNLOAD_URL_PREFIX):
      raise BmairReleaseError("BMAIR release contained an unexpected asset URL")
  return version, executable, checksum


def parse_checksum(contents, executable_name):
  """Read the expected digest while also checking its associated filename."""
  line = contents.decode("ascii").strip()
  match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
  if not match or match.group(2) != executable_name:
    raise BmairReleaseError("BMAIR checksum file has an unexpected format")
  return match.group(1).lower()


def atomic_write(path, contents, executable=False):
  """Replace a managed file only after its complete contents are on disk."""
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary_name = None
  try:
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
      temporary.write(contents)
      temporary.flush()
      os.fsync(temporary.fileno())
      temporary_name = temporary.name
    if executable:
      current_mode = os.stat(temporary_name).st_mode
      os.chmod(temporary_name, current_mode | stat.S_IXUSR | stat.S_IXGRP)
    os.replace(temporary_name, path)
  finally:
    if temporary_name and os.path.exists(temporary_name):
      os.unlink(temporary_name)


def install_release(release, install_dir, target_platform, fetcher=fetch):
  """Download, verify, and atomically install the selected release."""
  version, executable_asset, checksum_asset = release_assets(
      release, target_platform)
  executable_name = executable_asset["name"]
  expected = parse_checksum(
      fetcher(checksum_asset["browser_download_url"]), executable_name)
  contents = fetcher(executable_asset["browser_download_url"])
  actual = sha256_bytes(contents)
  if actual != expected:
    raise BmairReleaseError(
        f"BMAIR checksum mismatch: expected {expected}, received {actual}")

  executable_path = install_dir / executable_name
  atomic_write(executable_path, contents, executable=True)
  metadata = {
      "repository": "danlangford/bmai",
      "version": version,
      "tag": release["tag_name"],
      "platform": target_platform,
      "asset": executable_name,
      "sha256": expected,
  }
  atomic_write(
      install_dir / METADATA_FILE,
      (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
  )
  return executable_path


def resolve_bmair(mode="auto", install_dir=DEFAULT_INSTALL_DIR,
                  fallback=None, fetcher=fetch):
  """Return a verified BMAIR path, updating it when policy permits.

  A failed update always falls back to the last verified managed executable,
  then to the caller's legacy executable when one exists.
  """
  install_dir = Path(install_dir)
  fallback = Path(fallback) if fallback else None
  installed = verified_installed_binary(install_dir)
  if mode == "never":
    if installed:
      return installed
    if fallback and fallback.is_file():
      return fallback
    raise BmairReleaseError("No local BMAIR executable is available")

  try:
    release = json.loads(fetcher(LATEST_RELEASE_URL).decode("utf-8"))
    target_platform = platform_name()
    version, executable_asset, _ = release_assets(release, target_platform)
    metadata = read_metadata(install_dir)
    if (mode != "force" and installed and metadata and
        metadata.get("tag") == release.get("tag_name") and
        metadata.get("asset") == executable_asset.get("name")):
      LOGGER.info("Using current BMAIR %s for %s", version, target_platform)
      return installed
    executable = install_release(
        release, install_dir, target_platform, fetcher=fetcher)
    LOGGER.info("Installed BMAIR %s for %s at %s",
                version, target_platform, executable)
    return executable
  except Exception as error:
    if installed:
      LOGGER.warning(
          "Could not update BMAIR; using the last verified copy at %s: %s",
          installed, error)
      return installed
    if fallback and fallback.is_file():
      LOGGER.warning(
          "Could not install BMAIR; using the legacy binary at %s: %s",
          fallback, error)
      return fallback
    if isinstance(error, BmairReleaseError):
      raise
    raise BmairReleaseError(f"Could not install BMAIR: {error}") from error
