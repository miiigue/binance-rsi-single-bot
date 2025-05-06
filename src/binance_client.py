# Este módulo gestionará la conexión y las operaciones con la API de Binance Futures
# usando la librería oficial binance-futures-connector-python.

# Importar UMFutures para USDT-Margined Futures
from binance.um_futures import UMFutures
# Importar excepciones específicas si las usamos, o un error general
from binance.error import ClientError
import pandas as pd
import time

# Importamos nuestra configuración y logger
from .config_loader import load_config
from .logger_setup import get_logger

# Variable global para el cliente de Binance Futures (para reutilizar la instancia)
futures_client_instance = None

def get_futures_client():
    """
    Crea y retorna una instancia del cliente UMFutures de Binance Futures,
    configurada según el archivo config.ini (modo live o paper/testnet).
    Reutiliza la instancia si ya fue creada.

    Returns:
        binance.um_futures.UMFutures: Instancia del cliente UMFutures.
                                      Retorna None si la configuración falla o la conexión inicial falla.
    """
    global futures_client_instance
    if futures_client_instance:
        return futures_client_instance

    logger = get_logger()
    config = load_config()
    if not config:
        logger.critical("No se pudo cargar la configuración para inicializar UMFutures Client.")
        return None

    try:
        api_key = config.get('BINANCE', 'API_KEY')
        api_secret = config.get('BINANCE', 'API_SECRET')
        mode = config.get('BINANCE', 'MODE', fallback='paper').lower()
        futures_base_url = config.get('BINANCE', 'FUTURES_BASE_URL') # Live URL: https://fapi.binance.com
        futures_testnet_url = config.get('BINANCE', 'FUTURES_TESTNET_BASE_URL') # Testnet URL: https://testnet.binancefuture.com

        if not api_key or api_key == 'TU_API_KEY_AQUI' or \
           not api_secret or api_secret == 'TU_API_SECRET_AQUI':
            logger.critical("API Key o API Secret no configuradas en config.ini. Por favor, añádelas.")
            return None

        base_url_to_use = ""
        if mode == 'paper' or mode == 'testnet':
            logger.warning("Inicializando cliente UMFutures en modo TESTNET.")
            base_url_to_use = futures_testnet_url
        else:
            logger.info("Inicializando cliente UMFutures en modo LIVE.")
            # La librería por defecto usa fapi.binance.com, pero lo pasamos explícitamente por claridad
            base_url_to_use = futures_base_url

        # Crear instancia del cliente UMFutures
        client = UMFutures(key=api_key, secret=api_secret, base_url=base_url_to_use)

        # Intentar hacer una llamada simple para verificar la conexión y las claves API
        try:
            logger.info(f"Verificando conexión con Futures API ({base_url_to_use}) usando time()...")
            server_time = client.time()
            logger.info(f"Conexión con Binance Futures {('Testnet' if mode != 'live' else 'Live')} exitosa. Hora del servidor: {server_time}")
            futures_client_instance = client
            return futures_client_instance

        except ClientError as e:
            # Capturar errores específicos de la librería
            logger.critical(f"Error de API al conectar con Binance Futures ({('Testnet' if mode != 'live' else 'Live')}): Status={e.status_code}, Code={e.error_code}, Msg={e.error_message}")
            logger.critical("Verifica tus API keys, permisos, si la URL base es correcta y si Binance está operativo.")
            return None
        except Exception as e:
            logger.critical(f"Error inesperado al verificar conexión con Binance Futures: {e}")
            return None

    except Exception as e:
        logger.critical(f"Error inesperado durante la inicialización de UMFutures Client: {e}")
        return None

