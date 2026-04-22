"""富友支付 service 层单元测试：RSA / 信封 / 通知验签 / 配置识别。"""
from __future__ import annotations

import json

import pytest


# ── 1. RSA 分块加解密：双密钥位数 + UTF-8 + 大文本 ──

class TestRsaBlockSizes:
    def test_block_sizes_1024(self, fake_keys_1024):
        from backend.app.services.fuiou_pay import _block_sizes

        plain, cipher = _block_sizes(fake_keys_1024["merchant_priv"])
        assert (plain, cipher) == (117, 128), "RSA-1024 PKCS1 v1.5 块大小必须是 117/128"

    def test_block_sizes_2048(self, fake_keys):
        from backend.app.services.fuiou_pay import _block_sizes

        plain, cipher = _block_sizes(fake_keys["merchant_priv"])
        assert (plain, cipher) == (245, 256), "RSA-2048 PKCS1 v1.5 块大小必须是 245/256"

    @pytest.mark.parametrize("bits_fixture", ["fake_keys", "fake_keys_1024"])
    def test_roundtrip_short(self, request, bits_fixture):
        keys = request.getfixturevalue(bits_fixture)
        from backend.app.services.fuiou_pay import _rsa_decrypt, _rsa_encrypt

        merchant_pub_for_encrypt = keys["merchant_priv"].publickey()
        text = "hello-fuiou"
        ct = _rsa_encrypt(text, merchant_pub_for_encrypt)
        pt = _rsa_decrypt(ct, keys["merchant_priv"])
        assert pt == text

    def test_roundtrip_chinese_emoji(self, fake_keys):
        from backend.app.services.fuiou_pay import _rsa_decrypt, _rsa_encrypt

        text = "中文/特殊 字符 😀 + symbols !@#$%^&*"
        ct = _rsa_encrypt(text, fake_keys["merchant_pub"])
        pt = _rsa_decrypt(ct, fake_keys["merchant_priv"])
        assert pt == text

    def test_roundtrip_multi_block_2048(self, fake_keys):
        """2048 单块 245 字节，构造 ~700 字节明文必走 3 块加密。"""
        from backend.app.services.fuiou_pay import _rsa_decrypt, _rsa_encrypt

        text = "x" * 700
        ct = _rsa_encrypt(text, fake_keys["merchant_pub"])
        pt = _rsa_decrypt(ct, fake_keys["merchant_priv"])
        assert pt == text

    def test_roundtrip_multi_block_1024(self, fake_keys_1024):
        """1024 单块 117 字节，构造 350 字节明文必走 3 块加密。"""
        from backend.app.services.fuiou_pay import _rsa_decrypt, _rsa_encrypt

        m_pub = fake_keys_1024["merchant_priv"].publickey()
        text = "y" * 350
        ct = _rsa_encrypt(text, m_pub)
        pt = _rsa_decrypt(ct, fake_keys_1024["merchant_priv"])
        assert pt == text

    def test_decrypt_wrong_key_raises(self, fake_keys):
        """用富友公钥加密的密文，商户私钥无法解 → RuntimeError。"""
        from backend.app.services.fuiou_pay import _rsa_decrypt, _rsa_encrypt

        ct = _rsa_encrypt("secret", fake_keys["fuiou_pub"])
        with pytest.raises(RuntimeError, match="解密失败"):
            _rsa_decrypt(ct, fake_keys["merchant_priv"])

    def test_encrypt_no_pubkey_raises(self):
        from backend.app.services.fuiou_pay import _rsa_encrypt

        with pytest.raises(RuntimeError, match="缺少富友公钥"):
            _rsa_encrypt("x", None)

    def test_decrypt_no_privkey_raises(self):
        from backend.app.services.fuiou_pay import _rsa_decrypt

        with pytest.raises(RuntimeError, match="缺少商户私钥"):
            _rsa_decrypt("YWJj", None)


# ── 2. 签名 / 验签 ──

