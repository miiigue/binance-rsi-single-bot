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
    from src.config_loader import load_config, get_trading_symbols, CONFIG_FILE_PATH
    from src.logger_setup import setup_logging, get_logger
    # La clase se llama TradingBot, su __init__ no toma args,
    # y tiene un método run_once() síncrono.
    from src.bot import TradingBot, BotState
    # --- Importar función de inicialización de DB --- 
    from src.database import init_db_schema
    # ----------------------------------------------
except ImportError as e:
    import traceback
    traceback.print_exc()
    print(f"\nError: No se pudieron importar los módulos necesarios desde 'src'.", file=sys.stderr)
    print(f"Detalle: {e}", file=sys.stderr)
    print("Asegúrate de que estás ejecutando este script desde el directorio raíz del proyecto", file=sys.stderr)
    print("y que el directorio 'src' y sus archivos existen y son correctos.", file=sys.stderr)
    sys.exit(1)

# Variable global para indicar a los hilos que deben detenerse
stop_event = threading.Event()
threads = [] # Lista para guardar los hilos
# --- Diccionario y Lock para Estados de Workers ---
worker_statuses = {} # Ej: {'BTCUSDT': {'state': 'IN_POSITION', 'pnl': 5.2}, 'ETHUSDT': ...}
status_lock = threading.Lock() # Para proteger el acceso a worker_statuses
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

def run_bot_worker(symbol, trading_params, stop_event):
    """Función ejecutada por cada hilo para manejar un bot de símbolo único."""
    logger = get_logger()
    
    # --- Crear la instancia del Bot UNA SOLA VEZ ---
    bot_instance = None
    try:
        bot_instance = TradingBot(symbol=symbol, trading_params=trading_params)
        # --- Inicializar estado en el diccionario compartido ---
        with status_lock:
             worker_statuses[symbol] = bot_instance.get_current_status() 
             # Inicialmente podría ser INITIALIZING o ya IN_POSITION si detectó algo
        # ---------------------------------------------------
        logger.info(f"[{symbol}] Worker thread iniciado. Instancia de TradingBot creada. Tiempo de espera: {get_sleep_seconds(trading_params)}s")
    except (ValueError, ConnectionError) as init_error:
         logger.error(f"No se pudo inicializar la instancia de TradingBot para {symbol}: {init_error}. Terminando worker.", exc_info=True)
         # --- Actualizar estado a ERROR en el diccionario compartido ---
         with status_lock:
              worker_statuses[symbol] = {
                  'symbol': symbol,
                  'state': BotState.ERROR.value, # Usar el valor del Enum
                  'last_error': str(init_error),
                  # Añadir otros campos con None o valores por defecto
                  'in_position': False,
                  'entry_price': None,
                  'quantity': None,
                  'pnl': None,
                  'pending_entry_order_id': None,
                  'pending_exit_order_id': None
              }
         # ----------------------------------------------------------
         return # Salir de la función si falla la inicialización
    except Exception as thread_error:
         logger.error(f"Error inesperado al crear instancia de TradingBot para {symbol}: {thread_error}. Terminando worker.", exc_info=True)
         # --- Actualizar estado a ERROR en el diccionario compartido ---
         with status_lock:
              worker_statuses[symbol] = {
                  'symbol': symbol,
                  'state': BotState.ERROR.value, 
                  'last_error': f"Unexpected init error: {thread_error}",
                  'in_position': False, 'entry_price': None, 'quantity': None, 'pnl': None,
                  'pending_entry_order_id': None, 'pending_exit_order_id': None
              }
         # ----------------------------------------------------------
         return

    # Calcular tiempo de espera
    sleep_duration = get_sleep_seconds(trading_params)

    while not stop_event.is_set():
        try:
            # --- Ejecutar un ciclo del bot ---
            if bot_instance: # Asegurarse que la instancia fue creada
                bot_instance.run_once()

            # --- Actualizar estado en diccionario compartido --- 
            if bot_instance:
                with status_lock:
                     worker_statuses[symbol] = bot_instance.get_current_status()
            # --------------------------------------------------

        except Exception as cycle_error:
            logger.error(f"[{symbol}] Error inesperado en el ciclo principal del worker: {cycle_error}", exc_info=True)
            # --- Actualizar estado a ERROR en diccionario compartido --- 
            if bot_instance:
                # Intentar poner el estado de error del bot si es posible
                bot_instance._set_error_state(f"Unhandled exception in worker loop: {cycle_error}")
                with status_lock:
                     worker_statuses[symbol] = bot_instance.get_current_status()
            else: # Si el error ocurrió antes o sin instancia
                 with status_lock:
                      worker_statuses[symbol] = {
                          'symbol': symbol,
                          'state': BotState.ERROR.value, 
                          'last_error': f"Unhandled exception in worker loop: {cycle_error}",
                          'in_position': False, 'entry_price': None, 'quantity': None, 'pnl': None,
                          'pending_entry_order_id': None, 'pending_exit_order_id': None
                      }
            # ---------------------------------------------------------
            # ¿Deberíamos detener el worker aquí o intentar continuar?
            # Por ahora, continuaremos después de un error, el bot intentará recuperarse.
            # Podríamos añadir un contador de errores para detener si falla repetidamente.
            pass 

        # Esperar antes del próximo ciclo, pero permitir interrupción
        # logger.debug(f"[{symbol}] Ciclo completado. Esperando {sleep_duration}s...")
        interrupted = stop_event.wait(timeout=sleep_duration) # Espera N seg O hasta que set() sea llamado
        if interrupted:
            logger.info(f"[{symbol}] Señal de parada recibida durante la espera.")
            break # Salir del bucle while

    logger.info(f"[{symbol}] Worker thread terminado.")
    # --- Opcional: Actualizar estado final a algo como 'STOPPED'? ---
    # with status_lock:
    #      if symbol in worker_statuses:
    #           worker_statuses[symbol]['state'] = 'Stopped' 
    # ---------------------------------------------------------------