def get_historical_klines(symbol: str, interval: str, limit: int = 500):
    """
    Obtiene datos históricos de velas (klines) para un símbolo y un intervalo dados.
    (Adaptado para binance-futures-connector)
    """
    logger = get_logger()
    client = get_futures_client()
    if not client:
        logger.error("No se pudo obtener el cliente UMFutures para buscar klines.")
        return None

    # La nueva librería puede tener validación interna de intervalo, pero podemos mantenerla
    # valid_intervals = [...] # Podríamos necesitar ajustar los strings si son diferentes

    logger.info(f"Obteniendo {limit} klines históricos para {symbol} en intervalo {interval}...")

    try:
        # La función se llama 'klines' en esta librería
        klines = client.klines(symbol=symbol, interval=interval, limit=limit)

        if not klines:
            logger.warning(f"No se recibieron klines para {symbol}, intervalo {interval}. ¿Es el símbolo correcto?")
            return None

        # Use lowercase and underscore standard column names
        columns = [
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ]
        df = pd.DataFrame(klines, columns=columns)

        # Convert appropriate columns to numeric types
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'quote_asset_volume',
                        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume']
        for col in numeric_cols:
            # Use errors='coerce' to turn invalid parsing into NaN (Not a Number)
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Convert timestamp columns to datetime objects (UTC)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df['close_time'] = pd.to_datetime(df['close_time'], unit='ms', utc=True)
        
        # Optional: Drop rows with NaN values in critical columns like 'close' or 'volume'
        # df.dropna(subset=['close', 'volume'], inplace=True)
        # Optional: Set timestamp as index
        # df.set_index('timestamp', inplace=True)

        # Log using the new column name 'close_time'
        logger.info(f"Se obtuvieron {len(df)} klines para {symbol}. Última vela cierra a: {df['close_time'].iloc[-1]}")
        return df

    except ClientError as e:
        logger.error(f"Error de API al obtener klines para {symbol}: Status={e.status_code}, Code={e.error_code}, Msg={e.error_message}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado al obtener/procesar klines para {symbol}: {e}", exc_info=True)
        return None

def get_futures_symbol_info(symbol: str):
    """
    Obtiene la información de un símbolo específico de futuros.
    (Adaptado para binance-futures-connector)
    """
    logger = get_logger()
    client = get_futures_client()
    if not client:
        logger.error("No se pudo obtener el cliente UMFutures para buscar info del símbolo.")
        return None

    try:
        # La función se llama 'exchange_info'
        logger.debug(f"Obteniendo información de exchange para futuros desde: {client.base_url}...")
        exchange_info = client.exchange_info()

        # El acceso a la información del símbolo puede ser igual
        for item in exchange_info['symbols']:
            if item['symbol'] == symbol:
                logger.info(f"Información encontrada para {symbol}: Precision Cantidad={item['quantityPrecision']}, Precision Precio={item['pricePrecision']}")
                logger.debug(f"Filtros para {symbol}: {item['filters']}")
                return item

        logger.error(f"No se encontró información para el símbolo {symbol} en exchange_info.")
        return None

    except ClientError as e:
        logger.error(f"Error de API al obtener exchange_info: Status={e.status_code}, Code={e.error_code}, Msg={e.error_message}")
        # ¡Aquí es donde ocurría el 403! Esperemos que ahora funcione.
        return None
    except Exception as e:
        logger.error(f"Error inesperado al obtener exchange_info: {e}", exc_info=True)
        return None

def create_futures_market_order(symbol: str, side: str, quantity: float):
    """
    Crea una orden de mercado de futuros (MARKET).
    (Adaptado para binance-futures-connector)
    """
    logger = get_logger()
    client = get_futures_client()
    if not client:
        logger.error("No se pudo obtener el cliente UMFutures para crear orden.")
        return None

    if side not in ['BUY', 'SELL']:
        logger.error(f"Lado de orden inválido: {side}. Debe ser 'BUY' o 'SELL'.")
        return None
    if quantity <= 0:
        logger.error(f"Cantidad inválida para la orden: {quantity}. Debe ser positiva.")
        return None

    # La nueva librería podría preferir pasar parámetros como un diccionario
    # --- INICIO MODIFICACIÓN HEDGE MODE ---
    position_side_to_use = 'LONG' # Como el bot solo maneja LONGs, siempre será LONG
    # --- FIN MODIFICACIÓN HEDGE MODE ---
    params = {
        'symbol': symbol,
        'side': side,
        'type': 'MARKET', # Usar string 'MARKET'
        'quantity': quantity, # La librería debería manejar el formato
        'positionSide': position_side_to_use # Obligatorio para Hedge Mode
    }

    logger.warning(f"Intentando crear orden de mercado: {side} {quantity} {symbol} (PositionSide={position_side_to_use}) con params: {params}")

    try:
        # La función se llama 'new_order'
        order = client.new_order(**params) # Usar ** para desempaquetar el diccionario
        logger.info(f"Orden de mercado creada exitosamente: ID={order.get('orderId', 'N/A')}, Symbol={order.get('symbol')}, Side={order.get('side')}, Qty={order.get('origQty')}, Status={order.get('status')}")
        logger.debug(f"Respuesta completa de la orden: {order}")
        return order

    except ClientError as e:
        logger.error(f"Error de API al crear orden {side} {quantity} {symbol}: Status={e.status_code}, Code={e.error_code}, Msg={e.error_message}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado al crear orden {side} {quantity} {symbol}: {e}", exc_info=True)
        return None

