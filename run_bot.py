#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Quitar asyncio si no se usa directamente, importar time
import sys
import logging
import time # Para time.sleep()
import threading # <--- Importar threading
import signal # <--- Para manejar señales de terminación
import os

# Añadir el directorio raíz al sys.path para importar desde src
current_dir = os.path.dirname(os.path.abspath(__file__))
# sys.path.append(current_dir) # No es ideal, mejor usar imports relativos o estructura de paquete

# Importar las piezas necesarias desde nuestro paquete 'src'
try:
    from src.config_loader import load_config, CONFIG_FILE_PATH
    from src.logger_setup import setup_logging, get_logger
    # La clase se llama TradingBot, su __init__ no toma args,
    # y tiene un método run_once() síncrono.
    from src.bot import TradingBot, BotState
    # --- Importar función de inicialización de DB --- 
    from src.database import init_db_schema
    # ----------------------------------------------
    from src.api_server import (
        app as flask_api_app, # Importar 'app' y renombrarla si se prefiere, o usar 'app' directamente
        load_initial_config, # Nueva función para cargar config en api_server
        stop_event, 
        threads,
        workers_started
    )
except ImportError as e:
    import traceback
    traceback.print_exc()
    print(f"\nError: No se pudieron importar los módulos necesarios desde 'src'.", file=sys.stderr)
    print(f"Detalle: {e}", file=sys.stderr)
    print("Asegúrate de que estás ejecutando este script desde el directorio raíz del proyecto", file=sys.stderr)
    print("y que el directorio 'src' y sus archivos existen y son correctos.", file=sys.stderr)
    sys.exit(1)

# Variable global para indicar a los hilos que deben detenerse
# threads = [] # Lista para guardar los hilos
# --- Diccionario y Lock para Estados de Workers (AHORA IMPORTADOS DESDE api_server) ---
# worker_statuses = {} # <-- ELIMINAR
# status_lock = threading.Lock() # <-- ELIMINAR
# -------------------------------------------------

def calculate_sleep_from_interval(interval_str: str) -> int:
    """Calcula segundos de espera basados en el string del intervalo (e.g., '1m', '5m', '1h'). Mínimo 60s."""
    unit = interval_str[-1].lower()
    try:
        value = int(interval_str[:-1])
        if unit == 'm':
            # Esperar la duración del intervalo, pero mínimo 60 segundos
            return max(60 * value, 60)
        elif unit == 'h':
            return max(3600 * value, 60)
        else:
            # Default a 1 minuto si la unidad no es reconocida
            logging.warning(f"Unidad de intervalo no reconocida '{unit}' en '{interval_str}'. Usando 60s por defecto.")
            return 60
    except (ValueError, IndexError):
        logging.warning(f"Formato de intervalo inválido '{interval_str}'. Usando 60s por defecto.")
        return 60 # Default a 1 minuto si el formato es inválido

def get_sleep_seconds(trading_params: dict) -> int:
    """Obtiene el tiempo de espera en segundos desde los parámetros o lo calcula."""
    logger = logging.getLogger() # Obtener el logger ya configurado
    try:
        # Leer desde el diccionario de parámetros
        sleep_override = trading_params.get('cycle_sleep_seconds') 
        
        # Convertir a int si es posible
        if sleep_override is not None:
            try:
                sleep_override = int(sleep_override)
            except (ValueError, TypeError):
                 logger.warning(f"Valor no numérico para cycle_sleep_seconds ({sleep_override}). Calculando desde RSI_INTERVAL.")
                 sleep_override = None # Forzar recálculo
        
        if sleep_override is not None and sleep_override > 0:
            logger.info(f"Usando tiempo de espera explícito: {sleep_override} segundos (desde cycle_sleep_seconds).")
            return max(sleep_override, 5)
        else:
            if sleep_override is not None:
                 logger.warning(f"CYCLE_SLEEP_SECONDS ({sleep_override}) inválido. Calculando desde RSI_INTERVAL.")
            # Calcular basado en RSI_INTERVAL
            rsi_interval = str(trading_params.get('rsi_interval', '5m'))
            calculated_sleep = calculate_sleep_from_interval(rsi_interval)
            logger.info(f"Calculando tiempo de espera desde RSI_INTERVAL ({rsi_interval}): {calculated_sleep} segundos.")
            return calculated_sleep

    except Exception as e:
        logger.error(f"Error inesperado al obtener tiempo de espera: {e}. Usando 60s por defecto.", exc_info=True)
        return 60

# --- FUNCIÓN PARA EJECUTAR FLASK EN UN HILO ---
def run_flask_app():
    logger_flask = get_logger()
    logger_flask.info("Iniciando servidor API Flask en un hilo separado...")
    try:
        # Deshabilitar el reloader de Flask y el modo debug cuando se ejecuta integrado
        flask_api_app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False)
        logger_flask.info("Servidor Flask detenido.") # Esto se logueará si .run() termina limpiamente
    except Exception as e:
        logger_flask.critical(f"Error fatal al intentar ejecutar el servidor Flask en su hilo: {e}", exc_info=True)
    finally:
        logger_flask.info("Hilo del servidor Flask finalizando.")
# --------------------------------------------

