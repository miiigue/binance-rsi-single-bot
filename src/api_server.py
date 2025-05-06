#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import configparser
from flask import Flask, jsonify, request
from flask_cors import CORS

# --- Quitar Workaround sys.path --- 
# current_dir = os.path.dirname(os.path.abspath(__file__))
# project_root = os.path.dirname(current_dir) 
# if project_root not in sys.path:
#     sys.path.insert(0, project_root)

# Importar funciones y variables usando importaciones ABSOLUTAS (desde src)
from src.config_loader import load_config, CONFIG_FILE_PATH
from src.logger_setup import setup_logging, get_logger
from run_bot import worker_statuses, status_lock # <-- Importar solo lo necesario de run_bot
import threading # Necesario para el Lock

# --- Configuración Inicial ---
# Es importante configurar el logging primero
# Pasar el nombre de archivo deseado para este proceso API
api_logger = setup_logging(log_filename='api.log') # <--- Especificar nombre de archivo

app = Flask(__name__) # Crear la aplicación Flask
# Habilitar CORS para permitir peticiones desde el frontend (que corre en otro puerto)
CORS(app) 

def config_to_dict(config: configparser.ConfigParser) -> dict:
    """Convierte un objeto ConfigParser a un diccionario anidado."""
    the_dict = {}
    for section in config.sections():
        the_dict[section] = {}
        for key, val in config.items(section):
            # Intentar convertir tipos
            try:
                if section == 'SYMBOLS' and key == 'symbols_to_trade': # Mantener la lista como string
                    processed_val = val
                elif val.lower() in ['true', 'false']:
                    processed_val = config.getboolean(section, key)
                elif '.' in val:
                    processed_val = config.getfloat(section, key)
                else:
                    processed_val = config.getint(section, key)
            except ValueError:
                processed_val = val # Mantener como string si no
            the_dict[section][key] = processed_val
    return the_dict

def map_frontend_trading_binance(frontend_data: dict) -> dict:
    """ Mapea claves de [TRADING] y [BINANCE] (y ahora volumen) """
    mapping = {
        # BINANCE
        'apiKey': ('BINANCE', 'api_key'), 
        'apiSecret': ('BINANCE', 'api_secret'),
        'mode': ('BINANCE', 'mode'),
        # TRADING
        'rsiInterval': ('TRADING', 'rsi_interval'),
        'rsiPeriod': ('TRADING', 'rsi_period'),
        'rsiThresholdUp': ('TRADING', 'rsi_threshold_up'),
        'rsiThresholdDown': ('TRADING', 'rsi_threshold_down'),
        'rsiEntryLevelLow': ('TRADING', 'rsi_entry_level_low'),
        'positionSizeUSDT': ('TRADING', 'position_size_usdt'),
        'stopLossUSDT': ('TRADING', 'stop_loss_usdt'),
        'takeProfitUSDT': ('TRADING', 'take_profit_usdt'),
        'cycleSleepSeconds': ('TRADING', 'cycle_sleep_seconds'),
        # --- Añadir mapeo de volumen --- 
        'volumeSmaPeriod': ('TRADING', 'volume_sma_period'),
        'volumeFactor': ('TRADING', 'volume_factor'),
        # --- Añadir mapeo para timeout --- 
        'orderTimeoutSeconds': ('TRADING', 'order_timeout_seconds'),
        # --------------------------------
    }
    ini_data = {}
    for frontend_key, value in frontend_data.items():
        if frontend_key in mapping:
            section, ini_key = mapping[frontend_key]
            if section not in ini_data:
                ini_data[section] = {}
            processed_value = str(value).lower() if isinstance(value, bool) else str(value)
            ini_data[section][ini_key] = processed_value
    return ini_data

# --- Endpoints de la API ---

@app.route('/api/config', methods=['GET'])
def get_config_endpoint():
    """Endpoint para obtener la configuración actual, incluyendo símbolos."""
    logger = get_logger()
    logger.info("Recibida petición GET /api/config")
    config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(';', '#'))
    try:
        if not os.path.exists(CONFIG_FILE_PATH):
            logger.error(f"Archivo de configuración no encontrado en {CONFIG_FILE_PATH}")
            # Devolver estructura vacía o valores por defecto si el archivo no existe
            return jsonify({
                "BINANCE": {},
                "TRADING": {},
                "SYMBOLS": {"symbols_to_trade": ""} # Asegurar que SYMBOLS existe
            })
        
        config.read(CONFIG_FILE_PATH, encoding='utf-8')
        config_dict = config_to_dict(config)
        
        # Asegurarse de que la sección SYMBOLS y la clave existen en la respuesta
        if 'SYMBOLS' not in config_dict:
            config_dict['SYMBOLS'] = {'symbols_to_trade': ''}
        elif 'symbols_to_trade' not in config_dict['SYMBOLS']:
            config_dict['SYMBOLS']['symbols_to_trade'] = ''
            
        logger.info("Configuración (incluyendo símbolos) enviada al frontend.")
        return jsonify(config_dict)

    except Exception as e:
        logger.error(f"Error al leer la configuración: {e}", exc_info=True)
        return jsonify({"error": "Failed to read configuration"}), 500