def get_futures_position(symbol: str):
    """
    Obtiene la información de la posición actual para un símbolo de futuros específico.
    (Adaptado para binance-futures-connector usando position_risk)
    """
    logger = get_logger()
    client = get_futures_client()
    if not client:
        logger.error("No se pudo obtener el cliente UMFutures para buscar posición.")
        return None

    try:
        # Usamos 'get_position_risk' que devuelve info por símbolo
        logger.debug(f"Consultando información de riesgo/posición para {symbol}...")
        positions = client.get_position_risk(symbol=symbol)

        if not positions:
            logger.info(f"No se encontró información de posición/riesgo para {symbol} (respuesta vacía).")
            return None

        # position_risk devuelve una lista incluso para un símbolo
        position_info = positions[0]

        position_amt_str = position_info.get('positionAmt', '0')
        try:
            position_amt = float(position_amt_str)
        except ValueError:
            logger.error(f"Valor inválido para positionAmt: {position_amt_str} para {symbol}.")
            return None

        # La lógica para verificar si la posición está abierta es la misma
        if abs(position_amt) > 1e-9:
            entry_price = float(position_info.get('entryPrice', '0'))
            leverage = int(position_info.get('leverage', '0')) # Leverage viene como string
            pnl = float(position_info.get('unRealizedProfit', '0'))

            logger.info(f"Posición encontrada para {symbol}: Cantidad={position_amt:.8f}, Precio Entrada={entry_price:.4f}, PnL no realizado={pnl:.4f}, Leverage={leverage}x")
            # Devolvemos el diccionario para mantener compatibilidad con el bot
            # Puede que necesitemos ajustar las claves si TradingBot accede a algo específico no presente aquí
            return position_info
        else:
            logger.debug(f"No hay posición abierta para {symbol} (Cantidad = {position_amt:.8f}).")
            return None

    except ClientError as e:
        logger.error(f"Error de API al obtener información de posición/riesgo para {symbol}: Status={e.status_code}, Code={e.error_code}, Msg={e.error_message}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado al obtener información de posición/riesgo para {symbol}: {e}", exc_info=True)
        return None

# --- Funciones existentes ---
# get_historical_klines(...)
# get_futures_symbol_info(...)
# create_futures_market_order(...)
# get_futures_position(...)

# --- NUEVAS FUNCIONES PARA ÓRDENES LIMIT ---

def get_order_book_ticker(symbol: str) -> dict | None:
    """
    Obtiene el mejor precio de compra (Bid) y venta (Ask) actual para un símbolo.
    Utiliza el endpoint futures_book_ticker.

    Args:
        symbol: El símbolo del par de futuros (ej: 'BTCUSDT').

    Returns:
        Un diccionario con 'bidPrice', 'askPrice' y otros datos si tiene éxito, None si hay error.
    """
    client = get_futures_client()
    logger = get_logger()
    if not client:
        logger.error("Cliente Binance no disponible para get_order_book_ticker.")
        return None
    try:
        # ticker = client.ticker_bookTicker(symbol=symbol.upper()) # Incorrecto 7

        # --- Octavo intento: Volvemos a book_ticker, que DEBERÍA ser el correcto ---
        ticker = client.book_ticker(symbol=symbol.upper()) 
        
        # Verificar si la respuesta contiene Bid y Ask
        bid_price = ticker.get('bidPrice')
        ask_price = ticker.get('askPrice')

        if bid_price is None or ask_price is None:
            logger.error(f"La respuesta de 'book_ticker' para {symbol} no contiene 'bidPrice' o 'askPrice'. Respuesta: {ticker}")
            return None
            
        logger.debug(f"Ticker book_ticker obtenido para {symbol}: Bid={bid_price}, Ask={ask_price}")
        return ticker # Devolver el ticker completo si Bid/Ask están presentes

    except AttributeError:
        logger.error(f"El método 'book_ticker' sigue sin existir en UMFutures. ¡Muy extraño!")
        return None
    except Exception as e:
        logger.error(f"Error al obtener el book ticker para {symbol} con 'book_ticker': {e}")
        return None

