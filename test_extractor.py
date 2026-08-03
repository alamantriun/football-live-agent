import asyncio
import sys
import logging
from extractor_flashscore import obtener_evento_flashscore, _crear_browser

logging.basicConfig(level=logging.INFO)

async def test_extractor(fixture_id):
    pw, browser, page = await _crear_browser()
    print(f"Buscando estadísticas para: {fixture_id}...")
    try:
        evento = await obtener_evento_flashscore(page, fixture_id)
        
        print("\n--- RESULTADO DE LA EXTRACCIÓN ---")
        print(f"Equipos: {evento['_equipos']['local']} vs {evento['_equipos']['visitante']}")
        print(f"Estado: {evento.get('_status')}")
        print(f"Marcador: {evento['marcador']['local']} - {evento['marcador']['visitante']}")
        
        for k, v in evento.items():
            if isinstance(v, dict) and 'local' in v:
                print(f"{k.capitalize()}: {v['local']} - {v['visitante']}")
    finally:
        await browser.close()
        await pw.stop()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        fid = sys.argv[1]
    else:
        fid = "jJucpA84" # España vs Austria
    asyncio.run(test_extractor(fid))
