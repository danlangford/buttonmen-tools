import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import bmair_release


class TestBmairRelease(unittest.TestCase):

  def make_release(self, contents=b"bmair executable", version="0.5.0"):
    platform_name = "macos-x86_64"
    executable_name = f"bmair-{version}-{platform_name}-release"
    checksum_name = f"bmair-{version}-{platform_name}-release.sha256"
    base = f"https://github.com/danlangford/bmai/releases/download/bmair-v{version}/"
    release = {
        "tag_name": f"bmair-v{version}",
        "assets": [
            {"name": executable_name,
             "browser_download_url": base + executable_name},
            {"name": checksum_name,
             "browser_download_url": base + checksum_name},
        ],
    }
    checksum = hashlib.sha256(contents).hexdigest()
    responses = {
        bmair_release.LATEST_RELEASE_URL: json.dumps(release).encode(),
        base + executable_name: contents,
        base + checksum_name:
            f"{checksum}  {executable_name}\n".encode(),
    }
    return release, responses, executable_name

  def test_platform_names_match_release_assets(self):
    self.assertEqual("macos-arm64",
                     bmair_release.platform_name("Darwin", "arm64"))
    self.assertEqual("linux-arm64",
                     bmair_release.platform_name("Linux", "aarch64"))
    self.assertEqual("windows-x86_64",
                     bmair_release.platform_name("Windows", "AMD64"))

  def test_unsupported_platform_is_clear(self):
    with self.assertRaisesRegex(
        bmair_release.BmairReleaseError, "no release build"):
      bmair_release.platform_name("FreeBSD", "riscv64")

  def test_installs_verified_executable_and_metadata(self):
    release, responses, executable_name = self.make_release()
    with TemporaryDirectory() as directory:
      install_dir = Path(directory)
      executable = bmair_release.install_release(
          release, install_dir, "macos-x86_64",
          fetcher=responses.__getitem__)

      self.assertEqual(b"bmair executable", executable.read_bytes())
      self.assertTrue(os.access(executable, os.X_OK))
      metadata = json.loads(
          (install_dir / bmair_release.METADATA_FILE).read_text())
      self.assertEqual(executable_name, metadata["asset"])
      self.assertEqual("bmair-v0.5.0", metadata["tag"])
      self.assertEqual(executable,
                       bmair_release.verified_installed_binary(install_dir))

  def test_refuses_download_that_does_not_match_checksum(self):
    release, responses, executable_name = self.make_release()
    executable_url = next(
        asset["browser_download_url"] for asset in release["assets"]
        if asset["name"] == executable_name)
    responses[executable_url] = b"tampered"

    with TemporaryDirectory() as directory:
      with self.assertRaisesRegex(
          bmair_release.BmairReleaseError, "checksum mismatch"):
        bmair_release.install_release(
            release, Path(directory), "macos-x86_64",
            fetcher=responses.__getitem__)
      self.assertFalse((Path(directory) / executable_name).exists())

  def test_offline_update_uses_last_verified_executable(self):
    release, responses, _ = self.make_release()
    with TemporaryDirectory() as directory:
      install_dir = Path(directory)
      installed = bmair_release.install_release(
          release, install_dir, "macos-x86_64",
          fetcher=responses.__getitem__)

      def offline(_url):
        raise OSError("offline")

      self.assertEqual(
          installed,
          bmair_release.resolve_bmair(
              install_dir=install_dir, fetcher=offline))

  def test_never_mode_does_not_access_network(self):
    release, responses, _ = self.make_release()
    with TemporaryDirectory() as directory:
      install_dir = Path(directory)
      installed = bmair_release.install_release(
          release, install_dir, "macos-x86_64",
          fetcher=responses.__getitem__)
      network = unittest.mock.Mock(side_effect=AssertionError("network used"))

      self.assertEqual(
          installed,
          bmair_release.resolve_bmair(
              mode="never", install_dir=install_dir, fetcher=network))
      network.assert_not_called()

  @patch("bmair_release.platform_name", return_value="macos-x86_64")
  def test_auto_mode_does_not_redownload_current_release(self, _platform):
    release, responses, _ = self.make_release()
    with TemporaryDirectory() as directory:
      install_dir = Path(directory)
      installed = bmair_release.install_release(
          release, install_dir, "macos-x86_64",
          fetcher=responses.__getitem__)
      requested = []

      def recording_fetch(url):
        requested.append(url)
        return responses[url]

      self.assertEqual(
          installed,
          bmair_release.resolve_bmair(
              install_dir=install_dir, fetcher=recording_fetch))
      self.assertEqual([bmair_release.LATEST_RELEASE_URL], requested)


if __name__ == "__main__":
  unittest.main()