def create_futures_limit_order(symbol: str, side: str, quantity: float, price: float) -> dict | None:
    """
    Crea una orden LIMIT en Binance Futures.
    Utiliza timeInForce='GTC' (Good 'Til Canceled).

    Args:
        symbol: Símbolo del par (ej: 'BTCUSDT').
        side: 'BUY' o 'SELL'.
        quantity: La cantidad a comprar/vender.
        price: El precio límite para la orden.

    Returns:
        El diccionario de respuesta de la API si la orden se creó exitosamente, None si falló.
    """
    client = get_futures_client()
    logger = get_logger()
    if not client:
        logger.error("Cliente Binance no disponible para create_futures_limit_order.")
        return None

    side = side.upper()
    if side not in ['BUY', 'SELL']:
        logger.error(f"Lado inválido '{side}' para crear orden LIMIT.")
        return None

    try:
        logger.info(f"Intentando crear orden LIMIT {side} para {quantity} {symbol} @ {price}")
        order = client.new_order(
            symbol=symbol.upper(),
            side=side,
            type='LIMIT',
            timeInForce='GTC',
            quantity=quantity,
            price=price,
            positionSide='LONG'
        )
        logger.info(f"Orden LIMIT {side} creada para {symbol}. Respuesta API: {order}")
        # La respuesta contendrá el orderId, status ('NEW'), etc.
        return order
    except Exception as e:
        logger.error(f"Error al crear orden LIMIT {side} para {symbol} @ {price}: {e}", exc_info=True)
        return None

def get_order_status(symbol: str, order_id: int) -> dict | None:
    """
    Consulta el estado de una orden específica en Binance Futures.

    Args:
        symbol: Símbolo del par (ej: 'BTCUSDT').
        order_id: El ID de la orden a consultar.

    Returns:
        Un diccionario con la información de la orden si tiene éxito, None si hay error.
        El estado importante está en la clave 'status'.
    """
    client = get_futures_client()
    logger = get_logger()
    if not client:
        logger.error("Cliente Binance no disponible para get_order_status.")
        return None
    try:
        order_info = client.query_order(symbol=symbol.upper(), orderId=order_id)
        logger.debug(f"Estado obtenido para orden {order_id} ({symbol}): Status={order_info.get('status')}")
        return order_info
    except Exception as e:
        # Un error común aquí es "Order does not exist", que puede pasar si ya fue purgada
        # Lo manejaremos en la lógica del bot
        logger.warning(f"Error al obtener estado de la orden {order_id} ({symbol}): {e}")
        return None

def cancel_futures_order(symbol: str, order_id: int) -> dict | None:
    """
    Cancela una orden abierta específica en Binance Futures.

    Args:
        symbol: Símbolo del par (ej: 'BTCUSDT').
        order_id: El ID de la orden a cancelar.

    Returns:
        Un diccionario con la respuesta de cancelación si tiene éxito, None si hay error.
    """
    client = get_futures_client()
    logger = get_logger()
    if not client:
        logger.error("Cliente Binance no disponible para cancel_futures_order.")
        return None
    try:
        logger.warning(f"Intentando cancelar orden {order_id} para {symbol}...")
        cancel_response = client.cancel_order(symbol=symbol.upper(), orderId=order_id)
        logger.info(f"Respuesta de cancelación para orden {order_id} ({symbol}): {cancel_response}")
        # La respuesta confirma los detalles de la orden cancelada.
        return cancel_response
    except Exception as e:
        # Un error común es si la orden ya no existe (fue llenada o cancelada justo antes)
        logger.error(f"Error al intentar cancelar orden {order_id} ({symbol}): {e}", exc_info=False) # No mostrar traceback completo para errores esperados
        return None
# --- FIN NUEVAS FUNCIONES ---

# --- Bloque de ejemplo (opcionalmente actualizar si se usa) ---
if __name__ == '__main__':
    # ... (El código de prueba aquí necesitaría ser adaptado también a la nueva librería) ...
    # ... (Por ahora lo dejamos como está, ya que no se ejecuta normalmente) ...
    pass # Añadimos pass para que el if no quede vacío si comentamos lo demás 