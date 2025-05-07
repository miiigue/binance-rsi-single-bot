#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import configparser
from flask import Flask, jsonify, request
from flask_cors import CORS
import threading
import time # Necesario para sleep
import logging # Necesario para get_logger y calculate_sleep

# --- Quitar Workaround sys.path --- 
# current_dir = os.path.dirname(os.path.abspath(__file__))
# project_root = os.path.dirname(current_dir) 
# if project_root not in sys.path:
#     sys.path.insert(0, project_root)

# Importar funciones y variables usando importaciones ABSOLUTAS (desde src)
from src.config_loader import load_config, get_trading_symbols, CONFIG_FILE_PATH
from src.logger_setup import setup_logging, get_logger
from src.database import get_cumulative_pnl_by_symbol
# Importar TradingBot y BotState para run_bot_worker
from src.bot import TradingBot, BotState 

# --- Definición de variables compartidas para la gestión de workers ---
worker_statuses = {} # Ej: {'BTCUSDT': {'state': 'IN_POSITION', 'pnl': 5.2}, 'ETHUSDT': ...}
status_lock = threading.Lock() 
stop_event = threading.Event() # Evento global para detener todos los hilos
threads = [] # Lista para guardar las instancias de los hilos de los workers
workers_started = False # Flag para saber si los workers están activos
# Variables para almacenar la configuración cargada al inicio
loaded_trading_params = {}
loaded_symbols_to_trade = []
# --------------------------------------------------------------------

# --- Funciones para calcular sleep (Movidas desde run_bot.py) ---
def calculate_sleep_from_interval(interval_str: str) -> int:
    """Calcula segundos de espera basados en el string del intervalo (e.g., '1m', '5m', '1h'). Mínimo 5s."""
    # Ajustado mínimo a 5 segundos como estaba en run_bot antes
    logger = get_logger()
    unit = interval_str[-1].lower()
    try:
        value = int(interval_str[:-1])
        if unit == 'm':
            # Esperar la duración del intervalo, pero mínimo 5 segundos
            return max(60 * value, 5) 
        elif unit == 'h':
            return max(3600 * value, 5)
        else:
            logger.warning(f"Unidad de intervalo no reconocida '{unit}' en '{interval_str}'. Usando 60s por defecto.")
            return 60 # Mantener default de 60 si es inválido
    except (ValueError, IndexError):
        logger.warning(f"Formato de intervalo inválido '{interval_str}'. Usando 60s por defecto.")
        return 60

def get_sleep_seconds(trading_params: dict) -> int:
    """Obtiene el tiempo de espera en segundos desde los parámetros o lo calcula."""
    logger = get_logger()
    try:
        sleep_override = trading_params.get('cycle_sleep_seconds') 
        if sleep_override is not None:
            try:
                sleep_override = int(sleep_override)
            except (ValueError, TypeError):
                 logger.warning(f"Valor no numérico para cycle_sleep_seconds ({sleep_override}). Calculando desde RSI_INTERVAL.")
                 sleep_override = None
        
        if sleep_override is not None and sleep_override > 0:
            # Usar mínimo 5 segundos incluso si se configura menos explícitamente
            final_sleep = max(sleep_override, 5)
            logger.info(f"Usando tiempo de espera explícito: {final_sleep} segundos (desde cycle_sleep_seconds, min 5s).")
            return final_sleep
        else:
            if sleep_override is not None:
                 logger.warning(f"CYCLE_SLEEP_SECONDS ({sleep_override}) inválido. Calculando desde RSI_INTERVAL.")
            rsi_interval = str(trading_params.get('rsi_interval', '5m'))
            calculated_sleep = calculate_sleep_from_interval(rsi_interval)
            logger.info(f"Calculando tiempo de espera desde RSI_INTERVAL ({rsi_interval}): {calculated_sleep} segundos.")
            return calculated_sleep
    except Exception as e:
        logger.error(f"Error inesperado al obtener tiempo de espera: {e}. Usando 60s por defecto.", exc_info=True)
        return 60
