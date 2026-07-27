import asyncio
import pandas as pd
from motor_metricas import iniciar
import traceback

async def run():
    rq = asyncio.Queue()
    mq = asyncio.Queue()
    ejemplo = {
        "timestamp": "2025-06-15T22:31:00Z",
        "minuto": 22.5,
        "marcador": {"local": 1, "visitante": 0},
        "posesion": {"local": 62.0, "visitante": 38.0},
        "tiros": {"local": 8, "visitante": 2},
        "ataques_peligrosos": {"local": 12, "visitante": 3},
        "acciones": ["corner_local", "falta_visitante"],
    }
    await rq.put(ejemplo)
    
    try:
        await asyncio.wait_for(iniciar(rq, mq), timeout=1.0)
    except asyncio.TimeoutError:
        print("Timeout reached")
    except Exception as e:
        traceback.print_exc()
    
    if not mq.empty():
        print("Success, got message")

asyncio.run(run())
