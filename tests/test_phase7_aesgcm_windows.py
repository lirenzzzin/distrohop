from __future__ import annotations

import unittest

from distrohop.capture.aesgcm import AuthenticationError, decrypt, encrypt


class NistAesGcmTests(unittest.TestCase):
    """NIST SP 800-38D, Appendix B/C known-answer cases."""

    def test_empty_plaintext_vectors_for_aes_128_and_aes_256(self) -> None:
        vectors = (
            (
                bytes(16),
                bytes(12),
                b"",
                b"",
                b"",
                bytes.fromhex("58e2fccefa7e3061367f1d57a4e7455a"),
            ),
            (
                bytes(32),
                bytes(12),
                b"",
                b"",
                b"",
                bytes.fromhex("530f8afbc74536b9a963b4f1c4cb738b"),
            ),
        )
        for key, nonce, plaintext, aad, ciphertext, tag in vectors:
            with self.subTest(bits=len(key) * 8):
                produced_ciphertext, produced_tag = encrypt(
                    key,
                    nonce,
                    plaintext,
                    aad,
                )
                self.assertEqual(produced_ciphertext, ciphertext)
                self.assertEqual(produced_tag, tag)
                self.assertEqual(
                    decrypt(key, nonce, ciphertext, tag, aad),
                    plaintext,
                )

    def test_single_block_vectors_for_aes_128_and_aes_256(self) -> None:
        vectors = (
            (
                bytes(16),
                bytes(12),
                bytes(16),
                bytes.fromhex("0388dace60b6a392f328c2b971b2fe78"),
                bytes.fromhex("ab6e47d42cec13bdf53a67b21257bddf"),
            ),
            (
                bytes(32),
                bytes(12),
                bytes(16),
                bytes.fromhex("cea7403d4d606b6e074ec5d3baf39d18"),
                bytes.fromhex("d0d1c8a799996bf0265b98b5d48ab919"),
            ),
        )
        for key, nonce, plaintext, ciphertext, tag in vectors:
            with self.subTest(bits=len(key) * 8):
                self.assertEqual(encrypt(key, nonce, plaintext), (ciphertext, tag))
                self.assertEqual(
                    decrypt(key, nonce, ciphertext, tag),
                    plaintext,
                )

    def test_tag_is_checked_before_plaintext_is_returned(self) -> None:
        key = bytes(range(32))
        nonce = bytes(range(12))
        ciphertext, tag = encrypt(key, nonce, b"sensitive", b"metadata")
        tampered = tag[:-1] + bytes((tag[-1] ^ 1,))

        with self.assertRaises(AuthenticationError):
            decrypt(key, nonce, ciphertext, tampered, b"metadata")

    def test_nist_aad_and_partial_final_block(self) -> None:
        key = bytes.fromhex("feffe9928665731c6d6a8f9467308308")
        nonce = bytes.fromhex("cafebabefacedbaddecaf888")
        aad = bytes.fromhex("feedfacedeadbeeffeedfacedeadbeefabaddad2")
        plaintext = bytes.fromhex(
            "d9313225f88406e5a55909c5aff5269a"
            "86a7a9531534f7da2e4c303d8a318a72"
            "1c3c0c95956809532fcf0e2449a6b525"
            "b16aedf5aa0de657ba637b39"
        )
        ciphertext = bytes.fromhex(
            "42831ec2217774244b7221b784d0d49c"
            "e3aa212f2c02a4e035c17e2329aca12e"
            "21d514b25466931c7d8f6a5aac84aa05"
            "1ba30b396a0aac973d58e091"
        )
        tag = bytes.fromhex("5bc94fbc3221a5db94fae95ae7121a47")

        self.assertEqual(encrypt(key, nonce, plaintext, aad), (ciphertext, tag))
        self.assertEqual(decrypt(key, nonce, ciphertext, tag, aad), plaintext)
