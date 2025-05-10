# Este módulo contendrá la lógica para calcular el RSI.
# Por ahora, lo dejamos vacío. 

import pandas as pd
import pandas_ta as ta # Importamos la librería pandas-ta

# Importamos el logger
from .logger_setup import get_logger

def calculate_rsi(close_prices: pd.Series, period: int):
    """
    Calcula el Índice de Fuerza Relativa (RSI) usando pandas_ta.

    Args:
        close_prices (pd.Series): Una Serie de Pandas que contiene los precios de cierre.
                                  Debe tener al menos 'period' + 1 valores.
        period (int): El período a usar para el cálculo del RSI (ej: 14).

    Returns:
        pd.Series: Una Serie de Pandas con los valores de RSI calculados.
                   Los primeros 'period' valores serán NaN (Not a Number) porque
                   se necesita ese historial mínimo para el cálculo.
                   Retorna None si hay un error o datos insuficientes.
    """
    logger = get_logger()

    # Validar la entrada
    if not isinstance(close_prices, pd.Series):
        logger.error("Error en calculate_rsi: close_prices debe ser una Serie de Pandas.")
        return None
    if not isinstance(period, int) or period <= 0:
        logger.error(f"Error en calculate_rsi: el período debe ser un entero positivo, se recibió {period}.")
        return None

    # Verificar si hay suficientes datos para el cálculo
    # pandas_ta necesita al menos 'period' puntos para empezar a calcular.
    # Pediremos un poco más para estar seguros (por si acaso la librería tiene requisitos internos)
    min_required_data = period + 5 # Un pequeño margen extra
    if len(close_prices) < min_required_data:
        logger.warning(f"Datos insuficientes para calcular RSI con período {period}. "
                       f"Se necesitan {min_required_data} puntos, se tienen {len(close_prices)}.")
        return None

    try:
        # --- Forma alternativa de llamar a pandas_ta --- 
        # En lugar de close_prices.ta.rsi(...), usamos ta.rsi(close_prices, ...)
        # Esto a veces funciona mejor si el accessor .ta no se registró correctamente.

        # Asegurar que close_prices sea de tipo float para evitar problemas de dtype con pandas-ta
        close_prices_float = close_prices.astype(float)
        rsi_series = ta.rsi(close=close_prices_float, length=period, fillna=False)

        if rsi_series is None or rsi_series.empty:
             logger.error("pandas_ta.rsi devolvió None o una Serie vacía.")
             return None

        # logger.debug(f"RSI calculado para los últimos {len(rsi_series)} puntos. Último valor: {rsi_series.iloc[-1]:.2f}")
        return rsi_series

    except Exception as e:
        logger.error(f"Error inesperado al calcular RSI con pandas_ta: {e}", exc_info=True)
        # exc_info=True añade el traceback del error al log, muy útil para depurar.
        return None

# --- Bloque de ejemplo para probar la función --- 
if __name__ == '__main__':
    # Configurar logger para poder ver los mensajes del ejemplo
    from .logger_setup import setup_logging
    setup_logging()
    main_logger = get_logger()

    if main_logger:
        # Crear datos de precios de cierre de ejemplo (simulando una subida y luego bajada)
        prices_data = [
            50000, 50100, 50050, 50200, 50300, 50250, 50400, 50500, 50600, 50700, # 10
            50800, 50900, 51000, 51100, 51200, 51150, 51050, 50900, 50850, 50700, # 20
            50600, 50500, 50400, 50300, 50200, 50100, 50000, 49900, 49800, 49700  # 30
        ]
        close_prices_series = pd.Series(prices_data)
        rsi_period_example = 14

        main_logger.info(f"Probando cálculo de RSI con {len(close_prices_series)} precios y período {rsi_period_example}")

        # Calcular RSI
        rsi_values = calculate_rsi(close_prices_series, period=rsi_period_example)

        if rsi_values is not None:
            main_logger.info("Cálculo de RSI exitoso.")
            # Imprimir los últimos 5 valores de RSI calculados
            # Usamos .iloc[-5:] para obtener las últimas 5 filas
            # Usamos .round(2) para redondear a 2 decimales
            main_logger.info(f"Últimos 5 valores de RSI:\n{rsi_values.iloc[-5:].round(2)}")

            # Ejemplo de cómo obtener solo el último valor
            latest_rsi = rsi_values.iloc[-1]
            if pd.notna(latest_rsi):
                 main_logger.info(f"Último valor de RSI calculado: {latest_rsi:.2f}")
            else:
                 main_logger.warning("El último valor de RSI es NaN.")
        else:
            main_logger.error("Fallo al calcular el RSI en el ejemplo.")

        # --- Prueba con datos insuficientes --- 
        main_logger.info("\nProbando con datos insuficientes...")
        short_prices = pd.Series(prices_data[:10]) # Solo los primeros 10 precios
        rsi_short = calculate_rsi(short_prices, period=rsi_period_example)
        if rsi_short is None:
             main_logger.info("Correcto: La función devolvió None por datos insuficientes.")
        else:
             main_logger.error("Incorrecto: La función debería haber devuelto None.")
    else:
        print("Fallo al configurar el logger, no se puede ejecutar el ejemplo de RSI.") 