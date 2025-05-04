# Este módulo configurará el sistema de logging.
# Por ahora, lo dejamos vacío.

import logging
import sys
import os # Importar os para crear el directorio si no existe
from logging.handlers import RotatingFileHandler

# Importamos nuestra función para cargar la configuración
# Usamos un punto (.) al principio para indicar que es una importación relativa
# dentro del mismo paquete (la carpeta 'src')
from .config_loader import load_config

# Variable global para el logger, inicializada a None
# Es útil tenerla global para poder acceder al logger desde otras partes si es necesario,
# aunque generalmente se pasa como argumento o se obtiene llamando a setup_logging.
logger = None

def setup_logging(log_filename: str = 'app.log'):
    """
    Configura el sistema de logging basado en los parámetros del archivo config.ini.
    Escribe logs tanto a la consola como a un archivo rotatorio especificado.

    Args:
        log_filename (str): Nombre del archivo de log a usar (e.g., 'bot.log', 'api.log').
                             Default es 'app.log', pero se recomienda especificar uno.

    Returns:
        logging.Logger: La instancia del logger configurado.
                      Retorna None si la configuración no pudo ser cargada o hubo un error.
    """
    global logger

    # Si ya está configurado (por otra llamada), no lo hacemos de nuevo.
    # TODO: Considerar si diferentes llamadas con diferentes filenames deberían crear diferentes loggers
    #       Por ahora, la primera llamada configura el logger raíz 'src'.
    if logger:
        return logger

    config = load_config()
    if not config:
        print("CRITICAL: No se pudo cargar config.ini para inicializar el logging.", file=sys.stderr)
        return None

    try:
        # Leemos los parámetros de logging desde la configuración
        # Quitamos la lectura del nombre de archivo de config.ini, usamos el parámetro
        # log_file_config = config.get('LOGGING', 'LOG_FILE', fallback='binance_rsi_bot.log')
        log_level_str = config.get('LOGGING', 'LOG_LEVEL', fallback='INFO').upper()
        log_level = getattr(logging, log_level_str, logging.INFO)

    except Exception as e:
        print(f"WARNING: Error al leer la configuración de logging de config.ini: {e}. Usando valores por defecto.", file=sys.stderr)
        log_level = logging.INFO
        log_level_str = 'INFO'

    # --- Crear y configurar el logger principal --- 
    local_logger = logging.getLogger(__name__.split('.')[0]) # Usamos 'src' como nombre base
    local_logger.setLevel(log_level)
    local_logger.propagate = False

    if local_logger.hasHandlers():
        local_logger.handlers.clear()

    log_formatter = logging.Formatter(
        '%(asctime)s - %(name)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # --- Handler para Archivo (con rotación) usando el filename del parámetro --- 
    try:
        # Asegurarse de que el directorio del log existe (si se especifica una ruta)
        log_dir = os.path.dirname(log_filename)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
            print(f"INFO: Directorio de log creado: {log_dir}", file=sys.stdout)

        # Usar el log_filename proporcionado
        file_handler = RotatingFileHandler(log_filename, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
        file_handler.setFormatter(log_formatter)
        file_handler.setLevel(log_level)
        local_logger.addHandler(file_handler)
    except Exception as e:
        # Usar f-string para el nombre de archivo en el error
        print(f"CRITICAL: No se pudo crear el handler de archivo de log '{log_filename}': {e}", file=sys.stderr)
        return None

    # --- Handler para Consola --- 
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(log_level)
    local_logger.addHandler(console_handler)

    # --- Asignar a la variable global --- 
    logger = local_logger

    # Usar f-string para el nombre de archivo en el mensaje de inicio
    logger.info(f"***** Logging iniciado. Nivel: {log_level_str}. Archivo: {log_filename} *****")

    return logger

# Función para obtener el logger configurado desde otros módulos
def get_logger():
    """Retorna la instancia del logger configurado."""
    # Advertencia: Esta función devolverá el logger configurado por la PRIMERA llamada a setup_logging.
    # Si se necesita asegurar un logger específico (bot vs api), es mejor llamar a setup_logging
    # directamente con el nombre de archivo correcto en cada punto de entrada (run_bot.py, api_server.py)
    # y guardar esa instancia específica, en lugar de depender de get_logger().
    # Por ahora, mantenemos la lógica simple asumiendo que setup_logging se llama primero.
    if not logger:
        print("WARNING: get_logger() llamado antes de setup_logging(). Intentando inicializar con default 'app.log'.", file=sys.stderr)
        # Llama a setup_logging con el default si no se ha llamado antes.
        # Esto puede no ser ideal si se espera un log específico.
        return setup_logging() 
    return logger

# Ejemplo de uso
if __name__ == '__main__':
    # Demostración: Configurar con un nombre específico
    example_logger = setup_logging(log_filename='example_setup.log')
    if example_logger:
        example_logger.info("Logger configurado para example_setup.log")
        # Llamar a get_logger ahora devolvería la misma instancia configurada arriba
        retrieved_logger = get_logger()
        if retrieved_logger is example_logger:
            retrieved_logger.info("get_logger() devolvió la instancia configurada correctamente.")
        else:
             retrieved_logger.error("¡get_logger() devolvió una instancia diferente!")
    else:
        print("Fallo al configurar el logger de ejemplo.")

    # Probando desde un logger hijo (simulando otro módulo)
    child_logger = logging.getLogger('src.another_module')
    # No necesita setLevel o addHandler, heredará la configuración de 'src'
    child_logger.info("Mensaje desde un logger hijo.") 