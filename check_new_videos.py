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
    "crazy_xyz": ("Crazy XYZ", "https://www.youtube.com/@crazyxyz"),
    "amit_xyz": ("Amit XYZ", "https://www.youtube.com/@AmitXYZ"),
    "varun_mayya": ("Varun Mayya", "https://www.youtube.com/@VarunMayya"),
}

# Content types to check for every channel above.
# "video"  -> regular long-form uploads
# "short"  -> YouTube Shorts
CONTENT_TYPES = ("video", "short")

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
    # Write atomically so a killed/cancelled run can't leave a truncated,
    # unparsable state file behind (which would silently reset baselines).
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, STATE_FILE)


def fetch_latest_content(channel_url: str, content_type: str) -> dict | None:
    """Ask the Apify actor for the single newest video OR short on a channel.

    content_type: "video" or "short". We make a dedicated call for each,
    rather than one combined call, so we don't have to guess at how the
    actor tags an item's type in the output schema.
    """
    if content_type == "short":
        payload = {
            "startUrls": [{"url": channel_url}],
            "maxResults": 0,
            "maxResultsShorts": 1,
            "maxResultStreams": 0,
            "sortVideosBy": "NEWEST",
        }
    else:
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

    try:
        items = resp.json()
    except ValueError:
        print(f"  Apify returned non-JSON content for {channel_url} ({content_type}).")
        return None

    if not isinstance(items, list):
        print(f"  Unexpected Apify response shape for {channel_url} ({content_type}): {items!r}")
        return None

    if not items:
        return None

    item = items[0]
    if not isinstance(item, dict):
        print(f"  Unexpected item shape for {channel_url} ({content_type}): {item!r}")
        return None

    if item.get("error"):
        print(f"  Apify returned an error for {channel_url} ({content_type}): "
              f"{item.get('error')} - {item.get('note')}")
        return None

    return item


def escape_markdown(text: str) -> str:
    """Escape characters that break Telegram's legacy 'Markdown' parse mode."""
    if not text:
        return text
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def send_telegram_alert(creator_name: str, content: dict, content_type: str) -> bool:
    video_id = content.get("id", "")
    title = content.get("title", "Untitled")
    url = content.get("url") or (
        f"https://www.youtube.com/shorts/{video_id}" if content_type == "short"
        else f"https://www.youtube.com/watch?v={video_id}"
    )
    duration = content.get("duration", "N/A")
    date = content.get("date", "N/A")
    thumbnail = content.get("thumbnailUrl") or (
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None
    )

    label = "Short" if content_type == "short" else "video"
    emoji = "🩳" if content_type == "short" else "🎬"
    safe_title = escape_markdown(title)

    caption = (
        f"{emoji} *New {label} from {escape_markdown(creator_name)}!*\n\n"
        f"*{safe_title}*\n\n"
        f"⏱ Duration: {duration}\n"
        f"📅 Uploaded: {date}\n"
        f"🔗 {url}"
    )
    # Plain-text fallback with no Markdown entities at all, used if the
    # Markdown-formatted send still fails for some other reason.
    plain_caption = (
        f"{emoji} New {label} from {creator_name}!\n\n"
        f"{title}\n\n"
        f"Duration: {duration}\n"
        f"Uploaded: {date}\n"
        f"{url}"
    )

    all_success = True
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            sent = False

            if thumbnail:
                tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
                tg_payload = {
                    "chat_id": chat_id,
                    "photo": thumbnail,
                    "caption": caption,
                    "parse_mode": "Markdown",
                }
                tg_response = requests.post(tg_url, json=tg_payload, timeout=15)
                if tg_response.status_code == 200:
                    sent = True
                else:
                    print(f"  ⚠️  sendPhoto failed for chat_id={chat_id} "
                          f"(HTTP {tg_response.status_code}) - {tg_response.text}")

            if not sent:
                # Fall back to a text message. Try Markdown first, then a
                # fully plain-text retry in case the title itself broke
                # Markdown parsing (not just the photo/thumbnail).
                tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                tg_payload = {
                    "chat_id": chat_id,
                    "text": caption,
                    "parse_mode": "Markdown",
                }
                tg_response = requests.post(tg_url, json=tg_payload, timeout=15)

                if tg_response.status_code == 200:
                    sent = True
                else:
                    print(f"  ⚠️  Markdown sendMessage failed for chat_id={chat_id} "
                          f"(HTTP {tg_response.status_code}), retrying as plain text.")
                    tg_payload = {"chat_id": chat_id, "text": plain_caption}
                    tg_response = requests.post(tg_url, json=tg_payload, timeout=15)
                    sent = tg_response.status_code == 200

            if sent:
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
        for content_type in CONTENT_TYPES:
            label = "Short" if content_type == "short" else "video"
            print(f"Checking {creator_name} ({label})...")
            try:
                content = fetch_latest_content(channel_url, content_type)
            except Exception as e:
                print(f"  ❌ Failed to fetch {label} data for {creator_name}: {e}")
                overall_ok = False
                continue

            if not content:
                print(f"  No {label} data returned for {creator_name}.")
                continue

            content_id = content.get("id")
            if not content_id:
                print(f"  No id in {label} response for {creator_name}, skipping.")
                continue

            state_key = f"{key}:{content_type}"
            last_seen_id = state.get(state_key)

            if last_seen_id is None:
                # First run for this creator/content type: record baseline, no alert.
                print(f"  Baseline set for {creator_name} ({label}): {content_id}")
                state[state_key] = content_id
                state_changed = True
                continue

            if content_id != last_seen_id:
                print(f"  🆕 New {label} detected for {creator_name}: {content_id}")
                notified = send_telegram_alert(creator_name, content, content_type)
                overall_ok = overall_ok and notified
                state[state_key] = content_id
                state_changed = True
            else:
                print(f"  No new {label} for {creator_name}.")

    if state_changed:
        save_state(state)
        print("State file updated.")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