def signal_handler(sig, frame):
    """Manejador para señales como SIGINT (Ctrl+C) y SIGTERM."""
    logger = get_logger()
    logger.warning(f"Señal {signal.Signals(sig).name} recibida. Iniciando apagado ordenado...")
    stop_event.set() # Indicar a todos los hilos que se detengan

def main():
    """Función principal: configura, crea hilos para cada símbolo y los gestiona."""
    logger = None # Definir logger fuera del try para usarlo en finally
    try:
        # 1. Configurar logging (como antes)
        logger = setup_logging(log_filename='bot.log')
        logger.info("="*40)
        logger.info("Iniciando el Multi-Symbol Binance RSI Trading Bot...")
        logger.info("="*40)

        # Registrar manejadores de señal para apagado limpio
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        logger.info("Manejadores de señal registrados (Ctrl+C para detener).")

        # 2. Cargar configuración general
        logger.info(f"Cargando configuración desde: {CONFIG_FILE_PATH}")
        config = load_config()
        if not config:
            logger.error("No se pudo cargar la configuración. Terminando.")
            return
            
        # 3. Inicializar el esquema de la base de datos
        logger.info("Inicializando/Verificando esquema de la base de datos SQLite...")
        if not init_db_schema():
            logger.critical("Fallo al inicializar el esquema de la DB. Abortando.")
            return
        logger.info("Esquema de la base de datos OK.")

        # 4. Obtener la lista de símbolos
        symbols_to_trade = get_trading_symbols()
        if not symbols_to_trade:
            logger.error("No se especificaron símbolos para operar en [SYMBOLS]/symbols_to_trade. Terminando.")
            return
        logger.info(f"Símbolos a operar: {', '.join(symbols_to_trade)}")

        # 5. Extraer parámetros de trading compartidos
        if 'TRADING' not in config:
             logger.error("Sección [TRADING] no encontrada en config.ini. Terminando.")
             return
        # Convertir la sección a un diccionario
        trading_params = dict(config['TRADING'])
        logger.info(f"Parámetros de trading compartidos: {trading_params}")
        
        # 6. Calcular tiempo de espera (usando los parámetros extraídos)
        sleep_seconds = get_sleep_seconds(trading_params)
        logger.info(f"Tiempo de espera base entre ciclos para cada worker: {sleep_seconds} segundos.")

        # 7. Crear e iniciar un hilo para cada símbolo
        logger.info("Creando e iniciando workers para cada símbolo...")
        for symbol in symbols_to_trade:
            logger.info(f"-> Preparando worker para {symbol}...")
            # La creación de la instancia de TradingBot ahora está DENTRO de run_bot_worker
            # Solo creamos e iniciamos el hilo aquí.
            thread = threading.Thread(target=run_bot_worker, args=(symbol, trading_params, stop_event), name=f"Worker-{symbol}")
            thread.daemon = True # Marcar como daemon para que no impidan salir si el principal termina
            threads.append(thread) # Guardar el hilo en la lista
            thread.start() # Iniciar el hilo
            # No hay log de "iniciado" aquí, se hace dentro del worker si tiene éxito
            time.sleep(0.5) # Pequeña pausa para evitar rate limits al iniciar muchos workers

        # 8. Mantener el hilo principal vivo y esperar la señal de parada
        logger.info(f"Todos los workers iniciados ({len(threads)} activos). El proceso principal esperará la señal de parada.")
        while not stop_event.is_set():
            # Esperar indefinidamente hasta que stop_event se active por la señal
            # Usamos un timeout largo para poder comprobar periódicamente
            stopped = stop_event.wait(timeout=60) 
            if stopped:
                logger.info("Proceso principal detectó señal de parada.")
                break
            # Opcional: Podríamos añadir aquí lógica para verificar salud de los hilos, etc.
            # logger.debug(f"[{time.strftime('%H:%M:%S')}] Proceso principal esperando...")

    except KeyboardInterrupt:
        # Esto no debería ocurrir si el signal handler funciona, pero por si acaso
        if logger: logger.warning("KeyboardInterrupt en el hilo principal (inesperado). Iniciando apagado...")
        stop_event.set()
    except Exception as e:
        if logger:
            logger.critical(f"Error fatal inesperado en el proceso principal: {e}", exc_info=True)
        else:
            print(f"Error fatal inesperado: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
        stop_event.set() # Intentar detener hilos si hay error fatal
        
    finally:
        # --- Limpieza Final --- 
        if logger:
             logger.warning("Iniciando secuencia de apagado. Esperando a que terminen los workers...")
        else:
             print("Iniciando secuencia de apagado...")
             
        # Esperar a que todos los hilos terminen (con un timeout)
        for thread in threads:
            if thread.is_alive():
                logger.info(f"Esperando al worker {thread.name}...")
                thread.join(timeout=10) # Dar 10 segundos para terminar limpiamente
                if thread.is_alive():
                     logger.warning(f"¡El worker {thread.name} no terminó a tiempo!")
            else:
                logger.info(f"Worker {thread.name} ya había terminado.")

        # Cerrar pool de DB (si se inicializó globalmente)
        # from src.database import close_db_pool
        # logger.info("Cerrando pool de DB...")
        # close_db_pool()
        # logger.info("Pool de DB cerrado.")

        if logger:
            logger.info("="*40)
            logger.info("El Bot Multi-Símbolo ha terminado.")
            logger.info("="*40)
        else:
            print("El Bot Multi-Símbolo ha terminado.")

if __name__ == "__main__":
    # La inicialización de DB/Schema debería hacerse aquí antes de crear los bots
    # import src.database
    # if not src.database.init_db_pool() or not src.database.init_db_schema():
    #     print("CRITICAL: Falla al inicializar la base de datos. Saliendo.", file=sys.stderr)
    #     sys.exit(1)
        
    main() # Llamar a la función principal refactorizada 