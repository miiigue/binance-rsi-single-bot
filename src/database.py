# Este módulo interactuará con la base de datos PostgreSQL.
# Por ahora, lo dejamos vacío. 

import psycopg2
from psycopg2 import pool
import time

# Importamos la configuración y el logger
from .config_loader import load_config
from .logger_setup import get_logger

# Variable global para el pool de conexiones (mejor que abrir/cerrar conexiones constantemente)
db_pool = None

def init_db_pool():
    """Inicializa el pool de conexiones a la base de datos."""
    global db_pool
    if db_pool:
        return db_pool # Ya inicializado

    logger = get_logger()
    config = load_config()
    if not config:
        logger.critical("No se pudo cargar la configuración para inicializar el pool de DB.")
        return None

    try:
        db_name = config.get('DATABASE', 'DB_NAME')
        db_user = config.get('DATABASE', 'DB_USER')
        db_password = config.get('DATABASE', 'DB_PASSWORD')
        db_host = config.get('DATABASE', 'DB_HOST')
        db_port = config.get('DATABASE', 'DB_PORT')

        logger.info(f"Intentando inicializar pool de conexiones a DB: {db_host}:{db_port}/{db_name} usuario: {db_user}")

        # Crear un pool de conexiones. minconn=1, maxconn=5 significa que mantendrá
        # al menos 1 conexión abierta y permitirá hasta 5 conexiones simultáneas.
        # Esto es más eficiente que crear una conexión nueva cada vez.
        db_pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            dbname=db_name,
            user=db_user,
            password=db_password,
            host=db_host,
            port=db_port
        )
        logger.info("Pool de conexiones a la base de datos inicializado exitosamente.")
        return db_pool

    except psycopg2.OperationalError as e:
        logger.critical(f"Error CRÍTICO al conectar con PostgreSQL: {e}")
        logger.critical("Verifica que PostgreSQL esté corriendo y los datos en config.ini sean correctos.")
        db_pool = None # Asegura que el pool quede como None si falla
        return None
    except Exception as e:
        logger.critical(f"Error inesperado al inicializar el pool de DB: {e}")
        db_pool = None
        return None

def get_db_connection():
    """Obtiene una conexión del pool. Espera si el pool no está listo."""
    global db_pool
    logger = get_logger()

    # Si el pool no se inicializó (quizás al inicio), intentar de nuevo.
    if not db_pool:
        logger.warning("El pool de DB no estaba inicializado. Intentando inicializar ahora...")
        init_db_pool()
        if not db_pool:
            logger.error("Fallo al inicializar el pool de DB bajo demanda.")
            return None # No se pudo inicializar

    try:
        # Obtener una conexión del pool
        conn = db_pool.getconn()
        if conn:
            # logger.debug("Conexión obtenida del pool.")
            return conn
        else:
            logger.error("No se pudo obtener una conexión del pool (pool vacío o error).")
            return None
    except Exception as e:
        logger.error(f"Error al obtener conexión del pool: {e}")
        return None

def release_db_connection(conn):
    """Devuelve una conexión al pool."""
    global db_pool
    logger = get_logger()
    if db_pool and conn:
        try:
            db_pool.putconn(conn)
            # logger.debug("Conexión devuelta al pool.")
        except Exception as e:
            logger.error(f"Error al devolver conexión al pool: {e}")
    elif not db_pool:
         logger.warning("Intento de devolver conexión pero el pool no existe.")


