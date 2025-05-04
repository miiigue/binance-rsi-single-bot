#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Quitar asyncio si no se usa directamente, importar time
import sys
import logging
import time # Para time.sleep()

# Importar las piezas necesarias desde nuestro paquete 'src'
try:
    from src.config_loader import load_config, CONFIG_FILE_PATH
    from src.logger_setup import setup_logging
    # La clase se llama TradingBot, su __init__ no toma args,
    # y tiene un método run_once() síncrono.
    from src.bot import TradingBot
except ImportError as e:
    import traceback
    traceback.print_exc()
    print(f"\nError: No se pudieron importar los módulos necesarios desde 'src'.", file=sys.stderr)
    print(f"Detalle: {e}", file=sys.stderr)
    print("Asegúrate de que estás ejecutando este script desde el directorio raíz del proyecto", file=sys.stderr)
    print("y que el directorio 'src' y sus archivos existen y son correctos.", file=sys.stderr)
    sys.exit(1)

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

def get_sleep_seconds(config) -> int:
    """Obtiene el tiempo de espera en segundos desde config o lo calcula."""
    logger = logging.getLogger() # Obtener el logger ya configurado
    try:
        # Intentar leer el valor explícito
        sleep_override = config.getint('TRADING', 'CYCLE_SLEEP_SECONDS', fallback=None)

        if sleep_override is not None and sleep_override > 0:
            logger.info(f"Usando tiempo de espera explícito: {sleep_override} segundos (desde CYCLE_SLEEP_SECONDS).")
            # Aplicar un mínimo razonable (ej. 5 segundos) para evitar abuso de API
            return max(sleep_override, 5)
        else:
            if sleep_override is not None:
                 logger.warning(f"CYCLE_SLEEP_SECONDS ({sleep_override}) inválido. Calculando desde RSI_INTERVAL.")
            # Calcular basado en RSI_INTERVAL si no hay override válido
            rsi_interval = config.get('TRADING', 'RSI_INTERVAL', fallback='5m')
            calculated_sleep = calculate_sleep_from_interval(rsi_interval)
            logger.info(f"Calculando tiempo de espera desde RSI_INTERVAL ({rsi_interval}): {calculated_sleep} segundos.")
            return calculated_sleep

    except ValueError:
         logger.warning(f"Valor no numérico para CYCLE_SLEEP_SECONDS. Calculando desde RSI_INTERVAL.")
         rsi_interval = config.get('TRADING', 'RSI_INTERVAL', fallback='5m')
         calculated_sleep = calculate_sleep_from_interval(rsi_interval)
         logger.info(f"Calculando tiempo de espera desde RSI_INTERVAL ({rsi_interval}): {calculated_sleep} segundos.")
         return calculated_sleep
    except Exception as e:
        logger.error(f"Error inesperado al obtener tiempo de espera: {e}. Usando 60s por defecto.", exc_info=True)
        return 60

def main():
    """Función principal síncrona para configurar y ejecutar el bot."""
    logger = None
    try:
        # 1. Configurar el logging
        logger = setup_logging(log_filename='bot.log')
        logger.info("="*40)
        logger.info("Iniciando el Bot de Trading RSI...")
        logger.info("="*40)

        # 2. Cargar la configuración
        logger.info(f"Cargando configuración desde: {CONFIG_FILE_PATH}")
        config = load_config()
        if not config:
            logger.error("No se pudo cargar la configuración. Terminando.")
            return

        # Obtener el tiempo de espera usando la nueva lógica
        sleep_seconds = get_sleep_seconds(config)
        logger.info(f"Tiempo de espera entre ciclos establecido a: {sleep_seconds} segundos.")

        # 3. Crear una instancia del bot
        logger.info("Creando instancia del TradingBot...")
        bot = TradingBot()

        # 4. Ejecutar el bot en un bucle
        logger.info("Iniciando el ciclo principal del bot (usa CTRL+C para detener)...")
        while True:
            start_time = time.monotonic()
            try:
                bot.run_once() # Llamar al método síncrono del bot
                logger.debug(f"Ciclo 'run_once' completado.") # Cambiado a debug para menos ruido
            except Exception as cycle_error:
                # Capturar error dentro del ciclo para intentar continuar
                logger.error(f"Error durante un ciclo del bot: {cycle_error}", exc_info=True)
                # Esperar igualmente antes de reintentar

            end_time = time.monotonic()
            elapsed_time = end_time - start_time
            wait_time = max(0, sleep_seconds - elapsed_time) # Calcular cuánto falta esperar

            if wait_time > 0:
                logger.info(f"Ciclo tomó {elapsed_time:.2f}s. Esperando {wait_time:.2f}s más...")
                time.sleep(wait_time)
            else:
                logger.warning(f"¡El ciclo del bot tomó {elapsed_time:.2f}s, más que el tiempo de espera configurado de {sleep_seconds}s! Iniciando siguiente ciclo inmediatamente.")

    except KeyboardInterrupt:
        if logger:
            logger.warning("Interrupción por teclado recibida. Terminando bot...")
        else:
            print("\nInterrupción por teclado recibida. Terminando...", file=sys.stderr)
    except Exception as e:
        # Capturar cualquier otro error inesperado (ej. en inicialización)
        if logger:
            logger.critical(f"Error fatal inesperado: {e}", exc_info=True)
        else:
            print(f"Error fatal inesperado: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
    finally:
        if logger:
            logger.info("="*40)
            logger.info("El bot ha terminado.")
            logger.info("="*40)
        else:
            print("El bot ha terminado.")

if __name__ == "__main__":
    main() # Llamar directamente a la función síncrona main 