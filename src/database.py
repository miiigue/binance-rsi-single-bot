# Este módulo interactuará con la base de datos SQLite.

import sqlite3
import json
import datetime
import os
from decimal import Decimal # Mantener para posible conversión
import pandas as pd

# Importamos la configuración y el logger (Logger sí, Config no es necesaria aquí)
# from .config_loader import load_config # Ya no necesitamos leer config de DB
from .logger_setup import get_logger

# Definir el nombre del archivo de la base de datos
# Lo ubicaremos en el directorio raíz del proyecto (un nivel arriba de 'src')
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_FILE = os.path.join(BASE_DIR, 'trades.db')

def get_db_connection():
    """Establece una conexión con la base de datos SQLite."""
    logger = get_logger()
    conn = None
    try:
        # connect() creará el archivo si no existe
        conn = sqlite3.connect(DATABASE_FILE)
        # logger.debug(f"Conexión a SQLite DB '{DATABASE_FILE}' establecida.")
        return conn
    except sqlite3.Error as e:
        logger.critical(f"Error CRÍTICO al conectar/crear SQLite DB '{DATABASE_FILE}': {e}")
        return None
    except Exception as e:
        logger.critical(f"Error inesperado al conectar con SQLite: {e}")
        return None

def init_db_schema():
    """Crea la tabla 'trades' si no existe en la base de datos SQLite."""
    logger = get_logger()
    logger.info(f"Verificando/creando esquema de DB en: {DATABASE_FILE}")
    conn = get_db_connection()
    if not conn:
        logger.error("No se pudo obtener conexión a SQLite DB para inicializar esquema.")
        return False

    # SQL para crear la tabla en SQLite.
    # Usamos 'INTEGER PRIMARY KEY AUTOINCREMENT' para el ID.
    # Usamos 'TEXT' para fechas (guardaremos en formato ISO 8601).
    # Usamos 'REAL' para números decimales (suficiente precisión para este caso).
    # Usamos 'TEXT' para guardar el JSON de parámetros.
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        trade_type TEXT NOT NULL CHECK (trade_type IN ('LONG', 'SHORT')),
        open_timestamp TEXT NOT NULL, -- Formato ISO 8601 YYYY-MM-DD HH:MM:SS.sss
        close_timestamp TEXT,
        open_price REAL NOT NULL,
        close_price REAL,
        quantity REAL NOT NULL,
        position_size_usdt REAL NOT NULL,
        pnl_usdt REAL,
        close_reason TEXT, -- 'take_profit', 'stop_loss', 'manual', 'error', 'limit_order_filled' etc.
        parameters TEXT, -- Guardar config usada para este trade (JSON string)
        created_at TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime')),
        entry_price REAL,
        exit_price REAL,
        side TEXT,
        entry_timestamp TEXT,
        exit_timestamp TEXT,
        reason TEXT,
        order_details TEXT
    );
    """
    success = False
    try:
        # Usamos 'with conn:' para manejar automáticamente commit/rollback y cierre
        with conn:
            cur = conn.cursor()
            cur.execute(create_table_sql)
        logger.info("Tabla 'trades' verificada/creada exitosamente en SQLite DB.")
        success = True
    except sqlite3.Error as e:
        logger.error(f"Error de SQLite al crear/verificar la tabla 'trades': {e}")
    except Exception as e:
        logger.error(f"Error inesperado al inicializar esquema SQLite: {e}")
    finally:
        # Aunque 'with conn:' cierra la conexión en éxito/error,
        # es buena práctica cerrarla explícitamente si la obtuvimos fuera del 'with'.
        # En este caso, 'with conn:' ya lo maneja. Si no usáramos 'with', haríamos conn.close() aquí.
        if conn:
           conn.close() # Asegurarnos de cerrar si salimos por error antes del 'with' (poco probable aquí)

    return success


def record_trade(**kwargs):
    """Registra un trade completado en la tabla 'trades'."""
    logger = get_logger()
    conn = None
    try:
        conn = get_db_connection()
        if conn is None:
            # El error ya se logueó en get_db_connection
            return
            
        cursor = conn.cursor()

        # Definir las columnas esperadas (coincide con init_db_schema)
        columns = ['symbol', 'trade_type', 'open_timestamp', 'close_timestamp', 
                   'open_price', 'close_price', 'quantity', 'position_size_usdt', 
                   'pnl_usdt', 'close_reason', 'parameters',
                   # --- Nuevas columnas para detalles de órdenes --- 
                   'entry_price', 'exit_price', 'side', 'entry_timestamp', 'exit_timestamp',
                   'reason', 'order_details'
                   # ---------------------------------------------
                   ]
                   
        # Preparar los datos a insertar
        # Usar kwargs.get(col, None) para manejar columnas opcionales/nuevas
        # Convertir Decimal a float y Timestamp a string ISO donde sea necesario
        values_dict = {}
        for col in columns:
            value = kwargs.get(col)
            # Conversiones y formateo
            if isinstance(value, Decimal):
                values_dict[col] = float(value)
            elif isinstance(value, (datetime.datetime, pd.Timestamp)):
                # Asegurarse de que tenga timezone (UTC) y formatear
                if value.tzinfo is None:
                    value = value.replace(tzinfo=datetime.timezone.utc)
                else:
                    value = value.tz_convert('UTC') # Asegurar UTC
                values_dict[col] = value.isoformat()
            elif isinstance(value, dict):
                values_dict[col] = json.dumps(value) # Convertir dict a JSON string
            else:
                values_dict[col] = value # Usar el valor tal cual (None, str, float, int)

        # Crear placeholders (?, ?, ...) y la lista de valores en orden
        placeholders = ', '.join(['?'] * len(columns))
        ordered_values = [values_dict.get(col) for col in columns]

        sql = f"INSERT INTO trades ({', '.join(columns)}) VALUES ({placeholders})"
        
        # --- Log Detallado Antes de Insertar --- 
        logger.debug(f"Intentando registrar trade en DB:")
        logger.debug(f"  SQL: {sql}")
        # Loguear valores de forma segura (truncar largos si es necesario)
        log_values = []
        for v in ordered_values:
            if isinstance(v, str) and len(v) > 100:
                log_values.append(v[:100] + '... (truncated)')
            else:
                log_values.append(repr(v)) # Usar repr para ver Nones, etc.
        logger.debug(f"  Valores: {log_values}")
        # ----------------------------------------

        cursor.execute(sql, ordered_values)
        conn.commit()
        logger.info(f"Trade para {values_dict.get('symbol', 'N/A')} ({values_dict.get('side', 'N/A')}) registrado exitosamente en la DB.")

    except sqlite3.Error as e:
        # Log específico de SQLite
        logger.error(f"Error SQLite al registrar trade: {e}", exc_info=True)
        # Intentar hacer rollback si algo falló
        if conn:
            try:
                conn.rollback()
                logger.warning("Rollback de transacción DB realizado.")
            except sqlite3.Error as rb_err:
                 logger.error(f"Error durante rollback de DB: {rb_err}")
    except Exception as e:
        # Log genérico para otros errores (ej: JSON, conversión)
        logger.error(f"Error inesperado al preparar/registrar trade en DB: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
            logger.debug("Conexión SQLite cerrada.")

# Ejemplo de uso (actualizado para SQLite)
if __name__ == '__main__':
    # Es importante llamar a setup_logging antes que a cualquier función que use get_logger
    from .logger_setup import setup_logging
    main_logger = setup_logging() # Configura el logger

    if main_logger:
        # 1. Crear/verificar la tabla (ya no necesitamos pool)
        schema_ok = init_db_schema()

        if schema_ok:
            # 2. Intentar registrar un trade de ejemplo
            # (Los tipos Decimal se convertirán a float dentro de record_trade)
            params_ejemplo = {
                'rsi_interval': '1m',
                'rsi_period': 7,
                'rsi_threshold_up': 2,
                'rsi_threshold_down': -10,
                'stop_loss_usdt': -0.01
            }

            trade_id_ejemplo = record_trade(
                symbol='TESTUSDT',
                trade_type='LONG',
                open_timestamp=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1),
                close_timestamp=datetime.datetime.now(datetime.timezone.utc),
                open_price=100.50,
                close_price=101.25,
                quantity=10.0,
                position_size_usdt=1005.0,
                pnl_usdt=7.50,
                close_reason='take_profit_test',
                parameters=params_ejemplo
            )

            if trade_id_ejemplo:
                 main_logger.info(f"Trade de ejemplo registrado con ID: {trade_id_ejemplo}")

                 # 3. Leer los trades para verificar (ejemplo)
                 conn_read = get_db_connection()
                 if conn_read:
                     try:
                         with conn_read:
                             cur = conn_read.cursor()
                             cur.execute("SELECT * FROM trades WHERE symbol = ? ORDER BY id DESC LIMIT 5", ('TESTUSDT',))
                             rows = cur.fetchall()
                             main_logger.info(f"Últimos 5 trades de TESTUSDT encontrados: {len(rows)}")
                             for row in rows:
                                 main_logger.info(f"  - {row}")
                     except sqlite3.Error as e:
                         main_logger.error(f"Error al leer trades de ejemplo: {e}")
                     finally:
                        conn_read.close()

            else:
                 main_logger.error("Fallo al registrar el trade de ejemplo.")
        else:
            main_logger.error("Fallo al inicializar el esquema de la base de datos SQLite.")

# --- FIN DE MODIFICACIONES ---
# El código original de PostgreSQL ha sido completamente reemplazado. 