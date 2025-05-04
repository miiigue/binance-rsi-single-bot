#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys

# Importar la instancia 'app' de Flask y el logger desde nuestro paquete src
# Nota: Esto asume que src/__init__.py existe.
from src.api_server import app, get_logger

if __name__ == '__main__':
    # Obtener el logger configurado en api_server
    logger = get_logger()
    if not logger:
        print("ERROR CRÍTICO: Logger no disponible al intentar iniciar desde run_api.py.", file=sys.stderr)
        sys.exit(1)

    logger.info("Iniciando servidor API Flask desde run_api.py...")
    try:
        # Ejecutar la aplicación Flask importada
        # Los parámetros de host, port y debug se definen aquí.
        app.run(host='0.0.0.0', port=5001, debug=True)
    except Exception as e:
        logger.critical(f"Error fatal al intentar ejecutar el servidor Flask: {e}", exc_info=True)
        sys.exit(1) 