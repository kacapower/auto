import requests
import os
import pytest


def test_telegram_send():
    token = os.environ.get("TELEGRAM_TOKEN")
    raw_ids = os.environ.get("TELEGRAM_CHAT_IDS")

    if not raw_ids:
        pytest.skip("TELEGRAM_CHAT_IDS not set — skipping Telegram test")

    chat_ids = [cid.strip() for cid in raw_ids.split(',') if cid.strip()]
    tg_url = f"https://api.telegram.org/bot{token}/sendMessage"
    message = "✅ TEST: This is a test message from the Telegram test script."

    failures = []
    for chat_id in chat_ids:
        payload = {"chat_id": chat_id, "text": message}
        try:
            resp = requests.post(tg_url, json=payload, timeout=10)
            if resp.status_code != 200:
                failures.append(f"chat_id={chat_id} HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            failures.append(f"chat_id={chat_id}: {e}")

    assert not failures, "Telegram send failures:\n" + "\n".join(failures)
