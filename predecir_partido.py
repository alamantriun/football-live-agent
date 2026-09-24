"""Predicción histórica reproducible sin scraping ni dependencias externas."""
import argparse
import glob
import json
from datetime import date
from modelo_historico import cargar, pronosticar

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", nargs="+", required=True)
    p.add_argument("--local", required=True)
    p.add_argument("--visita", required=True)
    p.add_argument("--fecha", type=date.fromisoformat, required=True)
    args = p.parse_args()
    paths = sorted({f for patron in args.csv for f in glob.glob(patron)})
    resultado = pronosticar(cargar(paths), args.local, args.visita, args.fecha)
    resultado.pop("simulaciones")
    resultado.pop("matriz_marcadores")
    print(json.dumps(resultado, indent=2, ensure_ascii=False))
