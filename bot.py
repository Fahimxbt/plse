import os
import asyncio
import traceback
from datetime import datetime

from telethon import TelegramClient, events
from telethon.sessions import StringSession

# =============================================================================
# CONFIGURATION - All from Railway env vars
# =============================================================================
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PULSE_BOT = os.getenv("PULSE_BOT", "PulseSMSReBoT")

# Countries to cycle through (in order)
COUNTRIES = os.getenv("COUNTRIES", "Thailand,Tunisia").split(",")
PRICE_BUTTON = os.getenv("PRICE_BUTTON", "$0.25")

# Delays (seconds)
CLICK_DELAY = float(os.getenv("CLICK_DELAY", "0.5"))
RESULT_WAIT = float(os.getenv("RESULT_WAIT", "8"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "3"))

# =============================================================================
# TELEGRAM CLIENT
# =============================================================================
client = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)

def now():
    return datetime.now().strftime("%H:%M:%S")

def strip_emoji(text):
    import re
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\U00002600-\U000026FF"
        "\U00002700-\U000027BF"
        "]+", flags=re.UNICODE
    )
    return emoji_pattern.sub(r'', text).strip()

# =============================================================================
# BUTTON HELPERS
# =============================================================================
async def find_and_click_button(button_text, message_id=None, search_recent=False, limit=10):
    """Find and click a button by text (case-insensitive, emoji-stripped)"""
    target = button_text.lower().strip()
    try:
        if message_id:
            try:
                msg = await client.get_messages(PULSE_BOT, ids=message_id)
                if msg and msg.buttons:
                    for row in msg.buttons:
                        for btn in row:
                            clean = strip_emoji(btn.text).lower().strip()
                            if target in clean or clean in target or target in btn.text.lower():
                                await btn.click()
                                print(f"[{now()}] -> Clicked: '{btn.text}' (msg_id={message_id})")
                                return True
            except Exception:
                pass

        async for msg in client.iter_messages(PULSE_BOT, limit=limit if search_recent else 5):
            if msg.buttons:
                for row in msg.buttons:
                    for btn in row:
                        clean = strip_emoji(btn.text).lower().strip()
                        if target in clean or clean in target or target in btn.text.lower():
                            await btn.click()
                            print(f"[{now()}] -> Clicked: '{btn.text}' (found in recent)")
                            return True
        return False
    except Exception as e:
        print(f"[{now()}] [Button Error] {e}")
        return False

async def click_price(price_text):
    return await find_and_click_button(price_text, search_recent=True, limit=10)

async def click_country(country_name):
    return await find_and_click_button(country_name, search_recent=True, limit=15)

async def click_ok():
    return await find_and_click_button("OK", search_recent=True, limit=5)

# =============================================================================
# STATE MACHINE
# =============================================================================
class BuyerState:
    IDLE = "idle"
    WAITING_RESULT = "waiting_result"
    SUCCESS = "success"
    NO_NUMBERS = "no_numbers"
    ERROR = "error"

class AutoBuyer:
    def __init__(self):
        self.state = BuyerState.IDLE
        self.country_index = 0
        self.attempts = 0
        self.success_count = 0
        self.fail_count = 0
        self._lock = asyncio.Lock()
        self._running = True
        self._last_msg_id = None

    def current_country(self):
        return COUNTRIES[self.country_index % len(COUNTRIES)]

    def next_country(self):
        self.country_index += 1
        return self.current_country()

buyer = AutoBuyer()

# =============================================================================
# MESSAGE HANDLER
# =============================================================================
@client.on(events.NewMessage(chats=PULSE_BOT))
async def handle_pulse_message(event):
    text = event.message.text or ""
    msg_id = event.message.id
    has_buttons = event.message.buttons is not None and len(event.message.buttons) > 0
    text_lower = text.lower()

    # Track latest message with buttons
    if has_buttons:
        buyer._last_msg_id = msg_id

    # --- SUCCESS: Number Reserved ---
    if "number reserved" in text_lower or "otp received" in text_lower:
        print(f"[{now()}] SUCCESS! Number acquired!")
        print(f"[{now()}] {text}")
        buyer.success_count += 1
        async with buyer._lock:
            buyer.state = BuyerState.SUCCESS
        await asyncio.sleep(5)
        async with buyer._lock:
            buyer.state = BuyerState.IDLE
        return

    # --- NO NUMBERS AVAILABLE ---
    if "no numbers available" in text_lower:
        print(f"[{now()}] No numbers available for {buyer.current_country()}")
        buyer.fail_count += 1
        async with buyer._lock:
            buyer.state = BuyerState.NO_NUMBERS
        return

    # --- TRYING TO PURCHASE ---
    if "trying to purchase" in text_lower:
        print(f"[{now()}] Purchase in progress for {buyer.current_country()}...")
        async with buyer._lock:
            buyer.state = BuyerState.WAITING_RESULT
        return

    # --- PROGRESS PERCENTAGE (0%, 10%, etc) ---
    stripped = text.strip()
    if stripped.endswith("%") and stripped.replace("%","").isdigit():
        print(f"[{now()}] Progress: {stripped}")
        return

