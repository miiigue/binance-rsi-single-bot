# Este será el punto de entrada principal para ejecutar el bot.
# Por ahora, lo dejamos vacío. 

import time
import sys

# Importar la configuración del logger ANTES que otros módulos nuestros
# para asegurar que los logs de inicialización se capturen.
from src.logger_setup import setup_logging, get_logger

# Ahora importar el resto de componentes necesarios
from src.bot import TradingBot
from src.config_loader import load_config # Para leer el intervalo de sleep
from src.database import db_pool # Para cerrar el pool al final

# --- Configuración Inicial --- 
# Es crucial configurar el logging lo primero.
scheduler_logger = setup_logging()

# Tiempo de espera entre ciclos del bot (en segundos)
# Podríamos leerlo de config.ini si quisiéramos hacerlo configurable
# Por ahora, lo ponemos fijo. Debería ser al menos unos segundos para no saturar la API.
# Un valor razonable podría ser cercano al intervalo de las velas, pero 
# para pruebas iniciales, podemos usar un valor más corto.
SLEEP_INTERVAL_SECONDS = 15 

def main():
    """Función principal que inicializa y ejecuta el bot."""
    if not scheduler_logger:
        print("Error CRÍTICO: No se pudo inicializar el logger. Abortando.", file=sys.stderr)
        sys.exit(1) # Salir con código de error

    scheduler_logger.info("=============================================")
    scheduler_logger.info("===       INICIANDO BOT DE TRADING RSI    ===")
    scheduler_logger.info("=============================================")

    try:
        # Crear la instancia del bot
        bot = TradingBot()

    except (ValueError, ConnectionError) as e:
        scheduler_logger.critical(f"Error fatal durante la inicialización del bot: {e}")
        scheduler_logger.critical("El bot no puede continuar. Revisa la configuración y las conexiones.")
        sys.exit(1)
    except Exception as e:
        scheduler_logger.critical(f"Error inesperado y fatal durante la inicialización: {e}", exc_info=True)
        sys.exit(1)

    # --- Bucle Principal del Bot --- 
    scheduler_logger.info(f"Iniciando bucle principal. Intervalo entre ciclos: {SLEEP_INTERVAL_SECONDS} segundos.")
    running = True
    while running:
        try:
            # Ejecutar un ciclo de la lógica del bot
            bot.run_once()

            # Esperar antes del próximo ciclo
            scheduler_logger.debug(f"Esperando {SLEEP_INTERVAL_SECONDS} segundos para el próximo ciclo...")
            time.sleep(SLEEP_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            # Capturar Ctrl+C para una salida ordenada
            scheduler_logger.warning("Señal de interrupción (Ctrl+C) recibida. Deteniendo el bot...")
            running = False # Salir del bucle while

        except Exception as e:
            # Capturar cualquier otro error inesperado durante run_once
            scheduler_logger.error(f"Error inesperado en el bucle principal: {e}", exc_info=True)
            scheduler_logger.error("Intentando continuar después de una breve pausa...")
            # Esperar un poco más después de un error para evitar ciclos rápidos de fallos
            time.sleep(SLEEP_INTERVAL_SECONDS * 2)

    # --- Limpieza Final --- 
    scheduler_logger.info("Cerrando recursos...")
    if db_pool:
        try:
            db_pool.closeall() # Cerrar todas las conexiones en el pool de la DB
            scheduler_logger.info("Pool de conexiones de base de datos cerrado.")
        except Exception as e:
            scheduler_logger.error(f"Error al cerrar el pool de la base de datos: {e}")

    scheduler_logger.info("=============================================")
    scheduler_logger.info("===         BOT DETENIDO LIMPIAMENTE      ===")
    scheduler_logger.info("=============================================")
    sys.exit(0) # Salir indicando éxito

# --- Punto de entrada de ejecución --- 
# Esto asegura que main() solo se llame cuando ejecutamos
# el script directamente (python main.py)
if __name__ == "__main__":
    main() 