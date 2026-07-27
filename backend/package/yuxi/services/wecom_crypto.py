"""The minimal Enterprise WeChat callback signature and AES helpers."""

from __future__ import annotations

import base64
import hashlib
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WeComCrypto:
    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        if not token or not encoding_aes_key or not corp_id:
            raise ValueError("WECOM_TOKEN, WECOM_ENCODING_AES_KEY and WECOM_CORP_ID must be configured")
        self.token = token
        self.key = base64.b64decode(f"{encoding_aes_key}=")
        self.corp_id = corp_id.encode()

    def signature(self, timestamp: str, nonce: str, encrypted: str) -> str:
        return hashlib.sha1("".join(sorted([self.token, timestamp, nonce, encrypted])).encode()).hexdigest()

    def verify_signature(self, signature: str, timestamp: str, nonce: str, encrypted: str) -> bool:
        return bool(signature) and self.signature(timestamp, nonce, encrypted) == signature

    def decrypt(self, encrypted: str) -> str:
        payload = base64.b64decode(encrypted)
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16]))
        padded = cipher.decryptor().update(payload) + cipher.decryptor().finalize()
        pad = padded[-1]
        if not 1 <= pad <= 32 or padded[-pad:] != bytes([pad]) * pad:
            raise ValueError("Invalid Enterprise WeChat payload padding")
        plain = padded[:-pad]
        content_length = struct.unpack("!I", plain[16:20])[0]
        content = plain[20 : 20 + content_length]
        if plain[20 + content_length :] != self.corp_id:
            raise ValueError("Enterprise WeChat CorpId does not match")
        return content.decode()

    def encrypt(self, text: str) -> str:
        plain = os.urandom(16) + struct.pack("!I", len(text.encode())) + text.encode() + self.corp_id
        padding = 32 - len(plain) % 32
        padded = plain + bytes([padding]) * padding
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16]))
        encrypted = cipher.encryptor().update(padded) + cipher.encryptor().finalize()
        return base64.b64encode(encrypted).decode()
