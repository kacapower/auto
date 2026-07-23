import requests
import os
import sys

API_URL = "https://cc.freemodel.dev/v1/chat/completions" 
API_KEY = os.environ.get("API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# This converts your comma-separated string into a list of IDs
TELEGRAM_CHAT_IDS = os.environ.get("TELEGRAM_CHAT_IDS").split(',')
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

data = {
    "model": "free-fabel-5",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 5
}

try:
    response = requests.post(API_URL, headers=headers, json=data)
    
    if response.status_code != 200:
        message = "🚨 *ALERT:* The Free Fabel 5 API on cc.freemodel.dev is working again!"
        
        # Telegram API endpoint for sending a message
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        tg_payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        requests.post(tg_url, json=tg_payload)
        print("API is up. Telegram notification sent.")
        sys.exit(0)
    else:
        print(f"Still suspended or error: {response.status_code} - {response.text}")
        sys.exit(0)
        
except Exception as e:
    print(f"Request failed: {e}")