def run_bot_worker(symbol, trading_params, stop_event_ref):
    """Función ejecutada por cada hilo para manejar un bot de símbolo único."""
    logger = get_logger()
    
    bot_instance = None
    try:
        bot_instance = TradingBot(symbol=symbol, trading_params=trading_params)
        with status_lock:
             worker_statuses[symbol] = bot_instance.get_current_status() 
        logger.info(f"[{symbol}] Worker thread iniciado. Instancia de TradingBot creada. Tiempo de espera: {get_sleep_seconds(trading_params)}s")
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

    sleep_duration = get_sleep_seconds(trading_params)

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
                 with status_lock:
                      worker_statuses[symbol] = {
                          'symbol': symbol, 'state': BotState.ERROR.value, 
                          'last_error': f"Unhandled exception in worker loop: {cycle_error}",
                          'in_position': False, 'entry_price': None, 'quantity': None, 'pnl': None,
                          'pending_entry_order_id': None, 'pending_exit_order_id': None
                      }
            pass 

        interrupted = stop_event_ref.wait(timeout=sleep_duration)
        if interrupted:
            logger.info(f"[{symbol}] Señal de parada recibida durante la espera.")
            break

    logger.info(f"[{symbol}] Worker thread terminado.")
    with status_lock:
         if symbol in worker_statuses and isinstance(worker_statuses[symbol], dict):
              worker_statuses[symbol]['state'] = BotState.STOPPED.value
         else:
              worker_statuses[symbol] = {'symbol': symbol, 'state': BotState.STOPPED.value}

def signal_handler(sig, frame):
    """Manejador para señales como SIGINT (Ctrl+C) y SIGTERM."""
    logger = get_logger()
    logger.warning(f"Señal {signal.Signals(sig).name} recibida. Iniciando apagado ordenado...")
    stop_event.set() # Indicar a todos los hilos que se detengan

def main():
    """Función principal: configura, inicia API, y espera señal de parada."""
    logger = None
    try:
        # 1. Configuración inicial (Logging, Señales)
        logger = setup_logging(log_filename='bot_combined.log')
        logger.info("="*40)
        logger.info("Iniciando el Multi-Symbol Binance RSI Trading Bot & API Server...")
        logger.info("="*40)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        logger.info("Manejadores de señal registrados (Ctrl+C para detener).")

        # 2. Cargar Configuración Inicial (ahora lo hace api_server)
        logger.info("Cargando configuración inicial...")
        if not load_initial_config(): # Llama a la función importada
            logger.critical("Fallo al cargar la configuración inicial desde api_server. Terminando.")
            return
        logger.info("Configuración inicial cargada en el módulo API.")
            
        # 3. Inicializar Base de Datos
        logger.info("Inicializando/Verificando esquema de la base de datos SQLite...")
        if not init_db_schema():
            logger.critical("Fallo al inicializar el esquema de la DB. Abortando.")
            return
        logger.info("Esquema de la base de datos OK.")

        # --- 4. INICIAR HILO DEL SERVIDOR API FLASK ---
        logger.info("Iniciando el servidor API Flask...")
        # Nota: run_flask_app ahora debe ejecutarse desde dentro de api_server o importarse
        # Asumiendo que Flask se ejecuta al iniciar el script si api_server.py se importa
        # O mejor, iniciar explícitamente como antes:
        api_thread = threading.Thread(target=lambda: flask_api_app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False), 
                                      name="FlaskAPIServerThread", daemon=True)
        api_thread.start()
        logger.info("Hilo del servidor API Flask iniciado.")
        # -----------------------------------------

        # --- 5. NO iniciar los workers aquí --- 
        # Los workers ahora se inician bajo demanda desde el endpoint /api/start_bots
        logger.info("Servidor listo. Los workers se iniciarán desde la API (/api/start_bots).")
        
        # --- 6. Esperar señal de parada --- 
        # El hilo principal ahora solo necesita esperar a que se active stop_event
        logger.info("Proceso principal esperando señal de apagado (Ctrl+C o /api/shutdown)...")
        stop_event.wait() # Espera aquí hasta que stop_event.set() sea llamado
        logger.info("Señal de apagado detectada en el hilo principal.")

        # 7. Esperar (brevemente) a que los hilos terminen (Flask y workers si están activos)
        # El endpoint /api/shutdown ya hace join en los workers.
        # Solo necesitamos esperar al hilo de Flask.
        logger.info("Esperando finalización del hilo de la API...")
        # Flask no termina limpiamente solo con daemon=True, necesita una señal externa o una ruta de apagado.
        # El stop_event no detiene Flask directamente. Podríamos añadir una llamada a /api/shutdown aquí si no se hizo?
        # Por ahora, confiamos en que signal_handler o /api/shutdown llamaron a stop_event.set()
        api_thread.join(timeout=5) # Espera brevemente al hilo de Flask
        if api_thread.is_alive():
             logger.warning("El hilo del servidor API Flask no terminó limpiamente.")

    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt recibido en el hilo principal (main).")
        if stop_event and not stop_event.is_set():
             stop_event.set() # Asegurar que el evento se activa
    except Exception as e:
        if logger:
            logger.critical(f"Error crítico en la función main: {e}", exc_info=True)
        else:
            print(f"Error crítico en la función main (logger no disponible): {e}", file=sys.stderr)
            traceback.print_exc()
    finally:
        # --- Secuencia de apagado final --- 
        if logger:
            logger.info("Iniciando secuencia de apagado final en main()...")
            
            # Asegurarse de que el evento de parada esté activo
            if stop_event and not stop_event.is_set():
                logger.info("Activando stop_event durante el apagado final.")
                stop_event.set()
                
            logger.info("Asegurándose de que todos los hilos de bot hayan terminado...")
            # El endpoint /api/shutdown ya hizo join, pero podemos verificar
            active_bot_threads = [t.name for t in threads if t.is_alive()]
            if active_bot_threads:
                 logger.warning(f"Los siguientes hilos de bot seguían activos: {active_bot_threads}")
            else:
                 logger.info("Confirmado: No hay hilos de bot activos.")

            logger.info("="*40)
            logger.info("Bot y API Server apagados.")
            logger.info("="*40)
        else:
            print("\nApagado finalizado (logger no disponible).")

if __name__ == '__main__':
    main() 