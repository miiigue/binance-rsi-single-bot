# Este módulo contendrá la lógica principal del bot y coordinará los demás módulos.
# Por ahora, lo dejamos vacío. 

import time
import pandas as pd
from decimal import Decimal, ROUND_DOWN
import math

# Importamos los módulos que hemos creado
# from .config_loader import load_config # No se usa directamente aquí ahora
from .logger_setup import get_logger
from .binance_client import (
    get_futures_client,
    get_historical_klines,
    get_futures_symbol_info,
    create_futures_market_order,
    get_futures_position
)
from .rsi_calculator import calculate_rsi
from .database import init_db_pool, init_db_schema, record_trade # DB funcs son globales

class TradingBot:
    """
    Clase que encapsula la lógica de trading RSI para UN símbolo específico.
    Interactúa con Binance Futures (Testnet/Live según cliente global).
    Diseñada para ser instanciada por cada símbolo a operar.
    """
    def __init__(self, symbol: str, trading_params: dict):
        """Inicializa el bot para un símbolo específico con parámetros dados."""
        self.symbol = symbol.upper()
        self.params = trading_params # Guardar los parámetros específicos
        self.logger = get_logger() # Obtener el logger global configurado
        self.client = get_futures_client() # Obtener (o crear) instancia compartida del cliente

        if not self.client:
            # Log crítico y excepción si no hay cliente
            self.logger.critical(f"[{self.symbol}] Fallo al obtener el cliente UMFutures. No se puede inicializar worker para este símbolo.")
            raise ConnectionError(f"Cliente Binance no disponible para {self.symbol}")

        self.logger.info(f"[{self.symbol}] Inicializando worker con parámetros: {self.params}")

        # Extraer parámetros necesarios de self.params (usando .get con defaults)
        try:
            self.rsi_interval = str(self.params.get('rsi_interval', '5m'))
            self.rsi_period = int(self.params.get('rsi_period', 14))
            self.rsi_threshold_up = float(self.params.get('rsi_threshold_up', 1.5))
            self.rsi_threshold_down = float(self.params.get('rsi_threshold_down', -1.0))
            self.rsi_entry_level_low = float(self.params.get('rsi_entry_level_low', 25.0))
            # Convertir a Decimal de forma segura
            self.position_size_usdt = Decimal(str(self.params.get('position_size_usdt', '50')))
            self.take_profit_usdt = Decimal(str(self.params.get('take_profit_usdt', '0')))
            self.stop_loss_usdt = Decimal(str(self.params.get('stop_loss_usdt', '0')))
            # Validar SL negativo o cero
            if self.stop_loss_usdt > 0:
                 self.logger.warning(f"[{self.symbol}] STOP_LOSS_USDT ({self.stop_loss_usdt}) debe ser negativo o cero. Usando 0.")
                 self.stop_loss_usdt = Decimal('0')
             # Validar TP positivo o cero
            if self.take_profit_usdt < 0:
                 self.logger.warning(f"[{self.symbol}] TAKE_PROFIT_USDT ({self.take_profit_usdt}) debe ser positivo o cero. Usando 0.")
                 self.take_profit_usdt = Decimal('0')

        except (ValueError, TypeError) as e:
            self.logger.critical(f"[{self.symbol}] Error al procesar parámetros de trading recibidos: {e}", exc_info=True)
            raise ValueError(f"Parámetros de trading inválidos para {self.symbol}")

        # Obtener información del símbolo (precisión, tick size) - usa self.symbol
        self.symbol_info = get_futures_symbol_info(self.symbol)
        if not self.symbol_info:
            self.logger.critical(f"[{self.symbol}] No se pudo obtener información para el símbolo. Abortando worker.")
            raise ValueError(f"Información de símbolo {self.symbol} no disponible")

        self.qty_precision = int(self.symbol_info.get('quantityPrecision', 0))
        self.price_tick_size = None
        for f in self.symbol_info.get('filters', []):
            if f.get('filterType') == 'PRICE_FILTER':
                self.price_tick_size = Decimal(f.get('tickSize', '0.00000001'))
                break
        if self.price_tick_size is None:
             self.logger.warning(f"[{self.symbol}] No se encontró PRICE_FILTER tickSize, redondeo de precio puede ser impreciso.")

        # La inicialización de DB y esquema es global, no se hace aquí

        # Estado inicial del bot para ESTE símbolo
        self.in_position = False
        self.current_position = None
        self.last_rsi_value = None
        self._check_initial_position() # Llama a get_futures_position con self.symbol

        self.logger.info(f"[{self.symbol}] Worker inicializado exitosamente.")

    def _check_initial_position(self):
        """Consulta a Binance si ya existe una posición para self.symbol."""
        self.logger.info(f"[{self.symbol}] Verificando posición inicial...")
        position_data = get_futures_position(self.symbol) # Usa self.symbol
        if position_data:
            pos_amt = Decimal(position_data.get('positionAmt', '0'))
            entry_price = Decimal(position_data.get('entryPrice', '0'))
            if abs(pos_amt) > Decimal('1e-9'):
                 if pos_amt > 0: # Solo LONG
                     self.logger.warning(f"[{self.symbol}] ¡Posición LONG existente encontrada! Cantidad: {pos_amt}, Precio Entrada: {entry_price}")
                     self.in_position = True
                     self.current_position = {
                         'entry_price': entry_price,
                         'quantity': pos_amt,
                         'entry_time': pd.Timestamp.now(tz='UTC'), # Placeholder time
                         'position_size_usdt': abs(pos_amt * entry_price),
                         'positionAmt': pos_amt
                     }
                 else:
                      self.logger.warning(f"[{self.symbol}] ¡Posición SHORT existente encontrada! Cantidad: {pos_amt}. Este bot no maneja SHORTs.")
            else:
                self.logger.info(f"[{self.symbol}] No hay posición abierta inicialmente.")
                self.in_position = False
                self.current_position = None
        else:
            # Puede que no haya posición o que haya un error de API leve
            self.logger.info(f"[{self.symbol}] No se pudo obtener información de posición inicial o no existe.")
            self.in_position = False
            self.current_position = None

    def _adjust_quantity(self, quantity: Decimal) -> float:
        """Ajusta la cantidad a la precisión requerida por self.symbol."""
        adjusted_qty = quantity.quantize(Decimal('1e-' + str(self.qty_precision)), rounding=ROUND_DOWN)
        self.logger.debug(f"[{self.symbol}] Cantidad original: {quantity:.8f}, Precisión: {self.qty_precision}, Cantidad ajustada: {adjusted_qty:.8f}")
        return float(adjusted_qty)

    def _adjust_price(self, price: Decimal) -> float:
        """Ajusta el precio al tick_size requerido por self.symbol (si se encontró)."""
        if self.price_tick_size is None or self.price_tick_size == 0:
            return float(price)
        adjusted_price = (price // self.price_tick_size) * self.price_tick_size
        self.logger.debug(f"[{self.symbol}] Precio original: {price}, Tick Size: {self.price_tick_size}, Precio ajustado: {adjusted_price}")
        return float(adjusted_price)

    def run_once(self):
        """
        Ejecuta un ciclo de la lógica del bot para self.symbol.
        """
        self.logger.debug(f"--- [{self.symbol}] Iniciando ciclo ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---")

        # 1. Verificar estado real de la posición para ESTE símbolo
        live_position_data = get_futures_position(self.symbol)
        live_pos_amt = Decimal('0')
        live_entry_price = Decimal('0')
        if live_position_data:
            live_pos_amt = Decimal(live_position_data.get('positionAmt', '0'))
            live_entry_price = Decimal(live_position_data.get('entryPrice', '0'))

        # Actualizar estado interno basado en la información real
        was_in_position = self.in_position # Guardar estado previo
        self.in_position = abs(live_pos_amt) > Decimal('1e-9') and live_pos_amt > 0 # Solo LONG

        if self.in_position:
            if not was_in_position: # Si acabamos de entrar (detectado desde Binance)
                 self.logger.warning(f"[{self.symbol}] Detectada posición LONG en Binance ({live_pos_amt}) que no estaba registrada internamente. Actualizando estado.")
                 self.current_position = {
                    'entry_price': live_entry_price,
                    'quantity': live_pos_amt,
                    'entry_time': pd.Timestamp.now(tz='UTC'), # Placeholder
                    'position_size_usdt': abs(live_pos_amt * live_entry_price),
                    'positionAmt': live_pos_amt
                 }
            else: # Actualizamos datos si ya estábamos en posición
                self.current_position['quantity'] = live_pos_amt
                self.current_position['entry_price'] = live_entry_price
                self.current_position['positionAmt'] = live_pos_amt
        elif was_in_position: # Si ya no estamos en posición según Binance, pero creíamos estarlo
             self.logger.warning(f"[{self.symbol}] Binance indica que no hay posición, pero el bot la tenía registrada. Reseteando estado interno.")
             # NOTA: No registramos trade aquí porque no sabemos cómo se cerró (manual, SL/TP server-side, etc.)
             self._reset_state() # Solo reseteamos estado interno

        # 2. Obtener Datos Históricos (para self.symbol)
        # Usar self.rsi_interval y self.rsi_period
        klines_df = get_historical_klines(self.symbol, self.rsi_interval, limit=self.rsi_period + 10)
        if klines_df is None or klines_df.empty:
            self.logger.error(f"[{self.symbol}] No se pudieron obtener datos de klines. Saltando ciclo.")
            return

        # 3. Calcular RSI
        rsi_series = calculate_rsi(klines_df['Close'], period=self.rsi_period)
        if rsi_series is None or rsi_series.empty:
            self.logger.error(f"[{self.symbol}] No se pudo calcular el RSI. Saltando ciclo.")
            return

        # Asegurarse de que hay suficientes puntos para obtener el penúltimo
        if len(rsi_series) < 2:
            self.logger.warning(f"[{self.symbol}] No hay suficientes datos de RSI ({len(rsi_series)}) para calcular el cambio. Esperando más datos.")
            self.last_rsi_value = rsi_series.iloc[-1] if len(rsi_series) > 0 else None
            return

        current_rsi = rsi_series.iloc[-1]
        previous_rsi = rsi_series.iloc[-2]

        if pd.isna(current_rsi) or pd.isna(previous_rsi):
            self.logger.warning(f"[{self.symbol}] RSI actual ({current_rsi:.2f if pd.notna(current_rsi) else 'NaN'}) o previo ({previous_rsi:.2f if pd.notna(previous_rsi) else 'NaN'}) es NaN. Esperando más datos.")
            self.last_rsi_value = current_rsi
            return

        rsi_change = current_rsi - previous_rsi
        # Usar los parámetros de ESTA instancia en el log
        self.logger.info(f"[{self.symbol}] RSI actual: {current_rsi:.2f}, RSI previo: {previous_rsi:.2f}, Cambio: {rsi_change:.2f}, Entry Level: {self.rsi_entry_level_low:.2f}")
        self.last_rsi_value = current_rsi

        # --- 4. Lógica de Trading ---
        latest_close_price = Decimal(str(klines_df['Close'].iloc[-1]))

        # A. Lógica de ENTRADA (si NO estamos en posición según Binance)
        if not self.in_position:
            # Usar self.rsi_threshold_up y self.rsi_entry_level_low
            if rsi_change >= self.rsi_threshold_up and current_rsi < self.rsi_entry_level_low:
                self.logger.warning(f"[{self.symbol}] CONDICIÓN DE ENTRADA LONG CUMPLIDA! Cambio RSI ({rsi_change:.2f}) >= {self.rsi_threshold_up:.2f} Y RSI Actual ({current_rsi:.2f}) < {self.rsi_entry_level_low:.2f}")

                if latest_close_price <= 0:
                    self.logger.error(f"[{self.symbol}] Precio de cierre inválido (<= 0) para calcular cantidad. No se puede entrar.")
                    return
                # Usar self.position_size_usdt
                desired_quantity = self.position_size_usdt / latest_close_price
                adjusted_quantity = self._adjust_quantity(desired_quantity)

                if adjusted_quantity <= 0:
                    self.logger.error(f"[{self.symbol}] Cantidad ajustada es <= 0 ({adjusted_quantity}). No se puede crear orden.")
                    return

                self.logger.info(f"[{self.symbol}] Intentando crear orden BUY MARKET: {adjusted_quantity} {self.symbol} a precio ~{latest_close_price:.4f}")
                # Llama a create_futures_market_order con self.symbol
                buy_order = create_futures_market_order(
                    symbol=self.symbol,
                    side='BUY',
                    quantity=adjusted_quantity
                )

                if buy_order:
                    # Éxito - el estado se actualizará en el próximo ciclo al verificar posición
                    self.logger.info(f"[{self.symbol}] Orden BUY enviada exitosamente. ID: {buy_order.get('orderId')}")
                    # Podríamos guardar detalles de la orden si quisiéramos seguirla
                else:
                    self.logger.error(f"[{self.symbol}] Fallo al crear la orden BUY.")
            else:
                 self.logger.debug(f"[{self.symbol}] No en posición. Condición de entrada no cumplida.")

        # B. Lógica de SALIDA (si SÍ estamos en posición LONG según Binance)
        elif self.in_position and self.current_position: # Asegurarnos que current_position no es None
            entry_price_dec = self.current_position['entry_price']
            quantity_dec = self.current_position['quantity'] # Cantidad de NUESTRA posición interna

            # Recalcular PnL con precio actual
            current_pnl_usdt = (latest_close_price - entry_price_dec) * quantity_dec
            self.logger.info(f"[{self.symbol}] En posición LONG. Cantidad={quantity_dec:.8f}, Precio Entrada={entry_price_dec:.4f}, "
                           f"Precio Actual={latest_close_price:.4f}, PnL no realizado={current_pnl_usdt:.4f} USDT")

            close_reason = None
            # Usar self.rsi_threshold_down
            if rsi_change <= self.rsi_threshold_down:
                self.logger.warning(f"[{self.symbol}] CONDICIÓN DE SALIDA (RSI) CUMPLIDA! Cambio RSI ({rsi_change:.2f}) <= {self.rsi_threshold_down:.2f}")
                close_reason = 'rsi_threshold'

            # Usar self.take_profit_usdt (solo si es > 0)
            elif self.take_profit_usdt > 0 and current_pnl_usdt >= self.take_profit_usdt:
                self.logger.warning(f"[{self.symbol}] CONDICIÓN DE SALIDA (TAKE PROFIT) CUMPLIDA! PnL ({current_pnl_usdt:.4f}) >= {self.take_profit_usdt:.2f}")
                close_reason = 'take_profit'

            # Usar self.stop_loss_usdt (solo si es < 0)
            elif self.stop_loss_usdt < 0 and current_pnl_usdt <= self.stop_loss_usdt:
                self.logger.warning(f"[{self.symbol}] CONDICIÓN DE SALIDA (STOP LOSS) CUMPLIDA! PnL ({current_pnl_usdt:.4f}) <= {self.stop_loss_usdt:.2f}")
                close_reason = 'stop_loss'

            if close_reason:
                # Ajustar la cantidad a cerrar (la de la posición)
                close_quantity = self._adjust_quantity(abs(quantity_dec))
                if close_quantity <= 0:
                    self.logger.error(f"[{self.symbol}] Cantidad a cerrar ajustada es <= 0. No se puede crear orden SELL.")
                    return

                self.logger.info(f"[{self.symbol}] Intentando crear orden SELL MARKET para cerrar: {close_quantity} {self.symbol} a precio ~{latest_close_price:.4f}")
                # Llama a create_futures_market_order con self.symbol
                sell_order = create_futures_market_order(
                    symbol=self.symbol,
                    side='SELL',
                    quantity=close_quantity
                )

                if sell_order:
                    self.logger.info(f"[{self.symbol}] Orden SELL enviada exitosamente para cerrar posición. ID: {sell_order.get('orderId')}")
                    # Registrar y resetear estado INTERNO
                    # Usamos los datos de self.current_position que teníamos ANTES de la orden
                    self._handle_successful_closure(
                        close_price=latest_close_price, # Precio de decisión
                        reason=close_reason
                    )
                    # El estado self.in_position se actualizará en el próximo ciclo desde Binance
                else:
                    self.logger.error(f"[{self.symbol}] Fallo al crear la orden SELL para cerrar la posición.")
            else:
                 self.logger.debug(f"[{self.symbol}] En posición LONG. Condiciones de salida no cumplidas.")

        self.logger.debug(f"--- [{self.symbol}] Fin de ciclo ---")

    def _handle_successful_closure(self, close_price, reason):
        """Registra el trade completado en la DB y resetea el estado interno del bot para este símbolo."""
        if not self.current_position:
            self.logger.error(f"[{self.symbol}] Se intentó registrar cierre, pero no había datos de posición interna.")
            self._reset_state()
            return

        # Usar datos guardados en self.current_position
        entry_price = self.current_position.get('entry_price', Decimal('0'))
        quantity = self.current_position.get('quantity', Decimal('0'))
        entry_time = self.current_position.get('entry_time')
        position_size_usdt_est = self.current_position.get('position_size_usdt', abs(entry_price * quantity))

        final_pnl = (close_price - entry_price) * quantity
        self.logger.info(f"[{self.symbol}] Registrando cierre de posición: Razón={reason}, PnL Final={final_pnl:.4f} USDT")

        if pd.isna(entry_time):
             entry_time = pd.Timestamp.now(tz='UTC') - pd.Timedelta(minutes=1)
             self.logger.warning(f"[{self.symbol}] Timestamp de entrada no era válido, usando valor estimado.")

        try:
            # Preparar parámetros para la DB (estos son los compartidos)
            db_trade_params = {
                'rsi_interval': self.rsi_interval,
                'rsi_period': self.rsi_period,
                'rsi_threshold_up': self.rsi_threshold_up,
                'rsi_threshold_down': self.rsi_threshold_down,
                'rsi_entry_level_low': self.rsi_entry_level_low,
                'position_size_usdt': float(self.position_size_usdt),
                'take_profit_usdt': float(self.take_profit_usdt),
                'stop_loss_usdt': float(self.stop_loss_usdt)
            }
            record_trade(
                symbol=self.symbol, # El símbolo de esta instancia
                trade_type='LONG',
                open_timestamp=entry_time,
                close_timestamp=pd.Timestamp.now(tz='UTC'), # Hora actual del cierre
                open_price=float(entry_price),
                close_price=float(close_price),
                quantity=float(quantity),
                position_size_usdt=float(position_size_usdt_est),
                pnl_usdt=float(final_pnl),
                close_reason=reason,
                parameters=db_trade_params # Guardar los parámetros usados
            )
        except Exception as e:
            self.logger.error(f"[{self.symbol}] Error al registrar el trade en la DB: {e}", exc_info=True)

        # Resetear estado interno del bot DESPUÉS de intentar registrar
        self._reset_state()

    def _reset_state(self):
        """Resetea las variables de estado interno de la posición para este símbolo."""
        self.logger.debug(f"[{self.symbol}] Reseteando estado interno de la posición.")
        self.in_position = False
        self.current_position = None
        # self.last_rsi_value = None # Podríamos mantenerlo o resetearlo


# --- Bloque de ejemplo (ya no se usa directamente así) ---
# if __name__ == '__main__':
    # ... Este bloque se moverá y adaptará en run_bot.py ...
    # pass

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