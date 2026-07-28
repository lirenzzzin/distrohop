"""Small constant-interface AES-GCM implementation for Windows Chromium.

Only stdlib primitives are used. The implementation supports AES-128/192/256,
96-bit Chromium nonces and general GCM nonces. Authentication is verified
before plaintext is returned.
"""

from __future__ import annotations

import hmac
from functools import lru_cache
from typing import Iterable, List, Sequence, Tuple


class AuthenticationError(ValueError):
    pass


SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5,
    0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0,
    0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC,
    0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A,
    0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0,
    0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B,
    0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85,
    0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5,
    0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17,
    0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88,
    0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C,
    0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9,
    0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6,
    0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E,
    0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94,
    0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68,
    0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
)

RCON = (
    0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40,
    0x80, 0x1B, 0x36,
)


def _expand_key(key: bytes) -> Tuple[List[bytes], int]:
    if len(key) not in (16, 24, 32):
        raise ValueError("AES exige chave de 128, 192 ou 256 bits")
    nk = len(key) // 4
    rounds = nk + 6
    words: List[List[int]] = [
        list(key[offset:offset + 4]) for offset in range(0, len(key), 4)
    ]
    for index in range(nk, 4 * (rounds + 1)):
        temporary = list(words[index - 1])
        if index % nk == 0:
            temporary = temporary[1:] + temporary[:1]
            temporary = [SBOX[value] for value in temporary]
            temporary[0] ^= RCON[index // nk]
        elif nk > 6 and index % nk == 4:
            temporary = [SBOX[value] for value in temporary]
        words.append([
            words[index - nk][position] ^ temporary[position]
            for position in range(4)
        ])
    keys = [
        bytes(value for word in words[offset:offset + 4] for value in word)
        for offset in range(0, len(words), 4)
    ]
    return keys, rounds


def _xtime(value: int) -> int:
    return ((value << 1) ^ (0x1B if value & 0x80 else 0)) & 0xFF


def _mix_column(column: Sequence[int]) -> List[int]:
    total = column[0] ^ column[1] ^ column[2] ^ column[3]
    return [
        column[0] ^ total ^ _xtime(column[0] ^ column[1]),
        column[1] ^ total ^ _xtime(column[1] ^ column[2]),
        column[2] ^ total ^ _xtime(column[2] ^ column[3]),
        column[3] ^ total ^ _xtime(column[3] ^ column[0]),
    ]


def _word_tables() -> Tuple[
    Tuple[int, ...],
    Tuple[int, ...],
    Tuple[int, ...],
    Tuple[int, ...],
]:
    first: List[int] = []
    second: List[int] = []
    third: List[int] = []
    fourth: List[int] = []
    for substituted in SBOX:
        doubled = _xtime(substituted)
        tripled = doubled ^ substituted
        first.append(
            (doubled << 24) | (substituted << 16) | (substituted << 8) | tripled
        )
        second.append(
            (tripled << 24) | (doubled << 16) | (substituted << 8) | substituted
        )
        third.append(
            (substituted << 24) | (tripled << 16) | (doubled << 8) | substituted
        )
        fourth.append(
            (substituted << 24) | (substituted << 16) | (tripled << 8) | doubled
        )
    return tuple(first), tuple(second), tuple(third), tuple(fourth)


TE0, TE1, TE2, TE3 = _word_tables()


def _round_words(round_keys: Sequence[bytes]) -> Tuple[Tuple[int, ...], ...]:
    return tuple(
        tuple(
            int.from_bytes(key[offset:offset + 4], "big")
            for offset in range(0, 16, 4)
        )
        for key in round_keys
    )


def _encrypt_block_words(
    block: bytes,
    round_keys: Sequence[bytes],
    round_words: Sequence[Sequence[int]],
    rounds: int,
) -> bytes:
    if len(block) != 16:
        raise ValueError("bloco AES deve ter 16 bytes")
    words = [
        int.from_bytes(block[offset:offset + 4], "big")
        ^ round_words[0][offset // 4]
        for offset in range(0, 16, 4)
    ]
    for round_number in range(1, rounds):
        key = round_words[round_number]
        state0, state1, state2, state3 = words
        words = [
            TE0[state0 >> 24]
            ^ TE1[(state1 >> 16) & 0xFF]
            ^ TE2[(state2 >> 8) & 0xFF]
            ^ TE3[state3 & 0xFF]
            ^ key[0],
            TE0[state1 >> 24]
            ^ TE1[(state2 >> 16) & 0xFF]
            ^ TE2[(state3 >> 8) & 0xFF]
            ^ TE3[state0 & 0xFF]
            ^ key[1],
            TE0[state2 >> 24]
            ^ TE1[(state3 >> 16) & 0xFF]
            ^ TE2[(state0 >> 8) & 0xFF]
            ^ TE3[state1 & 0xFF]
            ^ key[2],
            TE0[state3 >> 24]
            ^ TE1[(state0 >> 16) & 0xFF]
            ^ TE2[(state1 >> 8) & 0xFF]
            ^ TE3[state2 & 0xFF]
            ^ key[3],
        ]
    state0, state1, state2, state3 = words
    final = bytes(
        (
            SBOX[state0 >> 24],
            SBOX[(state1 >> 16) & 0xFF],
            SBOX[(state2 >> 8) & 0xFF],
            SBOX[state3 & 0xFF],
            SBOX[state1 >> 24],
            SBOX[(state2 >> 16) & 0xFF],
            SBOX[(state3 >> 8) & 0xFF],
            SBOX[state0 & 0xFF],
            SBOX[state2 >> 24],
            SBOX[(state3 >> 16) & 0xFF],
            SBOX[(state0 >> 8) & 0xFF],
            SBOX[state1 & 0xFF],
            SBOX[state3 >> 24],
            SBOX[(state0 >> 16) & 0xFF],
            SBOX[(state1 >> 8) & 0xFF],
            SBOX[state2 & 0xFF],
        )
    )
    key = round_keys[rounds]
    return bytes(value ^ key[index] for index, value in enumerate(final))


def _encrypt_block(block: bytes, round_keys: Sequence[bytes], rounds: int) -> bytes:
    return _encrypt_block_words(
        block,
        round_keys,
        _round_words(round_keys),
        rounds,
    )


def _gf_multiply(left: int, right: int) -> int:
    result = 0
    value = right
    reduction = 0xE1000000000000000000000000000000
    for bit in range(128):
        if left & (1 << (127 - bit)):
            result ^= value
        value = (value >> 1) ^ (reduction if value & 1 else 0)
    return result


def _blocks(data: bytes) -> Iterable[bytes]:
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16]
        yield block + bytes(16 - len(block)) if len(block) < 16 else block


@lru_cache(maxsize=32)
def _multiplication_table(hash_key: bytes) -> Tuple[Tuple[int, ...], ...]:
    key = int.from_bytes(hash_key, "big")
    return tuple(
        tuple(
            _gf_multiply(value << (120 - position * 8), key)
            for value in range(256)
        )
        for position in range(16)
    )


def _multiply_with_table(value: int, table: Sequence[Sequence[int]]) -> int:
    result = 0
    encoded = value.to_bytes(16, "big")
    for position, byte in enumerate(encoded):
        result ^= table[position][byte]
    return result


def _ghash(hash_key: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    accumulator = 0
    table = _multiplication_table(hash_key)
    for block in _blocks(aad):
        accumulator = _multiply_with_table(
            accumulator ^ int.from_bytes(block, "big"),
            table,
        )
    for block in _blocks(ciphertext):
        accumulator = _multiply_with_table(
            accumulator ^ int.from_bytes(block, "big"),
            table,
        )
    lengths = (len(aad) * 8).to_bytes(8, "big") + (
        len(ciphertext) * 8
    ).to_bytes(8, "big")
    accumulator = _multiply_with_table(
        accumulator ^ int.from_bytes(lengths, "big"),
        table,
    )
    return accumulator.to_bytes(16, "big")


def _increment(counter: bytes) -> bytes:
    value = (int.from_bytes(counter[-4:], "big") + 1) & 0xFFFFFFFF
    return counter[:-4] + value.to_bytes(4, "big")


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(first ^ second for first, second in zip(left, right))


def _initial_counter(nonce: bytes, hash_key: bytes) -> bytes:
    if len(nonce) == 12:
        return nonce + b"\x00\x00\x00\x01"
    return _ghash(hash_key, b"", nonce)


def _crypt(
    data: bytes,
    initial: bytes,
    round_keys: Sequence[bytes],
    rounds: int,
) -> bytes:
    output = bytearray()
    counter = initial
    words = _round_words(round_keys)
    for offset in range(0, len(data), 16):
        counter = _increment(counter)
        block = data[offset:offset + 16]
        stream = _encrypt_block_words(counter, round_keys, words, rounds)
        output.extend(_xor(block, stream))
    return bytes(output)


def encrypt(
    key: bytes,
    nonce: bytes,
    plaintext: bytes,
    aad: bytes = b"",
    *,
    tag_length: int = 16,
) -> Tuple[bytes, bytes]:
    if not 12 <= tag_length <= 16:
        raise ValueError("tag GCM deve ter de 12 a 16 bytes")
    round_keys, rounds = _expand_key(key)
    hash_key = _encrypt_block(bytes(16), round_keys, rounds)
    initial = _initial_counter(nonce, hash_key)
    ciphertext = _crypt(plaintext, initial, round_keys, rounds)
    authentication = _ghash(hash_key, aad, ciphertext)
    tag = _xor(
        _encrypt_block(initial, round_keys, rounds),
        authentication,
    )[:tag_length]
    return ciphertext, tag


def decrypt(
    key: bytes,
    nonce: bytes,
    ciphertext: bytes,
    tag: bytes,
    aad: bytes = b"",
) -> bytes:
    if not 12 <= len(tag) <= 16:
        raise ValueError("tag GCM deve ter de 12 a 16 bytes")
    round_keys, rounds = _expand_key(key)
    hash_key = _encrypt_block(bytes(16), round_keys, rounds)
    initial = _initial_counter(nonce, hash_key)
    expected = _xor(
        _encrypt_block(initial, round_keys, rounds),
        _ghash(hash_key, aad, ciphertext),
    )[:len(tag)]
    if not hmac.compare_digest(expected, tag):
        raise AuthenticationError("tag AES-GCM inválida")
    return _crypt(ciphertext, initial, round_keys, rounds)