class TestRsaSign:
    def test_sign_and_verify_ok(self, fake_keys):
        from backend.app.services.fuiou_pay import _rsa_sign, _rsa_verify

        text = '{"order_id":"R1_abc","order_amt":"100"}'
        sign = _rsa_sign(text, fake_keys["fuiou_priv"])
        assert _rsa_verify(text, sign, fake_keys["fuiou_pub"]) is True

    def test_verify_tampered_text(self, fake_keys):
        from backend.app.services.fuiou_pay import _rsa_sign, _rsa_verify

        text = '{"order_id":"R1_abc","order_amt":"100"}'
        sign = _rsa_sign(text, fake_keys["fuiou_priv"])
        # 篡改一个字节
        bad = text.replace('"100"', '"999"')
        assert _rsa_verify(bad, sign, fake_keys["fuiou_pub"]) is False

    def test_verify_wrong_pubkey(self, fake_keys):
        """用商户公钥校验富友私钥的签名 → False。"""
        from backend.app.services.fuiou_pay import _rsa_sign, _rsa_verify

        text = "x"
        sign = _rsa_sign(text, fake_keys["fuiou_priv"])
        assert _rsa_verify(text, sign, fake_keys["merchant_pub"]) is False

    def test_verify_empty_sign(self, fake_keys):
        from backend.app.services.fuiou_pay import _rsa_verify

        assert _rsa_verify("x", "", fake_keys["fuiou_pub"]) is False

    def test_verify_corrupt_sign(self, fake_keys):
        from backend.app.services.fuiou_pay import _rsa_verify

        assert _rsa_verify("x", "!!!not-base64!!!", fake_keys["fuiou_pub"]) is False


# ── 3. 信封构造 + 自洽（商户加密 → 模拟富友解密） ──

class TestEnvelope:
    def test_build_envelope_decryptable_by_fuiou(self, patch_fuiou_settings, fake_keys):
        """商户用富友公钥加密 → 富友能用富友私钥解出原 JSON。"""
        from backend.app.services.fuiou_pay import _build_envelope, _rsa_decrypt

        plain = {"order_id": "R1_abc", "order_amt": "100", "ver": "1.0.0"}
        env = _build_envelope(plain)
        assert env["mchnt_cd"] == "0001000F0040992"
        assert env["message"] and isinstance(env["message"], str)
        # 模拟富友：用富友私钥解密 message
        plain_text = _rsa_decrypt(env["message"], fake_keys["fuiou_priv"])
        assert json.loads(plain_text) == plain

    def test_build_envelope_chinese_safe(self, patch_fuiou_settings, fake_keys):
        from backend.app.services.fuiou_pay import _build_envelope, _rsa_decrypt

        plain = {"goods_name": "充值-100积分", "goods_detail": "测试 中文 ✓"}
        env = _build_envelope(plain)
        plain_text = _rsa_decrypt(env["message"], fake_keys["fuiou_priv"])
        assert json.loads(plain_text) == plain


# ── 4. 异步通知验签：parse_notify 各种合法/非法分支 ──