# --- Fin Funciones sleep ---

# --- Configuración Inicial ---
api_logger = setup_logging(log_filename='api.log')

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

# --- Función run_bot_worker (Movida desde run_bot.py) ---
# Adaptada para usar las variables globales definidas aquí
def run_bot_worker(symbol, trading_params, stop_event_ref):
    """Función ejecutada por cada hilo para manejar un bot de símbolo único."""
    logger = get_logger()
    
    bot_instance = None
    try:
        # Asegurarse de que trading_params no esté vacío
        if not trading_params:
             logger.error(f"[{symbol}] No se proporcionaron parámetros de trading válidos al worker. Terminando.")
             # Actualizar estado a Error
             with status_lock:
                  worker_statuses[symbol] = {
                      'symbol': symbol, 'state': BotState.ERROR.value, 'last_error': "Missing trading parameters.",
                      'in_position': False, 'entry_price': None, 'quantity': None, 'pnl': None,
                      'pending_entry_order_id': None, 'pending_exit_order_id': None
                  }
             return
             
        # Obtener sleep_duration aquí usando la función movida
        sleep_duration = get_sleep_seconds(trading_params)
        
        bot_instance = TradingBot(symbol=symbol, trading_params=trading_params)
        with status_lock:
             worker_statuses[symbol] = bot_instance.get_current_status() 
        logger.info(f"[{symbol}] Worker thread iniciado. Instancia de TradingBot creada. Tiempo de espera: {sleep_duration}s") # Usar sleep_duration
    except (ValueError, ConnectionError) as init_error:
         logger.error(f"No se pudo inicializar la instancia de TradingBot para {symbol}: {init_error}. Terminando worker.", exc_info=True)
         with status_lock:
              worker_statuses[symbol] = {
                  'symbol': symbol, 'state': BotState.ERROR.value, 'last_error': str(init_error),
                  'in_position': False, 'entry_price': None, 'quantity': None, 'pnl': None,
                  'pending_entry_order_id': None, 'pending_exit_order_id': None
              }
         return
    except Exception as thread_error:
         logger.error(f"Error inesperado al crear instancia de TradingBot para {symbol}: {thread_error}. Terminando worker.", exc_info=True)
         with status_lock:
              worker_statuses[symbol] = {
                  'symbol': symbol, 'state': BotState.ERROR.value, 
                  'last_error': f"Unexpected init error: {thread_error}",
                  'in_position': False, 'entry_price': None, 'quantity': None, 'pnl': None,
                  'pending_entry_order_id': None, 'pending_exit_order_id': None
              }
         return

    # Ya no necesitamos get_sleep_seconds aquí si lo calculamos antes

    while not stop_event_ref.is_set():
        try:
            if bot_instance:
                bot_instance.run_once()
            if bot_instance:
                with status_lock:
                     worker_statuses[symbol] = bot_instance.get_current_status()
        except Exception as cycle_error:
            logger.error(f"[{symbol}] Error inesperado en el ciclo principal del worker: {cycle_error}", exc_info=True)
            if bot_instance:
                bot_instance._set_error_state(f"Unhandled exception in worker loop: {cycle_error}")
                with status_lock:
                     worker_statuses[symbol] = bot_instance.get_current_status()
            else:
                 # Si bot_instance es None aquí, hubo un error muy temprano
                 with status_lock:
                      if symbol not in worker_statuses or not isinstance(worker_statuses.get(symbol), dict):
                           worker_statuses[symbol] = {} # Asegurar que existe como dict
                           
                      worker_statuses[symbol].update({
                          'symbol': symbol, 'state': BotState.ERROR.value, 
                          'last_error': f"Critical worker loop error before bot ready: {cycle_error}",
                          'in_position': False, 'entry_price': None, 'quantity': None, 'pnl': None,
                          'pending_entry_order_id': None, 'pending_exit_order_id': None
                      })
            # Continuar el bucle para permitir posible recuperación o apagado
            pass 

        # Usar el sleep_duration calculado
        interrupted = stop_event_ref.wait(timeout=sleep_duration)
        if interrupted:
            logger.info(f"[{symbol}] Señal de parada recibida durante la espera.")
            break

    logger.info(f"[{symbol}] Worker thread terminado.")
    # Actualizar estado final al detenerse
    with status_lock:
         # Asegurarse que la entrada existe y es un diccionario
         if symbol not in worker_statuses or not isinstance(worker_statuses.get(symbol), dict):
             worker_statuses[symbol] = {'symbol': symbol} # Crear entrada mínima
         worker_statuses[symbol]['state'] = BotState.STOPPED.value
