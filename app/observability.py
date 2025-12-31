# app/observability.py
try:
    from ddtrace import tracer
except ImportError:
    tracer = None

def trace_simulation_step(temp, pressure):
    """Safely traces physics simulation, ignores errors if Datadog is missing"""
    if not tracer: return
    try:
        with tracer.trace("hypercure.physics.calculation") as span:
            span.set_tag("sensor.temp", temp)
            span.set_tag("sensor.pressure", pressure)
    except Exception:
        pass

def trace_ai_inference(risk_level):
    """Safely traces AI decisions, preventing crashes on CRITICAL alerts"""
    if not tracer: return
    try:
        with tracer.trace("hypercure.ai.inference") as span:
            span.set_tag("ai.risk_level", risk_level)
            
            # CRITICAL FIX: Check if dogstatsd exists before calling it
            if risk_level == "CRITICAL" and hasattr(tracer, 'dogstatsd') and tracer.dogstatsd:
                try:
                    tracer.dogstatsd.increment('hypercure.ai.critical_alerts')
                except Exception:
                    pass
    except Exception:
        pass