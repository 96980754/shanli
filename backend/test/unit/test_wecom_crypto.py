import base64

from yuxi.services.wecom_crypto import WeComCrypto


def test_wecom_crypto_round_trip_and_signature():
    key = base64.b64encode(b"a" * 32).decode().rstrip("=")
    crypto = WeComCrypto("token", key, "corp")
    encrypted = crypto.encrypt("<xml>hello</xml>")

    assert crypto.decrypt(encrypted) == "<xml>hello</xml>"
    signature = crypto.signature("1", "nonce", encrypted)
    assert crypto.verify_signature(signature, "1", "nonce", encrypted)
