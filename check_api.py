import requests
import os
import sys

API_URL = "YOUR_FREEMODEL_API_ENDPOINT"
API_KEY = os.environ.get("API_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") # E.g., a Discord or Telegram Webhook

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# Example payload - adjust based on what the API requires
data = {
    "prompt": "test",
    "model": "free-fabel-5"
}

try:
    response = requests.post(API_URL, headers=headers, json=data)
    
    # Check if the response indicates the suspension is lifted (e.g., a 200 OK)
    if response.status_code == 200:
        message = "🚨 Good news! The Free Fabel 5 API is working again."
        requests.post(WEBHOOK_URL, json={"content": message})
        print("API is up. Notification sent.")
        sys.exit(0)
    else:
        # If still suspended (e.g., 403 or specific error message), fail silently
        print(f"Still suspended or error: {response.status_code} - {response.text}")
        sys.exit(0)
        
except Exception as e:
    print(f"Request failed: {e}")