# --- Fin de run_bot_worker ---


# --- Función para iniciar los workers (Movida y Adaptada) ---
def start_bot_workers():
    global workers_started, threads, loaded_trading_params, loaded_symbols_to_trade
    logger = get_logger()
    
    with status_lock: # Proteger acceso a workers_started y threads
        if workers_started:
            logger.warning("start_bot_workers fue llamado pero los workers ya están iniciados.")
            return False # Indicar que no se hizo nada

        if not loaded_symbols_to_trade:
            logger.error("No hay símbolos configurados para iniciar los workers.")
            return False
            
        if not loaded_trading_params:
            logger.error("No hay parámetros de trading configurados para iniciar los workers.")
            return False

        logger.info("Iniciando workers de bot...")
        # Limpiar lista de hilos anterior por si acaso (aunque no debería haber)
        threads.clear() 
        stop_event.clear() # Asegurarse que el evento de parada no esté activo

        for symbol_idx, symbol in enumerate(loaded_symbols_to_trade):
            logger.info(f"-> Preparando worker para {symbol}...")
            # Usar loaded_trading_params
            thread = threading.Thread(target=run_bot_worker, args=(symbol, loaded_trading_params, stop_event), name=f"Worker-{symbol}")
            threads.append(thread)
            thread.start()
            if (symbol_idx + 1) < len(loaded_symbols_to_trade):
                 # Espera corta entre inicios de hilos para evitar sobrecarga inicial
                 time.sleep(1) 
        
        num_bot_threads = len(threads)
        workers_started = True # Marcar como iniciados
        logger.info(f"Todos los {num_bot_threads} workers de bot iniciados.")
        return True # Indicar éxito
# --- Fin de start_bot_workers ---


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

@app.route('/api/status', methods=['GET'])
def get_worker_status():
    global workers_started # Necesitamos acceso al flag global
    logger = get_logger()
    logger.debug("API call received for /api/status")
    
    all_symbols_status = []
    # Usar los símbolos cargados al inicio
    configured_symbols = loaded_symbols_to_trade 
    historical_pnl_data = get_cumulative_pnl_by_symbol()

    logger.debug(f"Símbolos configurados (cargados al inicio): {configured_symbols}")
    logger.debug(f"PnL histórico de DB: {historical_pnl_data}")

    with status_lock: 
        active_worker_details = dict(worker_statuses)

    for symbol in configured_symbols:
        status_entry = {
            'symbol': symbol,
            'state': BotState.STOPPED.value if not workers_started else 'Initializing', # Estado inicial antes de que el worker actualice
            'in_position': False,
            'entry_price': None,
            'quantity': None,
            'pnl': None,
            'pending_entry_order_id': None,
            'pending_exit_order_id': None,
            'last_error': None,
            'cumulative_pnl': historical_pnl_data.get(symbol, 0.0)
        }

        if symbol in active_worker_details and workers_started:
            active_status = active_worker_details[symbol]
            # Sobrescribir solo si el estado del worker no es STOPPED (o si es la primera vez)
            if active_status.get('state') != BotState.STOPPED.value:
                 status_entry.update(active_status)
                 status_entry['symbol'] = symbol # Asegurar que el símbolo es el correcto
                 status_entry['cumulative_pnl'] = historical_pnl_data.get(symbol, 0.0) # Mantener PnL histórico
            # Si el worker individual reporta STOPPED, mantenerlo.
            elif active_status.get('state') == BotState.STOPPED.value:
                 status_entry['state'] = BotState.STOPPED.value

        all_symbols_status.append(status_entry)
    
    # Añadir estado global de los workers
    response_data = {
        "bots_running": workers_started,
        "statuses": all_symbols_status
    }
    
    logger.debug(f"Returning combined statuses. Bots running: {workers_started}")
    # return jsonify(all_symbols_status) # Devolver el nuevo formato
    return jsonify(response_data)

