import os
import json
import time
import requests
from .observability import trace_ai_inference
from .notifications import send_twilio_alert

# --- CONFIGURATION ---
PROJECT_ID = os.getenv("PROJECT_ID", "hypercure")
LOCATION = os.getenv("VERTEX_AI_LOCATION", "us-central1")
API_KEY = os.getenv("GEMINI_API_KEY")

# RATE LIMITING & LOGGING CONTROL
RATE_LIMIT_DELAY = 15.0 
last_ai_call_time = 0.0
last_429_log_time = 0.0  # NEW: Controls error log frequency

# MODEL CONFIG
MODEL_ID = "gemini-2.0-flash-exp" 
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_ID}:generateContent?key={API_KEY}"

# --- HEXCEL 8552 KNOWLEDGE BASE ---
HEXCEL_KB = """
PRODUCT: HexPly 8552 (Amine cured toughened epoxy matrix)
CRITICAL SPECIFICATIONS:
1. CURE TEMP: Target 180°C (356°F). 
   - CONSTRAINT: Matrix degradation occurs > 185°C. 
   - SAFETY THRESHOLD: 175°C.
2. RAMP RATE: Max 3°C/min.
   - RISK: Rates > 3°C/min in thick laminates cause exothermic runaway.
3. PRESSURE: Standard 6-7 bar.
   - CONSTRAINT: Minimum 5 bar required during gelation to suppress voids.
4. VACUUM: > 0.95 bar (28 inHg).
   - FAILURE: Drop below 0.5 bar indicates bag failure/leak.

FAILURE RESPONSE PROTOCOLS:
- IF EXOTHERM DETECTED (High Rate OR High Temp):
  -> IMMEDIATE ACTION: ENGAGE BACKUP PUMP / EMERGENCY COOLING / NITROGEN VENT.
- IF VACUUM LOSS DETECTED:
  -> IMMEDIATE ACTION: CHECK BAG SEALS.
"""

if not API_KEY:
    print("❌ CRITICAL ERROR: GEMINI_API_KEY not found.")
else:
    print(f"✅ AI: Initialized Direct REST Client using model: {MODEL_ID}")

def generate_voice_alert(text):
    """ ElevenLabs Text-to-Speech Integration """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM") 
    if not api_key: return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }
    data = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.5}
    }
    try:
        response = requests.post(url, json=data, headers=headers, timeout=2) 
        if response.status_code == 200:
            return response.content
    except Exception:
        pass
    return None

def trigger_voice_if_critical(analysis):
    """ Checks risk level and triggers voice AND Twilio if CRITICAL """
    try:
        if analysis.get("risk_level") == "CRITICAL":
            alert_text = f"Critical Alert. {analysis.get('prediction', 'Anomaly Detected')}. {analysis.get('recommendation', 'Intervention Required')}."
            
            # Trigger Twilio SMS
            send_twilio_alert(alert_text)

            # Trigger ElevenLabs Voice
            audio_data = generate_voice_alert(alert_text)
            if audio_data:
                print(f"🔊 VOICE ALERT GENERATED: {len(audio_data)} bytes")
                
    except Exception as e:
        print(f"Trigger Logic Failed: {e}")

def get_deterministic_fallback(event_data):
    """ Safety net: If AI fails, return hardcoded physics rules. """
    evt_type = event_data.get("type", "UNKNOWN")
    
    if "EXOTHERM" in evt_type:
        return {
            "risk_level": "CRITICAL",
            "prediction": "Runaway Exothermic Reaction (Backup Rule)",
            "recommendation": "ENGAGE BACKUP PUMP"
        }
    if "VACUUM" in evt_type:
        return {
            # FIX 1: Set to WARNING so UI shows Yellow
            "risk_level": "WARNING",
            "prediction": "Vacuum Integrity Loss (Backup Rule)",
            "recommendation": "CHECK BAG SEALS"
        }
    return None

def analyze_anomaly(event_data):
    """ Analyzes Flink Event Data using Gemini via Direct REST API. """
    global last_ai_call_time, last_429_log_time
    
    # THROTTLE CHECK
    if time.time() - last_ai_call_time < RATE_LIMIT_DELAY:
        fallback = get_deterministic_fallback(event_data)
        if fallback and fallback.get('risk_level') == 'CRITICAL':
            trigger_voice_if_critical(fallback)
        return fallback
        
    prompt = f"""
    ROLE: HyperCure F1 Thermal Supervisor.
    KNOWLEDGE BASE:
    {HEXCEL_KB}
    
    LIVE TELEMETRY EVENT:
    {json.dumps(event_data)}
    
    TASK: Analyze the event against the Hexcel Specifications.
    OUTPUT: Valid JSON only. Do NOT use markdown code blocks. Just the raw JSON string.
    {{
        "risk_level": "CRITICAL" | "WARNING" | "LOW",
        "prediction": "Technical physics assessment based on Hexcel specs",
        "recommendation": "Specific mitigation action from protocols"
    }}
    """
    
    if API_KEY:
        try:
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 150,
                    "responseMimeType": "application/json"
                },
                "safetySettings": [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                ]
            }

            response = requests.post(URL, json=payload, timeout=5)
            
            if response.status_code == 200:
                result = response.json()
                try:
                    text_content = result['candidates'][0]['content']['parts'][0]['text']
                    text_content = text_content.replace("```json", "").replace("```", "").strip()
                    analysis = json.loads(text_content)
                    
                    last_ai_call_time = time.time()
                    
                    trace_ai_inference(analysis.get("risk_level", "UNKNOWN"))
                    trigger_voice_if_critical(analysis)
                    return analysis
                except (KeyError, IndexError, json.JSONDecodeError):
                    pass # Silent parse fail
            else:
                # FIX 2: Log Dampening for 429 Errors
                if response.status_code == 429:
                    current_time = time.time()
                    if current_time - last_429_log_time > 10.0: # Only print every 10 seconds
                        print("⏳ AI Rate Limit (429) - Using Fallback (Log Dampened)")
                        last_429_log_time = current_time
                else:
                    print(f"❌ AI API Error {response.status_code}")

        except Exception:
            pass # Silent connection fail
    
    # Fallback Logic
    fallback = get_deterministic_fallback(event_data)
    if fallback:
        trace_ai_inference(fallback["risk_level"] + "_FALLBACK")
        if fallback.get('risk_level') == 'CRITICAL':
            trigger_voice_if_critical(fallback)
        return fallback

    return {"risk_level": "UNKNOWN", "prediction": "System Nominal", "recommendation": "Monitor"}