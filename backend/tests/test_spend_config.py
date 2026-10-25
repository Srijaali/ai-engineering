"""The user-approved four-dollar harness ceiling cannot be raised by environment input."""
import pytest

from backend.app import config


def test_default_spend_ceiling_is_four_without_reading_real_secrets(monkeypatch):
    monkeypatch.setattr(config, 'dotenv_values', lambda _: {
        'OPENROUTER_API_KEY': 'test-placeholder', 'STUDENT_NAME': 'test', 'STUDENT_ROLL': 'test'})
    assert config.load_settings().max_spend_usd == 4


@pytest.mark.parametrize('amount', ['4.01', '5.00', 'nan', 'inf'])
def test_environment_cannot_silently_raise_approved_ceiling(monkeypatch, amount):
    monkeypatch.setattr(config, 'dotenv_values', lambda _: {
        'OPENROUTER_API_KEY': 'test-placeholder', 'STUDENT_NAME': 'test',
        'STUDENT_ROLL': 'test', 'MAX_SPEND_USD': amount})
    with pytest.raises(ValueError, match='user-approved'):
        config.load_settings()