@app.route('/api/config', methods=['POST'])
def update_config_endpoint():
    """Endpoint para recibir y guardar la configuración, incluyendo símbolos."""
    logger = get_logger()
    logger.info("Recibida petición POST /api/config")
    
    if not request.is_json:
        logger.error("Petición POST no contenía JSON.")
        return jsonify({"error": "Request must be JSON"}), 400

    frontend_data = request.get_json()
    if not frontend_data:
        logger.error("JSON recibido estaba vacío.")
        return jsonify({"error": "No data received"}), 400

    logger.debug(f"Datos recibidos del frontend: {frontend_data}")

    # 1. Extraer la lista de símbolos del frontend_data
    symbols_string_raw = frontend_data.get('symbolsToTrade', '') # Usar la clave del estado de React
    # Limpiar y validar la lista de símbolos
    symbols_list = [s.strip().upper() for s in symbols_string_raw.split(',') if s.strip()]
    symbols_to_save = ",".join(symbols_list) # Guardar como string separado por comas
    logger.debug(f"Símbolos procesados para guardar: {symbols_to_save}")

    # 2. Mapear los otros parámetros (BINANCE, TRADING)
    ini_other_data = map_frontend_trading_binance(frontend_data)

    config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(';', '#'))
    try:
        # Leer el archivo existente para mantener secciones no modificadas (ej: LOGGING)
        if os.path.exists(CONFIG_FILE_PATH):
             config.read(CONFIG_FILE_PATH, encoding='utf-8')
        else:
             logger.warning(f"El archivo {CONFIG_FILE_PATH} no existía, se creará uno nuevo.")

        # 3. Actualizar el objeto config con los datos mapeados (BINANCE, TRADING)
        for section, keys in ini_other_data.items():
            if not config.has_section(section):
                config.add_section(section)
            for key, value in keys.items():
                config.set(section, key, str(value))
                logger.debug(f"Actualizando [{section}] {key} = {str(value)}")
                
        # 4. Actualizar/Crear la sección [SYMBOLS]
        if not config.has_section('SYMBOLS'):
            config.add_section('SYMBOLS')
        config.set('SYMBOLS', 'symbols_to_trade', symbols_to_save)
        logger.debug(f"Actualizando [SYMBOLS] symbols_to_trade = {symbols_to_save}")

        # 5. Escribir los cambios de vuelta al archivo config.ini
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as configfile:
            config.write(configfile)
        
        logger.info(f"Archivo de configuración {CONFIG_FILE_PATH} actualizado exitosamente.")
        return jsonify({"message": "Configuration updated successfully"}), 200

    except Exception as e:
        logger.error(f"Error al escribir la configuración: {e}", exc_info=True)
        return jsonify({"error": "Failed to write configuration"}), 500

# --- NUEVO Endpoint para Estado de Workers ---
@app.route('/api/status', methods=['GET'])
def get_worker_status():
    """
    Devuelve el estado actual de todos los workers activos.
    Lee del diccionario compartido 'worker_statuses'.
    """
    logger = get_logger()
    logger.debug("API call received for /api/status")
    
    # Acceder al diccionario compartido de forma segura usando el lock
    with status_lock:
        # Crear una copia para evitar problemas si se modifica mientras se envía
        current_statuses = dict(worker_statuses) 
    
    logger.debug(f"Returning statuses for {len(current_statuses)} workers.")
    return jsonify(current_statuses)
# -------------------------------------------

# --- Bloque para ejecutar directamente (si se llama con python -m src.api_server) ---
if __name__ == '__main__':
    # El logger ya se configuró al inicio del script cuando se importó
    # No necesitamos volver a llamar a setup_logging aquí.
    # Simplemente obtenemos la instancia ya configurada (o verificamos que exista)
    logger_main = get_logger() 
    if not logger_main:
         # Esto no debería pasar si setup_logging al inicio funcionó
         print("ERROR CRÍTICO: Logger no disponible al intentar iniciar desde __main__.", file=sys.stderr)
         sys.exit(1)
    
    logger_main.info("Iniciando servidor API Flask (ejecutado como módulo)...")
    try:
        # Usar los mismos parámetros que teníamos en run_api.py
        app.run(host='0.0.0.0', port=5001, debug=True)
    except Exception as e:
        logger_main.critical(f"Error fatal al intentar ejecutar el servidor Flask: {e}", exc_info=True)
        sys.exit(1) 