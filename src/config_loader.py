# Este módulo cargará la configuración desde un archivo.
# Por ahora, lo dejamos vacío.

import configparser
import os

# Define la ruta absoluta al archivo de configuración
# Se asume que config.ini está en el directorio padre del directorio 'src'
CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.ini')

def load_config(config_file=CONFIG_FILE_PATH):
    """
    Carga la configuración desde el archivo .ini especificado.

    Args:
        config_file (str): Ruta al archivo de configuración .ini.

    Returns:
        configparser.ConfigParser: Objeto con la configuración cargada.
                                    Retorna None si el archivo no se encuentra.
    """
    if not os.path.exists(config_file):
        # Usamos logging en lugar de print para errores en módulos reutilizables
        # Aún no hemos configurado el logger, así que por ahora usaremos print
        # pero lo ideal sería: logging.error(f"Archivo de configuración no encontrado en {config_file}")
        print(f"Error: Archivo de configuración no encontrado en {config_file}")
        return None

    config = configparser.ConfigParser()
    config.read(config_file)
    return config

# Ejemplo de cómo podrías usarlo (esto no se ejecutará cuando importes el módulo)
if __name__ == '__main__':
    config = load_config()
    if config:
        print("Configuración cargada exitosamente!")
        try:
            # Acceder a un valor específico
            api_key = config.get('BINANCE', 'API_KEY', fallback='No encontrado') # Usar get es más seguro
            symbol = config.get('TRADING', 'SYMBOL', fallback='No encontrado')
            db_name = config.get('DATABASE', 'DB_NAME', fallback='No encontrado')
            print(f"API Key: {api_key[:5]}..." if api_key != 'No encontrado' else "API Key: No encontrado")
            print(f"Símbolo: {symbol}")
            print(f"Base de datos: {db_name}")
        except configparser.NoSectionError as e:
            print(f"Error: Falta la sección '{e.section}' en el archivo config.ini")
        except Exception as e:
            print(f"Ocurrió un error inesperado al leer la configuración: {e}")

    else:
        print("No se pudo cargar la configuración.") 