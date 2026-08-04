import requests
import os
import sys
import json

APIFY_TOKEN = os.environ.get("APIFY_API_TOKEN")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
raw_ids = os.environ.get("TELEGRAM_CHAT_IDS")

STATE_FILE = "last_seen.json"
ACTOR_ENDPOINT = "https://api.apify.com/v2/acts/streamers~youtube-scraper/run-sync-get-dataset-items"

# Creators to watch: key -> (display name, channel URL)
CHANNELS = {
    "gaurav_thakur": ("Gaurav Thakur", "https://www.youtube.com/@GauravThakur-GSF"),
    "getsetflyscience": ("GetsetflySCIENCE", "https://www.youtube.com/channel/UC00ifCvU8YOOzbL3RdiSTDw"),
    "veritasium": ("Veritasium", "https://www.youtube.com/@veritasium"),
    "mark_rober": ("Mark Rober", "https://www.youtube.com/@MarkRober"),
}

if not APIFY_TOKEN:
    print("Error: APIFY_API_TOKEN secret is missing! Add it in GitHub Secrets.")
    sys.exit(1)

if not TELEGRAM_TOKEN or not raw_ids:
    print("Error: TELEGRAM_TOKEN or TELEGRAM_CHAT_IDS secret is missing!")
    sys.exit(1)

TELEGRAM_CHAT_IDS = [cid.strip() for cid in raw_ids.split(',') if cid.strip()]


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_latest_video(channel_url: str) -> dict | None:
    """Ask the Apify actor for the single newest video on a channel."""
    payload = {
        "startUrls": [{"url": channel_url}],
        "maxResults": 1,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
        "sortVideosBy": "NEWEST",
    }
    params = {"token": APIFY_TOKEN}
    resp = requests.post(ACTOR_ENDPOINT, params=params, json=payload, timeout=120)
    resp.raise_for_status()
    items = resp.json()

    if not items:
        return None

    item = items[0]
    if item.get("error"):
        print(f"  Apify returned an error for {channel_url}: {item.get('error')} - {item.get('note')}")
        return None

    return item


def send_telegram_alert(creator_name: str, video: dict) -> bool:
    video_id = video.get("id", "")
    title = video.get("title", "Untitled")
    url = video.get("url") or f"https://www.youtube.com/watch?v={video_id}"
    duration = video.get("duration", "N/A")
    date = video.get("date", "N/A")
    thumbnail = video.get("thumbnailUrl") or (
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None
    )

    caption = (
        f"🎬 *New video from {creator_name}!*\n\n"
        f"*{title}*\n\n"
        f"⏱ Duration: {duration}\n"
        f"📅 Uploaded: {date}\n"
        f"🔗 {url}"
    )

    all_success = True
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            if thumbnail:
                tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
                tg_payload = {
                    "chat_id": chat_id,
                    "photo": thumbnail,
                    "caption": caption,
                    "parse_mode": "Markdown",
                }
            else:
                tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                tg_payload = {
                    "chat_id": chat_id,
                    "text": caption,
                    "parse_mode": "Markdown",
                }

            tg_response = requests.post(tg_url, json=tg_payload, timeout=15)

            # If sendPhoto fails (e.g. bad thumbnail URL), fall back to plain text
            if tg_response.status_code != 200 and thumbnail:
                tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                tg_payload = {
                    "chat_id": chat_id,
                    "text": caption,
                    "parse_mode": "Markdown",
                }
                tg_response = requests.post(tg_url, json=tg_payload, timeout=15)

            if tg_response.status_code == 200:
                print(f"  ✅ Telegram alert sent to chat_id={chat_id}")
            else:
                all_success = False
                print(f"  ❌ Telegram send failed for chat_id={chat_id} "
                      f"(HTTP {tg_response.status_code}) - {tg_response.text}")
        except Exception as e:
            all_success = False
            print(f"  ❌ Telegram request error for chat_id={chat_id} - {e}")

    return all_success


def main() -> int:
    state = load_state()
    overall_ok = True
    state_changed = False

    for key, (creator_name, channel_url) in CHANNELS.items():
        print(f"Checking {creator_name}...")
        try:
            video = fetch_latest_video(channel_url)
        except Exception as e:
            print(f"  ❌ Failed to fetch data for {creator_name}: {e}")
            overall_ok = False
            continue

        if not video:
            print(f"  No video data returned for {creator_name}.")
            continue

        video_id = video.get("id")
        if not video_id:
            print(f"  No video id in response for {creator_name}, skipping.")
            continue

        last_seen_id = state.get(key)

        if last_seen_id is None:
            # First run for this creator: just record baseline, no alert
            print(f"  Baseline set for {creator_name}: {video_id}")
            state[key] = video_id
            state_changed = True
            continue

        if video_id != last_seen_id:
            print(f"  🆕 New video detected for {creator_name}: {video_id}")
            notified = send_telegram_alert(creator_name, video)
            overall_ok = overall_ok and notified
            state[key] = video_id
            state_changed = True
        else:
            print(f"  No new video for {creator_name}.")

    if state_changed:
        save_state(state)
        print("State file updated.")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
