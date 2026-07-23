import requests
import os
import sys

API_URL = "https://cc.freemodel.dev/v1/chat/completions"
API_KEY = os.environ.get("API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

raw_ids = os.environ.get("TELEGRAM_CHAT_IDS")

# Stop the script gracefully if the secret is missing, rather than throwing a hard error
if not raw_ids:
    print("Error: TELEGRAM_CHAT_IDS secret is missing or empty! Please check your GitHub Secrets and YAML file.")
    sys.exit(1)

# Strip whitespace around each id (protects against "id1, id2" style secrets)
TELEGRAM_CHAT_IDS = [cid.strip() for cid in raw_ids.split(',') if cid.strip()]

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

data = {
    "model": "free-fabel-5",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 5
}


def send_telegram_message(message: str) -> bool:
    """Send message to every configured chat id (Telegram only accepts one chat_id per call).

    Returns True only if ALL sends succeeded.
    """
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    all_success = True

    for chat_id in TELEGRAM_CHAT_IDS:
        tg_payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            tg_response = requests.post(tg_url, json=tg_payload, timeout=10)
            if tg_response.status_code == 200:
                print(f"✅ SUCCESS: Telegram message sent to chat_id={chat_id}")
            else:
                all_success = False
                print(f"❌ FAILED: Telegram message NOT sent to chat_id={chat_id} "
                      f"(HTTP {tg_response.status_code}) - {tg_response.text}")
        except Exception as e:
            all_success = False
            print(f"❌ FAILED: Telegram request error for chat_id={chat_id} - {e}")

    if all_success:
        print("✅ SUMMARY: All Telegram notifications sent successfully.")
    else:
        print("❌ SUMMARY: One or more Telegram notifications FAILED to send.")

    return all_success


try:
    response = requests.post(API_URL, headers=headers, json=data, timeout=15)

    if response.status_code == 200:
        # API is back up -> notify
        print("API is up.")
        message = "🚨 *ALERT:* The Free Fabel 5 API on cc.freemodel.dev is working again!"
        notified = send_telegram_message(message)
        sys.exit(0 if notified else 1)
    else:
        print(f"Still suspended or error: {response.status_code} - {response.text}")
        sys.exit(0)

except Exception as e:
    print(f"Request failed: {e}")
    sys.exit(1)
