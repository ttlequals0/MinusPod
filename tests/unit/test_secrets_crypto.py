"""Tests for src/secrets_crypto.py."""
import base64
import os

import pytest

import secrets_crypto


@pytest.fixture(autouse=True)
def _crypto_env(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-passphrase')
    secrets_crypto.reset_cache()
    yield
    secrets_crypto.reset_cache()


def test_roundtrip(temp_db):
    env = secrets_crypto.encrypt(temp_db, 'sk-secret')
    assert env.startswith('enc:v1:')
    assert secrets_crypto.decrypt(temp_db, env) == 'sk-secret'


def test_ciphertext_varies_across_encrypts(temp_db):
    a = secrets_crypto.encrypt(temp_db, 'same')
    b = secrets_crypto.encrypt(temp_db, 'same')
    assert a != b
    assert secrets_crypto.decrypt(temp_db, a) == secrets_crypto.decrypt(temp_db, b) == 'same'


def test_encrypt_bytes_roundtrip(temp_db):
    payload = b"SQLite format 3\x00" + b"\x00" * 512
    blob = secrets_crypto.encrypt_bytes(temp_db, payload)
    assert blob.startswith(b"MPBK01\x00")
    assert blob != payload
    assert secrets_crypto.decrypt_bytes(temp_db, blob) == payload


def test_decrypt_bytes_rejects_non_envelope(temp_db):
    with pytest.raises(ValueError):
        secrets_crypto.decrypt_bytes(temp_db, b"raw plaintext blob")


def test_decrypt_bytes_rejects_truncated(temp_db):
    blob = secrets_crypto.encrypt_bytes(temp_db, b"hello")
    with pytest.raises(Exception):
        secrets_crypto.decrypt_bytes(temp_db, blob[:-4])


def test_missing_passphrase_raises(temp_db, monkeypatch):
    secrets_crypto.reset_cache()
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)
    assert not secrets_crypto.is_available()
    with pytest.raises(secrets_crypto.CryptoUnavailableError):
        secrets_crypto.encrypt(temp_db, 'x')


def test_salt_persisted_and_reused(temp_db):
    secrets_crypto.encrypt(temp_db, 'one')
    raw_salt = temp_db.get_setting('provider_crypto_salt')
    assert raw_salt
    assert len(base64.b64decode(raw_salt)) == 16
    # After resetting the DEK cache the same salt should yield a DEK that can
    # decrypt prior ciphertext (i.e., salt was not regenerated).
    env = secrets_crypto.encrypt(temp_db, 'two')
    secrets_crypto.reset_cache()
    assert secrets_crypto.decrypt(temp_db, env) == 'two'
    assert temp_db.get_setting('provider_crypto_salt') == raw_salt


def test_is_ciphertext():
    assert secrets_crypto.is_ciphertext('enc:v1:abc:def')
    assert not secrets_crypto.is_ciphertext('sk-plaintext')
    assert not secrets_crypto.is_ciphertext(None)
    assert not secrets_crypto.is_ciphertext('')


def test_db_secret_helpers(temp_db):
    temp_db.set_secret('openrouter_api_key', 'sk-or-1234')
    stored = temp_db.get_setting('openrouter_api_key')
    assert stored.startswith('enc:v1:')
    assert temp_db.get_secret('openrouter_api_key') == 'sk-or-1234'
    temp_db.clear_secret('openrouter_api_key')
    assert temp_db.get_secret('openrouter_api_key') is None


def test_legacy_plaintext_transparent_read(temp_db):
    """A pre-v1.2.0 plaintext row should still decrypt via get_secret."""
    temp_db.set_setting('openrouter_api_key', 'legacy-plain')
    assert temp_db.get_secret('openrouter_api_key') == 'legacy-plain'


def test_rotate_reencrypts_under_new_passphrase(temp_db, monkeypatch):
    temp_db.set_secret('anthropic_api_key', 'sk-ant-1')
    temp_db.set_secret('whisper_api_key', 'sk-w-2')
    old_salt = temp_db.get_setting('provider_crypto_salt')

    n = secrets_crypto.rotate(temp_db, 'test-passphrase', 'next-passphrase')
    assert n == 2
    assert temp_db.get_setting('provider_crypto_salt') != old_salt

    # New passphrase must be the active env value before the next derive.
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'next-passphrase')
    secrets_crypto.reset_cache()
    assert temp_db.get_secret('anthropic_api_key') == 'sk-ant-1'
    assert temp_db.get_secret('whisper_api_key') == 'sk-w-2'

    # Old passphrase no longer decrypts.
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-passphrase')
    secrets_crypto.reset_cache()
    assert temp_db.get_secret('anthropic_api_key') is None


def test_rotate_rejects_wrong_current_passphrase(temp_db):
    temp_db.set_secret('anthropic_api_key', 'sk-ant-1')
    with pytest.raises(ValueError, match='current passphrase mismatch'):
        secrets_crypto.rotate(temp_db, 'wrong', 'whatever')


def test_rotate_rejects_same_passphrase(temp_db):
    with pytest.raises(ValueError, match='must differ'):
        secrets_crypto.rotate(temp_db, 'test-passphrase', 'test-passphrase')


def test_admin_password_change_independence(temp_db):
    """Provider ciphertext is decoupled from admin auth state."""
    temp_db.set_secret('openrouter_api_key', 'sk-or-xyz')
    temp_db.set_setting('admin_password_hash', 'bcrypt$old')
    assert temp_db.get_secret('openrouter_api_key') == 'sk-or-xyz'
    # Simulate password change: ciphertext row untouched.
    temp_db.set_setting('admin_password_hash', 'bcrypt$new')
    assert temp_db.get_secret('openrouter_api_key') == 'sk-or-xyz'