# =============================================================================
# MAIN BUY LOOP
# =============================================================================
async def buy_loop():
    await asyncio.sleep(3)  # Wait for client to be ready

    while buyer._running:
        try:
            async with buyer._lock:
                state = buyer.state

            if state in [BuyerState.IDLE, BuyerState.NO_NUMBERS, BuyerState.ERROR, BuyerState.SUCCESS]:
                country = buyer.current_country()
                buyer.attempts += 1
                print(f"[{now()}] === ATTEMPT #{buyer.attempts} | Country: {country} | Success: {buyer.success_count} | Fail: {buyer.fail_count} ===")

                # Step 1: Click the country name (Thailand or Tunisia)
                print(f"[{now()}] Clicking {country}...")
                if not await click_country(country):
                    print(f"[{now()}] {country} button not found, retrying in {RETRY_DELAY}s...")
                    await asyncio.sleep(RETRY_DELAY)
                    continue

                await asyncio.sleep(CLICK_DELAY)

                # Step 2: Click the price ($0.25)
                print(f"[{now()}] Clicking {PRICE_BUTTON}...")
                if not await click_price(PRICE_BUTTON):
                    print(f"[{now()}] Price button not found, retrying in {RETRY_DELAY}s...")
                    await asyncio.sleep(RETRY_DELAY)
                    continue

                await asyncio.sleep(CLICK_DELAY)

                # Step 3: Wait for result
                print(f"[{now()}] Waiting {RESULT_WAIT}s for result...")
                async with buyer._lock:
                    buyer.state = BuyerState.WAITING_RESULT

                await asyncio.sleep(RESULT_WAIT)

                # Check state after wait
                async with buyer._lock:
                    current = buyer.state

                if current == BuyerState.WAITING_RESULT:
                    # Still waiting, maybe stuck. Switch country and retry
                    print(f"[{now()}] Still waiting after {RESULT_WAIT}s, switching country...")
                    buyer.next_country()

                elif current == BuyerState.NO_NUMBERS:
                    # Click OK to dismiss popup, then switch country immediately
                    print(f"[{now()}] No numbers popup detected. Clicking OK...")
                    await asyncio.sleep(CLICK_DELAY)
                    await click_ok()
                    await asyncio.sleep(CLICK_DELAY)
                    print(f"[{now()}] Switching to next country...")
                    buyer.next_country()
                    async with buyer._lock:
                        buyer.state = BuyerState.IDLE

                elif current == BuyerState.SUCCESS:
                    # Got a number! Wait a bit then continue for more
                    print(f"[{now()}] Got number! Waiting 10s before next attempt...")
                    await asyncio.sleep(10)
                    async with buyer._lock:
                        buyer.state = BuyerState.IDLE

            else:
                # In some intermediate state, just wait
                await asyncio.sleep(1)

        except Exception as e:
            print(f"[{now()}] [CRITICAL ERROR] {e}")
            traceback.print_exc()
            async with buyer._lock:
                buyer.state = BuyerState.ERROR
            await asyncio.sleep(RETRY_DELAY)

# =============================================================================
# COMMANDS (from self)
# =============================================================================
@client.on(events.NewMessage(pattern=r"/status", from_users="me"))
async def cmd_status(event):
    status = f"""Pulse SMS Auto-Buyer Status:
- State: {buyer.state}
- Current Country: {buyer.current_country()}
- Attempts: {buyer.attempts}
- Success: {buyer.success_count}
- Fail: {buyer.fail_count}
- Countries: {', '.join(COUNTRIES)}
- Price: {PRICE_BUTTON}"""
    await event.reply(status)

@client.on(events.NewMessage(pattern=r"/stop", from_users="me"))
async def cmd_stop(event):
    buyer._running = False
    await event.reply("Stopping buyer...")

@client.on(events.NewMessage(pattern=r"/startbuy", from_users="me"))
async def cmd_start_buy(event):
    buyer._running = True
    async with buyer._lock:
        buyer.state = BuyerState.IDLE
    await event.reply("Starting buyer loop...")
    asyncio.create_task(buy_loop())

# =============================================================================
# MAIN
# =============================================================================
async def main():
    print("Pulse SMS Auto-Buyer starting...")
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        print("Missing API credentials!")
        return
    if not SESSION_STRING:
        print("Need SESSION_STRING!")
        return

    await client.start()
    me = await client.get_me()
    print(f"Logged in as {me.first_name}")
    print(f"Target bot: @{PULSE_BOT}")
    print(f"Countries: {COUNTRIES}")
    print(f"Price: {PRICE_BUTTON}")

    # Start the buyer loop
    asyncio.create_task(buy_loop())

    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\nShutdown...")
    except Exception as e:
        print(f"Fatal: {e}")
        traceback.print_exc()
