from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import threading
import json
import os
import queue 
from confluent_kafka import Consumer
from confluent_kafka.serialization import SerializationContext, MessageField
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.json_schema import JSONDeserializer
from dotenv import load_dotenv

load_dotenv()
from . import simulator, ai_agent

app = FastAPI(title="HyperCure 2.0 Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

manager = ConnectionManager()

async def local_queue_consumer():
    while True:
        while not simulator.local_queue.empty():
            try:
                data_str = simulator.local_queue.get_nowait()
                data_json = json.loads(data_str)
                await manager.broadcast(json.dumps({
                    "type": "TELEMETRY", "payload": data_json
                }))
            except Exception as e:
                pass 
        await asyncio.sleep(0.1)

# Global tracker for log dampening
last_alert_type = None

# --- KAFKA CONSUMER ---
def kafka_consumer_loop():
    global last_alert_type
    if not os.getenv('BOOTSTRAP_SERVERS'): return

    conf = {
        'bootstrap.servers': os.getenv('BOOTSTRAP_SERVERS'),
        'group.id': 'hypercure-backend-group-v6', 
        'auto.offset.reset': 'latest',
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
        schema_registry_client = SchemaRegistryClient(sr_conf)
        json_deserializer = JSONDeserializer(schema_str=None, schema_registry_client=schema_registry_client)
        consumer = Consumer(conf)
        consumer.subscribe(['autoclave_events']) 
        print("KAFKA: Connected & Subscribed to autoclave_events")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while True:
            msg = consumer.poll(0.1)
            if msg is None: continue
            if msg.error(): continue
            
            try:
                data_json = json_deserializer(msg.value(), SerializationContext(msg.topic(), MessageField.VALUE))
                if data_json is None: continue

                if msg.topic() == "autoclave_events":
                    alert_type = data_json.get('type', '')
                    
                    # 1. Filter Stale Exotherms
                    if 'EXOTHERM' in alert_type and simulator.current_temp < 50.0:
                        continue 

                    # 2. Filter Stale Vacuum Leaks
                    if 'VACUUM' in alert_type and simulator.current_state == simulator.SimulationState.NORMAL:
                         continue 

                    # FIX 3: Log Dampening (Prevents console flooding)
                    if alert_type != last_alert_type:
                        print(f"⚠️ NEW ALERT DETECTED: {alert_type}")
                        last_alert_type = alert_type

                    ai_analysis = ai_agent.analyze_anomaly(data_json)
                    loop.run_until_complete(manager.broadcast(json.dumps({
                        "type": "AI_INSIGHT", "payload": ai_analysis
                    })))
            except Exception as e:
                print(f"Serialization Error: {e}")

    except Exception as e:
        print(f"Critical Consumer Error: {e}")
    finally:
        if 'consumer' in locals(): consumer.close()

@app.on_event("startup")
async def startup_event():
    simulator.local_queue = queue.Queue()
    asyncio.create_task(local_queue_consumer())
    if os.getenv('BOOTSTRAP_SERVERS'):
        threading.Thread(target=kafka_consumer_loop, daemon=True).start()
    simulator.is_running = True
    threading.Thread(target=simulator.run_simulation_loop, daemon=True).start()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- CONTROLS ---
@app.post("/simulate/normal")
def set_normal():
    global last_alert_type
    last_alert_type = None 
    simulator.set_mode("NORMAL")
    return {"status": "success", "mode": "NORMAL"}

@app.post("/simulate/vacuum_leak")
def set_vacuum_leak():
    simulator.set_mode("VACUUM_LEAK")
    return {"status": "success", "mode": "VACUUM_LEAK"}

@app.post("/simulate/exotherm")
def set_exotherm():
    simulator.set_mode("EXOTHERM")
    return {"status": "success", "mode": "EXOTHERM"}

@app.post("/simulate/reset")
async def reset():
    global last_alert_type
    last_alert_type = None 
    simulator.reset_sim()
    await manager.broadcast(json.dumps({
        "type": "AI_INSIGHT", 
        "payload": None 
    }))
    return {"status": "reset"}

@app.post("/api/intervene")
def intervene():
    print("INTERVENTION: ENGAGING BACKUP PUMP / COOLING")
    simulator.set_mode("MITIGATION") 
    return {"status": "intervention_sent", "action": "ENGAGE_BACKUP_PUMP"}

@app.get("/health")
def health(): return {"status": "ok"}