import requests
import os
import sys

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
raw_ids = os.environ.get("TELEGRAM_CHAT_IDS")

if not raw_ids:
    print("Error: TELEGRAM_CHAT_IDS secret is missing or empty!")
    sys.exit(1)

TELEGRAM_CHAT_IDS = [cid.strip() for cid in raw_ids.split(',') if cid.strip()]
tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
message = "✅ TEST: This is a test message from the Telegram test script."

all_success = True
for chat_id in TELEGRAM_CHAT_IDS:
    tg_payload = {"chat_id": chat_id, "text": message}
    try:
        tg_response = requests.post(tg_url, json=tg_payload, timeout=10)
        if tg_response.status_code == 200:
            print(f"✅ SUCCESS: sent to chat_id={chat_id}")
        else:
            all_success = False
            print(f"❌ FAILED: chat_id={chat_id} (HTTP {tg_response.status_code}) - {tg_response.text}")
    except Exception as e:
        all_success = False
        print(f"❌ FAILED: chat_id={chat_id} - {e}")

sys.exit(0 if all_success else 1)
