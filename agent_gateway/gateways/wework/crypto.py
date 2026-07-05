"""企业微信回调验签与解密工具。"""

from __future__ import annotations

import base64
import hashlib
import struct

try:
    from Crypto.Cipher import AES
except ImportError:  # pragma: no cover - optional dependency
    AES = None  # type: ignore[assignment]


class WeWorkCryptoError(ValueError):
    """企业微信回调加解密失败。"""


def verify_signature(
    *,
    token: str,
    signature: str,
    timestamp: str,
    nonce: str,
    encrypted: str,
) -> bool:
    """校验企业微信回调签名。"""

    if not token or not signature or not timestamp or not nonce or not encrypted:
        return False
    raw = "".join(sorted([token, timestamp, nonce, encrypted]))
    expected = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return expected == signature


def decrypt_message(encrypted: str, encoding_aes_key: str, receive_id: str = "") -> str:
    """解密企业微信回调密文，返回明文 XML 或 echostr。"""

    if AES is None:
        raise WeWorkCryptoError("pycryptodome is required for WeWork encrypted callback")
    if len(encoding_aes_key) != 43:
        raise WeWorkCryptoError("invalid WeWork EncodingAESKey length")
    try:
        aes_key = base64.b64decode(f"{encoding_aes_key}=")
    except Exception as exc:  # pragma: no cover - defensive
        raise WeWorkCryptoError("invalid WeWork EncodingAESKey") from exc
    if len(aes_key) != 32:
        raise WeWorkCryptoError("invalid WeWork AES key")

    try:
        encrypted_bytes = base64.b64decode(encrypted)
    except Exception as exc:
        raise WeWorkCryptoError("invalid encrypted payload") from exc
    if len(encrypted_bytes) % AES.block_size != 0:
        raise WeWorkCryptoError("invalid encrypted payload block size")

    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    decrypted = _pkcs7_unpad(cipher.decrypt(encrypted_bytes))
    if len(decrypted) < 20:
        raise WeWorkCryptoError("invalid decrypted payload")

    message_length = struct.unpack(">I", decrypted[16:20])[0]
    message_start = 20
    message_end = message_start + message_length
    message = decrypted[message_start:message_end]
    actual_receive_id = decrypted[message_end:].decode("utf-8", errors="ignore")
    if receive_id and actual_receive_id and actual_receive_id != receive_id:
        raise WeWorkCryptoError("WeWork receive id mismatch")
    return message.decode("utf-8")


def _pkcs7_unpad(payload: bytes) -> bytes:
    """移除企业微信 AES-CBC 的 PKCS#7 padding。"""

    if not payload:
        raise WeWorkCryptoError("empty decrypted payload")
    pad = payload[-1]
    if pad < 1 or pad > 32:
        raise WeWorkCryptoError("invalid padding")
    if payload[-pad:] != bytes([pad]) * pad:
        raise WeWorkCryptoError("invalid padding bytes")
    return payload[:-pad]
