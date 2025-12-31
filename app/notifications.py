import os
import time
from twilio.rest import Client

# --- TWILIO CONFIGURATION ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
TWILIO_TO_NUMBER = os.getenv("TWILIO_TO_NUMBER")

# --- ALERT THROTTLING ---
last_alert_timestamps = {}
SMS_COOLDOWN_SECONDS = 300  # 5 Minutes

def send_twilio_alert(message_body):
    """
    Sends an SMS via Twilio with built-in Debouncing (Throttling).
    """
    global last_alert_timestamps
    
    # 1. GENERATE SIGNATURE
    alert_signature = message_body[:20]
    current_time = time.time()

    # 2. CHECK THROTTLE
    if alert_signature in last_alert_timestamps:
        elapsed = current_time - last_alert_timestamps[alert_signature]
        if elapsed < SMS_COOLDOWN_SECONDS:
            # Silently skip
            return False

    # 3. VALIDATE CREDENTIALS
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_TO_NUMBER]):
        print("⚠️ Twilio Config Missing")
        return False

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        message = client.messages.create(
            body=f"🚨 HYPERCURE: {message_body}",
            from_=TWILIO_FROM_NUMBER,
            to=TWILIO_TO_NUMBER
        )
        
        # 4. UPDATE TIMESTAMP ON SUCCESS
        last_alert_timestamps[alert_signature] = current_time
        print(f"✅ Twilio SMS Sent! SID: {message.sid}")
        return True
        
    except Exception as e:
        # Don't print the full error if it's the daily limit
        if "exceeded the 50 daily" in str(e):
            print("❌ Twilio Daily Limit Reached (Wait 24h)")
        else:
            print(f"❌ Twilio Error: {e}")
        
        # Update timestamp to prevent retrying constantly
        last_alert_timestamps[alert_signature] = current_time
        return False