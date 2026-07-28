from __future__ import annotations

import hashlib
import csv
import base64
import ctypes
import ctypes.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from distrohop.capture.chromium_linux import (
    ChromiumDecryptionError,
    decrypt_chromium_value,
    derive_key,
)
from distrohop.capture.firefox import NSSDecryptor, SECItem
from distrohop.capture.neutral import write_bookmarks, write_cookies, write_logins
from distrohop.vault.crypto import decrypt_archive, encrypt_tree


def _chromium_blob(
    plaintext: bytes,
    password: bytes = b"peanuts",
    prefix: bytes = b"v10",
) -> bytes:
    key = derive_key(password)
    result = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-cbc",
            "-e",
            "-K",
            key.hex(),
            "-iv",
            (b" " * 16).hex(),
        ],
        input=plaintext,
        check=True,
        capture_output=True,
    )
    return prefix + result.stdout


class ChromiumLinuxCryptoTests(unittest.TestCase):
    def test_v10_uses_linux_peanuts_key(self) -> None:
        blob = _chromium_blob(b"session-token")
        self.assertEqual(decrypt_chromium_value(blob), "session-token")

    def test_chromium_130_domain_hash_is_removed_only_when_valid(self) -> None:
        domain = ".example.com"
        prefixed = hashlib.sha256(domain.encode()).digest() + b"cookie-value"
        self.assertEqual(
            decrypt_chromium_value(_chromium_blob(prefixed), host_key=domain),
            "cookie-value",
        )
        self.assertEqual(
            decrypt_chromium_value(_chromium_blob(prefixed), host_key=".other.test"),
            prefixed.decode("utf-8", errors="replace"),
        )

    def test_v11_uses_the_keyring_secret_and_never_guesses(self) -> None:
        secret = b"keyring-generated-secret"
        blob = _chromium_blob(b"protected", password=secret, prefix=b"v11")
        self.assertEqual(
            decrypt_chromium_value(blob, secret=secret),
            "protected",
        )
        with self.assertRaises(ChromiumDecryptionError):
            decrypt_chromium_value(blob)


@unittest.skipUnless(
    shutil.which("certutil") and ctypes.util.find_library("nss3"),
    "certutil/libnss3 indisponível",
)
class FirefoxNSSCryptoTests(unittest.TestCase):
    def test_nss_decrypts_a_synthetic_firefox_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            profile.mkdir()
            subprocess.run(
                ["certutil", "-N", "-d", "sql:{}".format(profile), "--empty-password"],
                check=True,
                capture_output=True,
            )
            nss = ctypes.CDLL(ctypes.util.find_library("nss3"))
            nss.NSS_InitReadWrite.argtypes = [ctypes.c_char_p]
            nss.NSS_InitReadWrite.restype = ctypes.c_int
            nss.PK11SDR_Encrypt.argtypes = [
                ctypes.POINTER(SECItem),
                ctypes.POINTER(SECItem),
                ctypes.POINTER(SECItem),
                ctypes.c_void_p,
            ]
            nss.PK11SDR_Encrypt.restype = ctypes.c_int
            nss.SECITEM_FreeItem.argtypes = [ctypes.POINTER(SECItem), ctypes.c_int]
            nss.PK11_GetInternalKeySlot.argtypes = []
            nss.PK11_GetInternalKeySlot.restype = ctypes.c_void_p
            nss.PK11_Authenticate.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
            ]
            nss.PK11_Authenticate.restype = ctypes.c_int
            nss.PK11_FreeSlot.argtypes = [ctypes.c_void_p]
            self.assertEqual(
                nss.NSS_InitReadWrite("sql:{}".format(profile).encode()),
                0,
            )
            slot = nss.PK11_GetInternalKeySlot()
            self.assertTrue(slot)
            self.assertEqual(nss.PK11_Authenticate(slot, 1, None), 0)
            plaintext = b"known-firefox-password"
            buffer = (ctypes.c_ubyte * len(plaintext)).from_buffer_copy(plaintext)
            source = SECItem(
                0, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)), len(plaintext)
            )
            encrypted = SECItem()
            key_id = SECItem(0, None, 0)
            self.assertEqual(
                nss.PK11SDR_Encrypt(
                    ctypes.byref(key_id),
                    ctypes.byref(source),
                    ctypes.byref(encrypted),
                    None,
                ),
                0,
            )
            try:
                encoded = base64.b64encode(
                    ctypes.string_at(encrypted.data, encrypted.len)
                ).decode()
            finally:
                nss.SECITEM_FreeItem(ctypes.byref(encrypted), 0)
                nss.PK11_FreeSlot(slot)
                nss.NSS_Shutdown()

            with NSSDecryptor(profile) as decryptor:
                self.assertEqual(decryptor.decrypt(encoded), plaintext.decode())


class NeutralFormatTests(unittest.TestCase):
    def test_neutral_files_are_machine_readable_and_bookmarks_are_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_cookies([{"host": "example.com", "name": "sid", "value": "á"}], root / "cookies.jsonl")
            write_logins([{"origin": "https://example.com", "username": "u", "password": "p"}], root / "logins.csv")
            write_bookmarks(
                [{"title": "<unsafe>", "url": "https://example.com/?a=1&b=2"}],
                root / "bookmarks.html",
            )

            cookie = json.loads((root / "cookies.jsonl").read_text(encoding="utf-8"))
            with (root / "logins.csv").open(encoding="utf-8", newline="") as stream:
                login = next(csv.DictReader(stream))
            bookmarks = (root / "bookmarks.html").read_text(encoding="utf-8")
            self.assertEqual(cookie["value"], "á")
            self.assertEqual(login["password"], "p")
            self.assertIn("&lt;unsafe&gt;", bookmarks)
            self.assertIn("a=1&amp;b=2", bookmarks)


class BundleCryptoTests(unittest.TestCase):
    def test_openssl_bundle_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            payload.mkdir()
            (payload / "secret.txt").write_text("segredo", encoding="utf-8")
            encrypted = root / "bundle.tar.enc"

            password = "correct horse battery staple"
            commands = []

            def runner(command, **kwargs):
                commands.append(command)
                self.assertNotIn(password, command)
                self.assertEqual(kwargs["input"], password.encode() + b"\n")
                return subprocess.run(command, **kwargs)

            digest = encrypt_tree(payload, encrypted, password, runner=runner)
            self.assertEqual(digest, hashlib.sha256(encrypted.read_bytes()).hexdigest())
            self.assertNotIn(b"segredo", encrypted.read_bytes())

            restored = root / "restored"
            decrypt_archive(encrypted, restored, "correct horse battery staple")
            self.assertEqual((restored / "secret.txt").read_text(encoding="utf-8"), "segredo")