@app.route('/api/shutdown', methods=['POST'])
def shutdown_bot():
    global workers_started, threads
    api_logger.warning("Solicitud de apagado recibida a través de la API.")
    
    if not workers_started:
         api_logger.warning("Señal de apagado recibida, pero los workers no estaban iniciados.")
         return jsonify({"message": "Workers no estaban corriendo."}), 200 # O un 4xx?

    stop_event.set() 
    api_logger.info("Esperando que los hilos de los workers terminen (join)...")
    
    # Esperar un tiempo razonable para que los hilos terminen
    join_timeout = 10 # segundos
    start_join_time = time.time()
    active_threads = []
    for t in threads:
        t.join(timeout=max(0.1, join_timeout - (time.time() - start_join_time)))
        if t.is_alive():
            active_threads.append(t.name)
            
    if active_threads:
         api_logger.warning(f"Los siguientes hilos no terminaron después de {join_timeout}s: {active_threads}")
    else:
         api_logger.info("Todos los hilos de workers han terminado.")

    workers_started = False # Marcar como detenidos
    threads.clear() # Limpiar la lista de hilos
    # Limpiar estados individuales (opcional, podrían quedarse en STOPPED)
    # with status_lock:
    #     worker_statuses.clear()

    return jsonify({"message": "Señal de apagado enviada y workers detenidos."}), 200

# --- NUEVO ENDPOINT PARA INICIAR LOS BOTS ---
@app.route('/api/start_bots', methods=['POST'])
def start_bots_endpoint():
    global workers_started
    logger = get_logger()
    logger.info("Recibida petición POST /api/start_bots")
    
    if workers_started:
        logger.warning("Intento de iniciar workers cuando ya estaban corriendo.")
        return jsonify({"error": "Bots ya están corriendo."}), 409 # 409 Conflict

    # Llamar a la función que realmente inicia los hilos
    success = start_bot_workers() 

    if success:
        return jsonify({"message": "Bots iniciados exitosamente."}), 200
    else:
        logger.error("Fallo al iniciar los workers (ver logs anteriores).")
        # Revisar si workers_started se quedó en False debido al fallo
        if not workers_started:
             return jsonify({"error": "Fallo al iniciar los bots (verificar configuración o logs)."}), 500 # Internal Server Error
        else:
             # Caso raro: la función falló pero el flag cambió? Devolver error igualmente.
              return jsonify({"error": "Estado inconsistente al iniciar los bots."}), 500
# ------------------------------------------

# Función para cargar configuración inicial (llamada desde run_bot.py)
def load_initial_config():
    global loaded_trading_params, loaded_symbols_to_trade
    logger = get_logger()
    logger.info("Cargando configuración inicial para API y Workers...")
    config = load_config()
    if not config:
        logger.error("No se pudo cargar la configuración global.")
        return False
        
    loaded_symbols_to_trade = get_trading_symbols() # No necesita argumento
    if not loaded_symbols_to_trade:
        logger.error("No se especificaron símbolos para operar.")
        # Considerar si esto es un error fatal o no
        
    if 'TRADING' not in config:
         logger.error("Sección [TRADING] no encontrada en config.ini.")
         return False
         
    loaded_trading_params = dict(config['TRADING'])
    logger.info(f"Configuración inicial cargada: {len(loaded_symbols_to_trade)} símbolos, Params: {loaded_trading_params}")
    return True

# La función para correr Flask en un hilo (start_flask_app) 
# y el if __name__ == '__main__' no se necesitan aquí 
# si api_server.py es solo para definir la app y sus rutas,
# y es importado por run_bot.py 