def init_db_schema():
    """Crea la tabla 'trades' si no existe."""
    logger = get_logger()
    conn = get_db_connection()
    if not conn:
        logger.error("No se pudo obtener conexión a DB para inicializar esquema.")
        return False

    # SQL para crear la tabla. Usamos 'IF NOT EXISTS' para evitar errores si ya existe.
    # Usamos TIMESTAMPTZ para guardar la zona horaria.
    # DECIMAL es bueno para valores monetarios exactos.
    # JSONB es útil para guardar los parámetros variables de la ejecución.
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS trades (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        trade_type VARCHAR(5) NOT NULL CHECK (trade_type IN ('LONG', 'SHORT')),
        open_timestamp TIMESTAMPTZ NOT NULL,
        close_timestamp TIMESTAMPTZ,
        open_price DECIMAL(20, 8) NOT NULL,
        close_price DECIMAL(20, 8),
        quantity DECIMAL(20, 8) NOT NULL,
        position_size_usdt DECIMAL(20, 2) NOT NULL,
        pnl_usdt DECIMAL(20, 4),
        close_reason VARCHAR(50), -- 'take_profit', 'stop_loss', 'manual', 'error'
        parameters JSONB, -- Guardar config usada para este trade (RSI thresholds, etc.)
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    );
    """
    success = False
    try:
        with conn.cursor() as cur:
            cur.execute(create_table_sql)
            conn.commit() # Guardar los cambios
            logger.info("Tabla 'trades' verificada/creada exitosamente.")
            success = True
    except psycopg2.Error as e:
        logger.error(f"Error al crear/verificar la tabla 'trades': {e}")
        try:
            conn.rollback() # Deshacer cambios si hubo error
        except:
            pass # Ignorar error en rollback
    finally:
        release_db_connection(conn) # Siempre devolver la conexión al pool

    return success


def record_trade(symbol, trade_type, open_timestamp, close_timestamp,
                 open_price, close_price, quantity, position_size_usdt,
                 pnl_usdt, close_reason, parameters):
    """
    Registra una operación completada en la base de datos.

    Args:
        symbol (str): Par de trading (ej: 'BTCUSDT').
        trade_type (str): 'LONG' o 'SHORT'.
        open_timestamp (datetime): Momento de apertura.
        close_timestamp (datetime): Momento de cierre.
        open_price (float/Decimal): Precio de apertura.
        close_price (float/Decimal): Precio de cierre.
        quantity (float/Decimal): Cantidad del activo base.
        position_size_usdt (float/Decimal): Tamaño de la posición en USDT.
        pnl_usdt (float/Decimal): Ganancia o pérdida neta en USDT.
        close_reason (str): Motivo del cierre ('take_profit', 'stop_loss', etc.).
        parameters (dict): Diccionario con los parámetros de config usados.

    Returns:
        int: El ID del trade registrado, o None si hubo un error.
    """
    logger = get_logger()
    conn = get_db_connection()
    if not conn:
        logger.error("No se pudo obtener conexión a DB para registrar trade.")
        return None

    # Convertir el diccionario de parámetros a JSON string para guardarlo en JSONB
    import json
    parameters_json = json.dumps(parameters)

    insert_sql = """
    INSERT INTO trades
        (symbol, trade_type, open_timestamp, close_timestamp, open_price, close_price,
         quantity, position_size_usdt, pnl_usdt, close_reason, parameters)
    VALUES
        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id; -- Devuelve el ID de la fila insertada
    """
    trade_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(insert_sql, (
                symbol, trade_type, open_timestamp, close_timestamp, open_price, close_price,
                quantity, position_size_usdt, pnl_usdt, close_reason, parameters_json
            ))
            trade_id = cur.fetchone()[0] # Obtener el ID devuelto
            conn.commit()
            logger.info(f"Trade ID {trade_id} para {symbol} registrado exitosamente (PnL: {pnl_usdt:.4f} USDT). Razón: {close_reason}")
    except psycopg2.Error as e:
        logger.error(f"Error al registrar trade para {symbol}: {e}")
        try:
            conn.rollback()
        except:
            pass
    except Exception as e:
        logger.error(f"Error inesperado al registrar trade: {e}")
        try:
            conn.rollback()
        except:
            pass
    finally:
        release_db_connection(conn)

    return trade_id

# Ejemplo de uso (no se ejecuta al importar)
if __name__ == '__main__':
    # Es importante llamar a setup_logging antes que a cualquier función que use get_logger
    from .logger_setup import setup_logging
    main_logger = setup_logging() # Configura el logger

    if main_logger:
        # 1. Inicializar el pool de conexiones
        pool = init_db_pool()

        if pool:
            # 2. Crear/verificar la tabla
            schema_ok = init_db_schema()

            if schema_ok:
                # 3. Intentar registrar un trade de ejemplo
                import datetime
                from decimal import Decimal # Usar Decimal para precisión financiera

                params_ejemplo = {
                    'rsi_interval': '5m',
                    'rsi_period': 14,
                    'rsi_threshold_up': 8,
                    'rsi_threshold_down': -8,
                    'stop_loss_usdt': -0.3
                }

                trade_id_ejemplo = record_trade(
                    symbol='BTCUSDT',
                    trade_type='LONG',
                    open_timestamp=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5),
                    close_timestamp=datetime.datetime.now(datetime.timezone.utc),
                    open_price=Decimal('50000.12345678'),
                    close_price=Decimal('50050.98765432'),
                    quantity=Decimal('0.001'),
                    position_size_usdt=Decimal('50.00'),
                    pnl_usdt=Decimal('0.48'), # PnL calculado (ejemplo)
                    close_reason='take_profit',
                    parameters=params_ejemplo
                )

                if trade_id_ejemplo:
                    main_logger.info(f"Trade de ejemplo registrado con ID: {trade_id_ejemplo}")
                else:
                    main_logger.error("Fallo al registrar el trade de ejemplo.")

            # Cerrar todas las conexiones del pool al final (importante)
            # En una aplicación real, esto se haría al detener el bot limpiamente.
            if db_pool:
                main_logger.info("Cerrando pool de conexiones...")
                db_pool.closeall()
                main_logger.info("Pool de conexiones cerrado.")
        else:
             main_logger.error("No se pudo inicializar el pool de DB.")
    else:
        print("Fallo al configurar el logger, no se puede ejecutar el ejemplo de DB.") 