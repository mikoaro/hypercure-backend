import time
import json
import random
import os
import queue
from datetime import datetime, timedelta
from confluent_kafka import Producer
from confluent_kafka.serialization import SerializationContext, MessageField
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONSerializer

class SimulationState:
    NORMAL = "NORMAL"
    VACUUM_LEAK = "VACUUM_LEAK"
    EXOTHERM = "EXOTHERM"
    MITIGATION = "MITIGATION"

current_state = SimulationState.NORMAL
current_temp = 20.0
is_running = False
local_queue = None

# Schema definition
SCHEMA_STR = """
{
  "title": "AutoclaveTelemetry",
  "type": "object",
  "properties": {
    "sensor_id": {"type": "string"},
    "timestamp": {"type": "number"},
    "temp_c": {"type": "number"},
    "pressure_bar": {"type": "number"},
    "vacuum_bar": {"type": "number"}
  }
}
"""

def delivery_report(err, msg):
    if err is not None:
        print(f"Delivery failed: {err}")

def get_producer_components():
    if not os.getenv('BOOTSTRAP_SERVERS'): return None, None
    conf = {
        'bootstrap.servers': os.getenv('BOOTSTRAP_SERVERS'),
        'security.protocol': 'SASL_SSL',
        'sasl.mechanisms': 'PLAIN',
        'sasl.username': os.getenv('CONFLUENT_API_KEY'),
        'sasl.password': os.getenv('CONFLUENT_API_SECRET')
    }
    sr_conf = {
        'url': os.getenv('SCHEMA_REGISTRY_URL'),
        'basic.auth.user.info': f"{os.getenv('SCHEMA_REGISTRY_API_KEY')}:{os.getenv('SCHEMA_REGISTRY_API_SECRET')}"
    }
    try:
        client = SchemaRegistryClient(sr_conf)
        serializer = JSONSerializer(SCHEMA_STR, client)
        producer = Producer(conf)
        return producer, serializer
    except Exception as e:
        print(f"Connection Error: {e}")
        return None, None

def set_mode(mode): 
    global current_state
    current_state = mode
    print(f"SIMULATOR: Mode switched to {mode}", flush=True)

def reset_sim(): 
    global current_temp, current_state
    current_temp = 20.0 
    current_state = SimulationState.NORMAL
    print("SIMULATOR: State Forced to NORMAL, Temp=20.0C", flush=True)

def run_simulation_loop():
    global current_temp, local_queue, current_state
    producer, serializer = get_producer_components()
    print(f"SIMULATOR: Starting. Kafka+SR Enabled: {producer is not None}", flush=True)

    tick_count = 0 

    while True:
        noise = random.uniform(-0.05, 0.05)
        
        # --- PHYSICS ENGINE TUNING (BUG 1 FIX) ---
        if current_state == SimulationState.EXOTHERM:
            # WAS: current_temp += 1.2 + noise (Too Fast)
            # NEW: 0.7 (Approx 2.8C per second). 
            # Allows alert to appear at ~90C while temp is still far from 180C.
            current_temp += 0.7 + noise 
            
        elif current_state == SimulationState.MITIGATION:
            # ACTIVE COOLING (Backup Pump Effect)
            current_temp -= 2.0 + noise
            
            # SOFT LANDING LOGIC
            if current_temp <= 22.0:
                current_temp = 20.0
                current_state = SimulationState.NORMAL
                
        elif current_temp > 20.0:
            # Passive cooling
            current_temp -= 0.5 
            if current_temp < 20.0: current_temp = 20.0
            
        else:
            # IDLE STATE (Stable at 20C)
            current_temp = 20.0 + abs(noise) 

        # Final Hard Clamp
        if current_temp < 20.0: current_temp = 20.0

        # ... (Rest of loop: Pressure/Vacuum logic/Kafka Produce remains the same) ...
        pressure = 6.0 + noise
        
        if current_state == SimulationState.VACUUM_LEAK:
            vacuum = 0.2 + noise 
        else:
            vacuum = 0.98 + noise
        
        future_time = datetime.now() + timedelta(seconds=10)
        
        telemetry = {
            "sensor_id": "TC-101",
            "timestamp": int(future_time.timestamp() * 1000),
            "temp_c": round(current_temp, 2),
            "pressure_bar": round(pressure, 2),
            "vacuum_bar": round(vacuum, 2)
        }

        if producer and serializer:
            try:
                producer.produce(
                    topic="autoclave_telemetry",
                    key=None,  
                    value=serializer(telemetry, SerializationContext("autoclave_telemetry", MessageField.VALUE)),
                    on_delivery=delivery_report
                )
                producer.poll(0)
            except Exception as e:
                print(f"Produce Error: {e}")

        if tick_count % 20 == 0:
            print(f"SIMULATOR ({current_state}): Sent Temp={telemetry['temp_c']} Vac={telemetry['vacuum_bar']}", flush=True)
        tick_count += 1

        if local_queue is not None:
            local_queue.put(json.dumps(telemetry))
        
        time.sleep(0.25)