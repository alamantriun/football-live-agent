import asyncio
import pandas as pd
from motor_metricas import iniciar, _motor
import motor_metricas

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
    
    task = asyncio.create_task(iniciar(rq, mq))
    await asyncio.sleep(0.5)
    
    if hasattr(motor_metricas, '_motor'):
        print("Columns:")
        print(motor_metricas._motor.df_buffer.columns)
        print("Data:")
        print(motor_metricas._motor.df_buffer)

asyncio.run(run())
