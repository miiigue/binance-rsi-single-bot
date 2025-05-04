# Este módulo contendrá la lógica principal del bot y coordinará los demás módulos.
# Por ahora, lo dejamos vacío. 

import time
import pandas as pd
from decimal import Decimal, ROUND_DOWN
import math

# Importamos los módulos que hemos creado
from .config_loader import load_config
from .logger_setup import get_logger
from .binance_client import (
    get_futures_client,
    get_historical_klines,
    get_futures_symbol_info,
    create_futures_market_order,
    get_futures_position
)
from .rsi_calculator import calculate_rsi
from .database import init_db_pool, init_db_schema, record_trade # Para registrar trades completados

class TradingBot:
    """
    Clase principal que encapsula la lógica del bot de trading RSI.
    Interactúa realmente con Binance Futures (Testnet/Live según config).
    """
    def __init__(self):
        """Inicializa el bot cargando configuración, logger, cliente, info del símbolo y estado inicial."""
        self.logger = get_logger() # Obtener logger configurado
        self.config = load_config()
        if not self.config:
            self.logger.critical("Fallo al cargar config.ini. No se puede inicializar el bot.")
            raise ValueError("Configuración no cargada")

        self.logger.info("Inicializando TradingBot...")
        self.client = get_futures_client() # Cambiado de get_binance_client
        if not self.client:
            self.logger.critical("Fallo al obtener el cliente UMFutures. No se puede inicializar el bot.")
            raise ConnectionError("Cliente de Binance Futures no inicializado")

        # Cargar parámetros de trading desde la configuración
        try:
            self.symbol = self.config.get('TRADING', 'SYMBOL')
            self.rsi_interval = self.config.get('TRADING', 'RSI_INTERVAL')
            self.rsi_period = self.config.getint('TRADING', 'RSI_PERIOD')
            self.rsi_threshold_up = self.config.getfloat('TRADING', 'RSI_THRESHOLD_UP')
            self.rsi_threshold_down = self.config.getfloat('TRADING', 'RSI_THRESHOLD_DOWN')
            # Leer el nuevo parámetro (asegurarse de que existe, con fallback)
            self.rsi_entry_level_low = self.config.getfloat('TRADING', 'RSI_ENTRY_LEVEL_LOW', fallback=25.0) # <-- Leer nuevo parámetro
            # Usamos Decimal para parámetros financieros
            self.position_size_usdt = Decimal(self.config.get('TRADING', 'POSITION_SIZE_USDT'))
            self.take_profit_usdt = Decimal(self.config.get('TRADING', 'TAKE_PROFIT_USDT'))
            self.stop_loss_usdt = Decimal(self.config.get('TRADING', 'STOP_LOSS_USDT')) # Negativo
            # Leer cycle_sleep_seconds para usarlo si es necesario
            self.cycle_sleep_seconds = self.config.getint('TRADING', 'CYCLE_SLEEP_SECONDS', fallback=60)

            self.logger.info(f"Parámetros de Trading: Symbol={self.symbol}, Interval={self.rsi_interval}, "
                           f"RSI Period={self.rsi_period}, Threshold Up={self.rsi_threshold_up:.2f}, "
                           f"Threshold Down={self.rsi_threshold_down:.2f}, RSI Entry Level Low={self.rsi_entry_level_low:.2f}, " # <-- Añadir al log
                           f"Size={self.position_size_usdt:.2f} USDT, TP={self.take_profit_usdt:.2f} USDT, "
                           f"SL={self.stop_loss_usdt:.2f} USDT, Sleep={self.cycle_sleep_seconds}s")

        except Exception as e:
            self.logger.critical(f"Error al leer parámetros de trading de config.ini: {e}", exc_info=True)
            raise ValueError("Error en configuración de trading")

        # Obtener información del símbolo (precisión, tick size)
        self.symbol_info = get_futures_symbol_info(self.symbol)
        if not self.symbol_info:
            self.logger.critical(f"No se pudo obtener información para el símbolo {self.symbol}. Abortando.")
            raise ValueError(f"Información de símbolo {self.symbol} no disponible")

        self.qty_precision = int(self.symbol_info.get('quantityPrecision', 0))
        # Encontrar el tickSize para el precio
        self.price_tick_size = None
        for f in self.symbol_info.get('filters', []):
            if f.get('filterType') == 'PRICE_FILTER':
                self.price_tick_size = Decimal(f.get('tickSize', '0.00000001')) # Usar Decimal
                break
        if self.price_tick_size is None:
             self.logger.warning(f"No se encontró PRICE_FILTER tickSize para {self.symbol}, se usará redondeo simple.")
             # Podríamos poner un valor por defecto o decidir no operar

        # Inicializar DB pool y esquema
        if not init_db_pool():
             self.logger.warning("Fallo al inicializar el pool de DB durante el inicio del bot.")
        elif not init_db_schema():
             self.logger.warning("Fallo al inicializar el esquema de la DB durante el inicio del bot.")

        # Estado inicial del bot (verificando posición existente en Binance)
        self.in_position = False
        self.current_position = None
        self.last_rsi_value = None
        self._check_initial_position()

        self.logger.info("TradingBot inicializado exitosamente.")

    def _check_initial_position(self):
        """Consulta a Binance si ya existe una posición para el símbolo al iniciar."""
        self.logger.info(f"Verificando posición inicial para {self.symbol}...")
        position_data = get_futures_position(self.symbol)
        if position_data:
            pos_amt = Decimal(position_data.get('positionAmt', '0'))
            entry_price = Decimal(position_data.get('entryPrice', '0'))
            if abs(pos_amt) > Decimal('1e-9'): # Si la cantidad no es cero
                 # Asumimos que solo manejamos LONG por ahora
                 if pos_amt > 0:
                     self.logger.warning(f"¡Posición LONG existente encontrada al iniciar! Cantidad: {pos_amt}, Precio Entrada: {entry_price}")
                     self.in_position = True
                     # Guardamos datos relevantes de la posición existente
                     # Necesitamos simular el 'entry_time' si no lo tenemos
                     self.current_position = {
                         'entry_price': entry_price,
                         'quantity': pos_amt,
                         'entry_time': pd.Timestamp.now(tz='UTC'), # Hora actual como placeholder
                         'position_size_usdt': abs(pos_amt * entry_price), # Estimado
                         'positionAmt': pos_amt # Guardamos el valor real de la API
                     }
                 else:
                      self.logger.warning(f"¡Posición SHORT existente encontrada al iniciar! Cantidad: {pos_amt}. Este bot no maneja SHORTs.")
                     # No cambiamos self.in_position, ya que solo manejamos LONG
            else:
                self.logger.info("No hay posición abierta inicialmente.")
        else:
            self.logger.info("No se pudo obtener información de posición inicial o no existe.")
            self.in_position = False
            self.current_position = None

    def _adjust_quantity(self, quantity: Decimal) -> float:
        """Ajusta la cantidad a la precisión requerida por el símbolo."""
        # Usamos ROUND_DOWN para no exceder el margen o tamaño deseado
        adjusted_qty = quantity.quantize(Decimal('1e-' + str(self.qty_precision)), rounding=ROUND_DOWN)
        self.logger.debug(f"Cantidad original: {quantity:.8f}, Precisión: {self.qty_precision}, Cantidad ajustada: {adjusted_qty:.8f}")
        # La API de python-binance espera float o string, devolvemos float
        return float(adjusted_qty)

    def _adjust_price(self, price: Decimal) -> float:
        """Ajusta el precio al tick_size requerido (si se encontró)."""
        if self.price_tick_size is None or self.price_tick_size == 0:
            return float(price) # No se puede ajustar
        # Usamos ROUND_DOWN para precios de compra, ROUND_UP para venta (más complejo)
        # Para simplificar, usamos ROUND_DOWN por ahora
        adjusted_price = (price // self.price_tick_size) * self.price_tick_size
        self.logger.debug(f"Precio original: {price}, Tick Size: {self.price_tick_size}, Precio ajustado: {adjusted_price}")
        return float(adjusted_price)

    def run_once(self):
        """
        Ejecuta un ciclo de la lógica del bot:
        1. Verifica estado real de la posición.
        2. Obtiene datos de klines.
        3. Calcula RSI.
        4. Evalúa estrategia y ejecuta órdenes reales (BUY/SELL).
        """
        self.logger.debug(f"--- Iniciando ciclo ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")

        # 1. Verificar estado real de la posición ANTES de obtener datos
        #    Esto sincroniza nuestro estado interno con el de Binance
        live_position_data = get_futures_position(self.symbol)
        live_pos_amt = Decimal('0')
        live_entry_price = Decimal('0')
        if live_position_data:
            live_pos_amt = Decimal(live_position_data.get('positionAmt', '0'))
            live_entry_price = Decimal(live_position_data.get('entryPrice', '0'))

        # Actualizar estado interno basado en la información real
        self.in_position = abs(live_pos_amt) > Decimal('1e-9') and live_pos_amt > 0 # Solo LONG
        if self.in_position:
            # Si estamos en posición según Binance, actualizamos nuestro registro interno
            if self.current_position is None: # Si el bot pensaba que no estaba en posición
                 self.logger.warning(f"Detectada posición LONG en Binance ({live_pos_amt}) que no estaba registrada internamente. Actualizando estado.")
                 self.current_position = {
                    'entry_price': live_entry_price,
                    'quantity': live_pos_amt,
                    'entry_time': pd.Timestamp.now(tz='UTC'), # Placeholder
                    'position_size_usdt': abs(live_pos_amt * live_entry_price),
                    'positionAmt': live_pos_amt # Guardamos el valor real de la API
                 }
            else: # Actualizamos cantidad y precio por si acaso
                self.current_position['quantity'] = live_pos_amt
                self.current_position['entry_price'] = live_entry_price
                self.current_position['positionAmt'] = live_pos_amt
        elif self.current_position is not None:
             # Si Binance dice que NO hay posición, pero el bot SÍ la tenía registrada,
             # puede que se cerrara manualmente o por liquidación. Reseteamos estado.
             self.logger.warning("Binance indica que no hay posición, pero el bot la tenía registrada. Reseteando estado interno.")
             self._reset_state()

        # 2. Obtener Datos Históricos
        klines_df = get_historical_klines(self.symbol, self.rsi_interval, limit=self.rsi_period + 10)
        if klines_df is None or klines_df.empty:
            self.logger.error("No se pudieron obtener datos de klines. Saltando ciclo.")
            return

        # 3. Calcular RSI
        rsi_series = calculate_rsi(klines_df['Close'], period=self.rsi_period)
        if rsi_series is None:
            self.logger.error("No se pudo calcular el RSI. Saltando ciclo.")
            return

        current_rsi = rsi_series.iloc[-1]
        previous_rsi = rsi_series.iloc[-2]

        if pd.isna(current_rsi) or pd.isna(previous_rsi):
            self.logger.warning(f"RSI actual ({current_rsi:.2f if pd.notna(current_rsi) else 'NaN'}) o previo ({previous_rsi:.2f if pd.notna(previous_rsi) else 'NaN'}) es NaN. Esperando más datos.")
            self.last_rsi_value = current_rsi
            return

        rsi_change = current_rsi - previous_rsi
        self.logger.info(f"RSI actual: {current_rsi:.2f}, RSI previo: {previous_rsi:.2f}, Cambio: {rsi_change:.2f}, Entry Level Low: {self.rsi_entry_level_low:.2f}")
        self.last_rsi_value = current_rsi

        # --- 4. Lógica de Trading (con órdenes reales) --- 
        latest_close_price = Decimal(str(klines_df['Close'].iloc[-1]))

        # A. Lógica de ENTRADA (si NO estamos en posición según Binance)
        if not self.in_position:
            if rsi_change >= float(self.rsi_threshold_up) and current_rsi < self.rsi_entry_level_low:
                self.logger.warning(f"CONDICIÓN DE ENTRADA LONG CUMPLIDA! Cambio RSI ({rsi_change:.2f}) >= {self.rsi_threshold_up:.2f} Y RSI Actual ({current_rsi:.2f}) < {self.rsi_entry_level_low:.2f}")

                # Calcular cantidad
                if latest_close_price <= 0:
                    self.logger.error("Precio de cierre inválido (<= 0) para calcular cantidad. No se puede entrar.")
                    return
                desired_quantity = self.position_size_usdt / latest_close_price
                adjusted_quantity = self._adjust_quantity(desired_quantity)

                if adjusted_quantity <= 0:
                    self.logger.error(f"Cantidad ajustada es <= 0 ({adjusted_quantity}). No se puede crear orden.")
                    return

                # Crear orden BUY
                self.logger.info(f"Intentando crear orden BUY MARKET: {adjusted_quantity} {self.symbol[:-4]} a precio ~{latest_close_price:.4f}")
                buy_order = create_futures_market_order(
                    symbol=self.symbol,
                    side='BUY',
                    quantity=adjusted_quantity
                )

                if buy_order:
                    self.logger.info(f"Orden BUY enviada exitosamente. ID: {buy_order.get('orderId')}")
                    # No cambiamos el estado in_position aquí. Esperamos al siguiente ciclo
                    # para que get_futures_position confirme que la orden se llenó.
                    # Podríamos guardar el ID de la orden si quisiéramos verificar su estado.
                else:
                    self.logger.error("Fallo al crear la orden BUY.")
            else:
                 self.logger.debug(f"No en posición. Condición de entrada no cumplida (RSI Change: {rsi_change:.2f}, Current RSI: {current_rsi:.2f})" )

        # B. Lógica de SALIDA (si SÍ estamos en posición LONG según Binance)
        elif self.in_position:
            # Usamos los datos reales de la posición obtenidos al inicio del ciclo
            entry_price_dec = live_entry_price # Usar el precio real de la API
            quantity_dec = live_pos_amt       # Usar la cantidad real de la API

            # Calcular PnL actual (Profit and Loss) usando precio actual del mercado
            current_pnl_usdt = (latest_close_price - entry_price_dec) * quantity_dec
            self.logger.info(f"En posición LONG. Cantidad={quantity_dec:.8f}, Precio Entrada={entry_price_dec:.4f}, "
                           f"Precio Actual={latest_close_price:.4f}, PnL no realizado={current_pnl_usdt:.4f} USDT")

            close_reason = None
            # Condición 1: Salida por cambio de RSI
            if rsi_change <= float(self.rsi_threshold_down):
                self.logger.warning(f"CONDICIÓN DE SALIDA (RSI) CUMPLIDA! Cambio RSI ({rsi_change:.2f}) <= {self.rsi_threshold_down:.2f}")
                close_reason = 'rsi_threshold'

            # Condición 2: Salida por Take Profit
            elif current_pnl_usdt >= self.take_profit_usdt:
                self.logger.warning(f"CONDICIÓN DE SALIDA (TAKE PROFIT) CUMPLIDA! PnL ({current_pnl_usdt:.4f}) >= {self.take_profit_usdt:.2f}")
                close_reason = 'take_profit'

            # Condición 3: Salida por Stop Loss
            elif current_pnl_usdt <= self.stop_loss_usdt:
                self.logger.warning(f"CONDICIÓN DE SALIDA (STOP LOSS) CUMPLIDA! PnL ({current_pnl_usdt:.4f}) <= {self.stop_loss_usdt:.2f}")
                close_reason = 'stop_loss'

            # Si se cumple alguna condición de salida, intentar cerrar
            if close_reason:
                # Ajustar la cantidad a cerrar (debería ser la cantidad exacta de la posición)
                close_quantity = self._adjust_quantity(abs(quantity_dec))
                if close_quantity <= 0:
                    self.logger.error("Cantidad a cerrar ajustada es <= 0. No se puede crear orden SELL.")
                    return

                self.logger.info(f"Intentando crear orden SELL MARKET para cerrar: {close_quantity} {self.symbol[:-4]} a precio ~{latest_close_price:.4f}")
                sell_order = create_futures_market_order(
                    symbol=self.symbol,
                    side='SELL',
                    quantity=close_quantity
                )

                if sell_order:
                    self.logger.info(f"Orden SELL enviada exitosamente para cerrar posición. ID: {sell_order.get('orderId')}")
                    # Registrar y resetear estado INTERNO (Binance puede tardar en actualizar)
                    # Pasamos los datos que teníamos ANTES de enviar la orden de cierre
                    self._handle_successful_closure(
                        close_price=latest_close_price, # Precio en el momento de la decisión
                        reason=close_reason,
                        entry_time=self.current_position.get('entry_time'), # Usar el tiempo guardado
                        quantity=quantity_dec,
                        entry_price=entry_price_dec,
                        position_size_usdt=self.current_position.get('position_size_usdt')
                    )
                else:
                    self.logger.error(f"Fallo al crear la orden SELL para cerrar la posición.")
            else:
                 self.logger.debug("En posición LONG. Condiciones de salida no cumplidas.")

        self.logger.debug(f"--- Fin de ciclo ---")

    def _handle_successful_closure(self, close_price, reason, entry_time, quantity, entry_price, position_size_usdt):
        """Registra el trade completado en la DB y resetea el estado interno del bot."""

        final_pnl = (close_price - entry_price) * quantity
        self.logger.info(f"Registrando cierre de posición: Razón={reason}, PnL Final={final_pnl:.4f} USDT")

        # Asegurar que los timestamps sean válidos para la DB
        if pd.isna(entry_time):
             entry_time = pd.Timestamp.now(tz='UTC') - pd.Timedelta(minutes=1) # Estimado si no lo teníamos
             self.logger.warning("Timestamp de entrada no era válido, usando valor estimado.")

        try:
            trade_params = {
                'rsi_interval': self.rsi_interval,
                'rsi_period': self.rsi_period,
                'rsi_threshold_up': float(self.rsi_threshold_up),
                'rsi_threshold_down': float(self.rsi_threshold_down),
                'position_size_usdt': float(self.position_size_usdt),
                'take_profit_usdt': float(self.take_profit_usdt),
                'stop_loss_usdt': float(self.stop_loss_usdt)
            }
            record_trade(
                symbol=self.symbol,
                trade_type='LONG',
                open_timestamp=entry_time,
                close_timestamp=pd.Timestamp.now(tz='UTC'), # Hora actual del cierre
                open_price=entry_price,
                close_price=close_price,
                quantity=quantity,
                position_size_usdt=position_size_usdt if position_size_usdt else abs(entry_price * quantity),
                pnl_usdt=final_pnl,
                close_reason=reason,
                parameters=trade_params
            )
        except Exception as e:
            self.logger.error(f"Error al registrar el trade en la DB: {e}", exc_info=True)

        # Resetear estado interno del bot
        self._reset_state()

    def _reset_state(self):
        """Resetea las variables de estado interno de la posición."""
        self.logger.debug("Reseteando estado interno de la posición.")
        self.in_position = False
        self.current_position = None
        self.last_rsi_value = None # Reseteamos el RSI previo para evitar señales falsas inmediatas


# --- Bloque de ejemplo (sin cambios significativos, pero ahora ejecutará lógica real) --- 
if __name__ == '__main__':
    # Configurar logger y DB primero
    from .logger_setup import setup_logging
    main_logger = setup_logging()

    if main_logger:
        try:
            bot = TradingBot()
            # Ejecutar unos pocos ciclos para ver cómo funciona
            # ¡ATENCIÓN! Esto ahora puede ejecutar órdenes reales en Testnet.
            main_logger.warning("*** INICIANDO EJECUCIÓN DE PRUEBA - PUEDE CREAR ÓRDENES EN BINANCE TESTNET ***")
            for i in range(5):
                main_logger.info(f"\n===== EJECUTANDO CICLO {i+1} =====")
                bot.run_once()
                # Usar el intervalo de sleep definido en main.py si se ejecuta desde ahí
                # Aquí usamos una pausa corta solo para el ejemplo
                time.sleep(5)
            main_logger.warning("*** FIN DE EJECUCIÓN DE PRUEBA ***")

        except (ValueError, ConnectionError) as e:
            main_logger.critical(f"No se pudo inicializar el bot para la prueba: {e}")
        except Exception as e:
             main_logger.critical(f"Error inesperado durante la prueba del bot: {e}", exc_info=True)
    else:
        print("Fallo al configurar el logger, no se puede ejecutar el ejemplo de Bot.") 