class TestParseNotify:
    def test_legit_notify_with_sign(self, patch_fuiou_settings, make_notify):
        from backend.app.services.fuiou_pay import parse_notify

        plain = {"order_id": "R1_x", "order_amt": "100", "order_st": "1"}
        env = make_notify(plain, with_sign=True)
        ok, got = parse_notify(env)
        assert ok is True
        assert got == plain

    def test_legit_notify_without_sign(self, patch_fuiou_settings, make_notify):
        """如果富友没带 sign 字段，按当前实现允许（只看 mchnt_cd + 解密）。"""
        from backend.app.services.fuiou_pay import parse_notify

        plain = {"order_id": "R1_x", "order_amt": "100", "order_st": "1"}
        env = make_notify(plain, with_sign=False)
        ok, got = parse_notify(env)
        assert ok is True
        assert got == plain

    def test_mchnt_cd_mismatch(self, patch_fuiou_settings, make_notify):
        from backend.app.services.fuiou_pay import parse_notify

        env = make_notify({"order_id": "x"}, mchnt_cd="9999999999OTHER")
        ok, _ = parse_notify(env)
        assert ok is False

    def test_message_missing(self, patch_fuiou_settings):
        from backend.app.services.fuiou_pay import parse_notify

        ok, _ = parse_notify({"mchnt_cd": "0001000F0040992", "message": ""})
        assert ok is False

    def test_message_tampered_cipher_bytes(self, patch_fuiou_settings, make_notify):
        """篡改 message 的中段字节 → RSA 解密失败。"""
        from backend.app.services.fuiou_pay import parse_notify

        env = make_notify({"order_id": "x"})
        # 把 message 中段 16 字节变成全 A
        msg = env["message"]
        env["message"] = msg[:16] + ("A" * 16) + msg[32:]
        ok, _ = parse_notify(env)
        assert ok is False

    def test_message_decrypts_but_not_json(self, patch_fuiou_settings, fake_keys):
        """message 能解密但内容不是合法 JSON → 拒绝。"""
        from backend.app.services.fuiou_pay import _rsa_encrypt, parse_notify

        cipher = _rsa_encrypt("not a json {{{ broken", fake_keys["merchant_pub"])
        env = {"mchnt_cd": "0001000F0040992", "message": cipher}
        ok, _ = parse_notify(env)
        assert ok is False

    def test_sign_tampered(self, patch_fuiou_settings, make_notify, fake_keys):
        """sign 字段被篡改一位 → 验签失败 → 拒绝。"""
        from backend.app.services.fuiou_pay import parse_notify

        env = make_notify({"order_id": "x"}, with_sign=True)
        original = env["sign"]
        # 用同一密钥对另一段文本签名，制造一个"真但不匹配"的签名（更强对抗）
        from backend.app.services.fuiou_pay import _rsa_sign

        env["sign"] = _rsa_sign("totally different text", fake_keys["fuiou_priv"])
        assert env["sign"] != original
        ok, _ = parse_notify(env)
        assert ok is False

    def test_envelope_not_a_dict(self, patch_fuiou_settings):
        from backend.app.services.fuiou_pay import parse_notify

        ok, got = parse_notify("string instead of dict")  # type: ignore[arg-type]
        assert ok is False and got == {}


# ── 5. fuiou_configured：只要任一关键项缺失就 False ──

class TestConfigured:
    def test_all_set(self, patch_fuiou_settings):
        from backend.app.services.fuiou_pay import fuiou_configured

        assert fuiou_configured() is True

    @pytest.mark.parametrize("missing", [
        "fuiou_mchnt_cd",
        "fuiou_gateway_pay_url",
        "fuiou_merchant_private_key_path",
        "fuiou_fuiou_public_key_path",
    ])
    def test_missing_field_returns_false(self, patch_fuiou_settings, monkeypatch, missing):
        from backend.app.services.fuiou_pay import fuiou_configured

        monkeypatch.setattr(patch_fuiou_settings, missing, "", raising=False)
        assert fuiou_configured() is False

    def test_pem_path_not_exist(self, patch_fuiou_settings, monkeypatch, tmp_path):
        from backend.app.services.fuiou_pay import fuiou_configured

        monkeypatch.setattr(
            patch_fuiou_settings,
            "fuiou_merchant_private_key_path",
            str(tmp_path / "nope.pem"),
            raising=False,
        )
        assert fuiou_configured() is False

    def test_pem_unparseable(self, patch_fuiou_settings, monkeypatch, tmp_path):
        from backend.app.services.fuiou_pay import fuiou_configured

        bad = tmp_path / "bad.pem"
        bad.write_text("-----BEGIN PRIVATE KEY-----\ngarbage\n-----END PRIVATE KEY-----\n")
        monkeypatch.setattr(
            patch_fuiou_settings,
            "fuiou_merchant_private_key_path",
            str(bad),
            raising=False,
        )
        assert fuiou_configured() is False


# ── 6. 工具函数：gen_order_id / today_order_date ──

class TestUtils:
    def test_order_id_max_30(self):
        from backend.app.services.fuiou_pay import gen_order_id

        oid = gen_order_id(99999999)
        assert len(oid) <= 30
        assert oid.startswith("R")

    def test_order_id_unique(self):
        from backend.app.services.fuiou_pay import gen_order_id

        ids = {gen_order_id(1) for _ in range(100)}
        assert len(ids) == 100

    def test_today_order_date_format(self):
        import re

        from backend.app.services.fuiou_pay import today_order_date

        assert re.fullmatch(r"\d{8}", today_order_date())
