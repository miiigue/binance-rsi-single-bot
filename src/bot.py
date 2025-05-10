# Este módulo contendrá la lógica principal del bot y coordinará los demás módulos.
# Por ahora, lo dejamos vacío. 

import time
import pandas as pd
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import math
from enum import Enum # <-- Importar Enum

# Importamos los módulos que hemos creado
# from .config_loader import load_config # No se usa directamente aquí ahora
from .logger_setup import get_logger
from .binance_client import (
    get_futures_client,
    get_historical_klines,
    get_futures_symbol_info,
    get_futures_position,
    get_order_book_ticker,
    create_futures_limit_order,
    get_order_status,
    cancel_futures_order
)
from .rsi_calculator import calculate_rsi
from .database import init_db_schema, record_trade # Importamos solo las necesarias

# --- Definición de Estados del Bot ---
class BotState(Enum):
    INITIALIZING = "Initializing"
    IDLE = "Idle (Waiting Cycle)"
    FETCHING_DATA = "Fetching Market Data"
    CHECKING_CONDITIONS = "Checking Entry/Exit Conditions"
    PLACING_ENTRY = "Placing Entry Order"
    WAITING_ENTRY_FILL = "Waiting Entry Order Fill"
    IN_POSITION = "In Position"
    PLACING_EXIT = "Placing Exit Order"
    WAITING_EXIT_FILL = "Waiting Exit Order Fill"
    CANCELING_ORDER = "Canceling Order"
    ERROR = "Error State"
    STOPPED = "Stopped" # <-- Nuevo estado
# ------------------------------------

class TradingBot:
    """
    Clase que encapsula la lógica de trading RSI para UN símbolo específico.
    Interactúa con Binance Futures (Testnet/Live según cliente global).
    Diseñada para ser instanciada por cada símbolo a operar.
    Ahora usa órdenes LIMIT.
    """
    def __init__(self, symbol: str, trading_params: dict):
        """
        Inicializa el bot para un símbolo específico.
        Lee parámetros, inicializa el cliente, obtiene información del símbolo y estado inicial.
        """
        self.symbol = symbol.upper()
        self.logger = get_logger()
        self.params = trading_params # <-- STORE the params dictionary
        self.logger.info(f"[{self.symbol}] Inicializando worker con parámetros: {self.params}")

        # --- Estado Interno ---
        self.current_state = BotState.INITIALIZING # Estado inicial
        self.last_error_message = None # Para guardar el último error
        self.last_known_pnl = None # <-- Initialize PnL attribute
        self.current_exit_reason = None # <-- Razón de la salida pendiente actual
        # ---------------------

        # Cliente Binance (se inicializa una vez por bot)
        self.client = get_futures_client()
        if not self.client:
            # Error crítico si no se puede inicializar el cliente
            self._set_error_state("Failed to initialize Binance client.")
            # Lanzar una excepción para detener la inicialización de este worker
            raise ConnectionError("Failed to initialize Binance client for worker.")

        # Extraer parámetros necesarios de self.params (usando .get con defaults)
        try:
            self.rsi_interval = str(self.params.get('rsi_interval', '5m'))
            self.rsi_period = int(self.params.get('rsi_period', 14))
            self.rsi_threshold_up = float(self.params.get('rsi_threshold_up', 1.5))
            self.rsi_threshold_down = float(self.params.get('rsi_threshold_down', -1.0))
            self.rsi_entry_level_low = float(self.params.get('rsi_entry_level_low', 25.0))
            # --- Leer parámetros de volumen --- 
            self.volume_sma_period = int(self.params.get('volume_sma_period', 20))
            self.volume_factor = float(self.params.get('volume_factor', 1.5))
            # --- Leer parámetro para detección de tendencia ---
            self.trend_period = int(self.params.get('trend_period', 5))
            # ----------------------------------
            self.position_size_usdt = Decimal(str(self.params.get('position_size_usdt', '50')))
            self.take_profit_usdt = Decimal(str(self.params.get('take_profit_usdt', '0')))
            self.stop_loss_usdt = Decimal(str(self.params.get('stop_loss_usdt', '0')))
            
            # --- Nuevo parámetro para timeout de órdenes LIMIT ---
            self.order_timeout_seconds = int(self.params.get('order_timeout_seconds', 60))
            if self.order_timeout_seconds < 0:
                self.logger.warning(f"[{self.symbol}] ORDER_TIMEOUT_SECONDS ({self.order_timeout_seconds}) debe ser >= 0. Usando 60.")
                self.order_timeout_seconds = 60
            # ---------------------------------------------------

            # Validaciones básicas de parámetros
            if self.volume_sma_period <= 0:
                 self.logger.warning(f"[{self.symbol}] VOLUME_SMA_PERIOD ({self.volume_sma_period}) debe ser positivo. Usando 20.")
                 self.volume_sma_period = 20
            if self.volume_factor <= 0:
                 self.logger.warning(f"[{self.symbol}] VOLUME_FACTOR ({self.volume_factor}) debe ser positivo. Usando 1.5.")
                 self.volume_factor = 1.5
            if self.trend_period <= 0:
                 self.logger.warning(f"[{self.symbol}] TREND_PERIOD ({self.trend_period}) debe ser positivo. Usando 5.")
                 self.trend_period = 5
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
        
        # --- Nuevo estado para órdenes LIMIT pendientes ---
        self.pending_entry_order_id = None  # Guarda el ID de la orden LIMIT BUY pendiente
        self.pending_exit_order_id = None   # Guarda el ID de la orden LIMIT SELL pendiente
        self.pending_order_timestamp = None # Guarda el time.time() cuando se creó la orden pendiente
        # self.current_exit_reason = None # Movido arriba con otros estados internos
        # --------------------------------------------------
        
        # self.last_known_pnl = None # Ya inicializado arriba
        
        self._check_initial_position() # Llama a get_futures_position con self.symbol

        # Si todo fue bien hasta aquí, el worker está listo para el primer ciclo
        if self.current_state == BotState.INITIALIZING: # Solo cambia si no hubo error
             # El estado se actualizará al inicio de run_once
             pass # Se pondrá en IDLE o similar al empezar el ciclo.

        self.logger.info(f"[{self.symbol}] Worker inicializado exitosamente (Timeout Órdenes: {self.order_timeout_seconds}s).")

    def _check_initial_position(self):
        """Consulta a Binance si ya existe una posición para self.symbol."""
        self.logger.info(f"[{self.symbol}] Verificando posición inicial...")
        position_data = get_futures_position(self.symbol) # Usa self.symbol
        if position_data:
            pos_amt = Decimal(position_data.get('positionAmt', '0'))
            entry_price = Decimal(position_data.get('entryPrice', '0'))
            unrealized_pnl = Decimal(position_data.get('unRealizedProfit', '0'))
            if abs(pos_amt) > Decimal('1e-9'):
                 if pos_amt > 0: # Solo LONG
                     self.logger.warning(f"[{self.symbol}] ¡Posición LONG existente encontrada! Cantidad: {pos_amt}, Precio Entrada: {entry_price}, PnL Inicial: {unrealized_pnl}")
                     self.in_position = True
                     self.current_position = {
                         'entry_price': entry_price,
                         'quantity': pos_amt,
                         'entry_time': pd.Timestamp.now(tz='UTC'), # Placeholder time
                         'position_size_usdt': abs(pos_amt * entry_price),
                         'positionAmt': pos_amt
                     }
                     self.last_known_pnl = unrealized_pnl
                 else:
                      self.logger.warning(f"[{self.symbol}] ¡Posición SHORT existente encontrada! Cantidad: {pos_amt}. Este bot no maneja SHORTs.")
                      # Even if SHORT, reset PnL state if bot thought it was LONG
                      if self.in_position:
                          self._reset_state() # Reset state if found SHORT but thought LONG
            else:
                self.logger.info(f"[{self.symbol}] No hay posición abierta inicialmente (PosAmt ~ 0).")
                # Ensure state consistency if bot thought it was in position
                if self.in_position: 
                     self._reset_state()
                else:
                    # Ensure these are None if no position
                    self.in_position = False
                    self.current_position = None
                    self.last_known_pnl = None
        else:
            # Could not get position info or no position exists
            self.logger.info(f"[{self.symbol}] No se pudo obtener información de posición inicial o no existe.")
            # Ensure state consistency
            if self.in_position:
                self._reset_state()
            else:
                self.in_position = False
                self.current_position = None
                self.last_known_pnl = None

        # Asegurarse de que no hay órdenes pendientes si encontramos una posición inicial
        if self.in_position:
             self.pending_entry_order_id = None
             self.pending_exit_order_id = None
             self.pending_order_timestamp = None
             self.current_exit_reason = None # <-- Resetear razón de salida

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

    # --- Method to calculate Volume SMA --- ADDED
    def _calculate_volume_sma(self, klines: pd.DataFrame):
        """Calculates the Simple Moving Average (SMA) of the volume and returns relevant values."""
        if klines is None or klines.empty or 'volume' not in klines.columns:
            self.logger.warning(f"[{self.symbol}] Invalid klines DataFrame or missing 'volume' column for SMA calculation.")
            return None

        try:
            # Ensure volume is numeric, coercing errors to NaN
            klines['volume'] = pd.to_numeric(klines['volume'], errors='coerce')
            
            # Calculate Volume SMA using the period defined in parameters
            # min_periods=1 allows calculation even with fewer data points than the window at the start
            volume_sma = klines['volume'].rolling(window=self.volume_sma_period, min_periods=1).mean()

            if volume_sma.empty:
                 self.logger.warning(f"[{self.symbol}] Volume SMA calculation resulted in an empty Series.")
                 return None
                 
            # Get the latest volume and its corresponding SMA value
            # We compare the last volume bar with the SMA calculated up to that point
            current_volume = klines['volume'].iloc[-1]
            average_volume = volume_sma.iloc[-1] # Use the last calculated SMA

            # Check for NaN values resulting from coercion or calculation
            if pd.isna(current_volume) or pd.isna(average_volume):
                self.logger.warning(f"[{self.symbol}] Current volume ({current_volume}) or Volume SMA ({average_volume}) is NaN.")
                return None

            # Return the values needed for the entry condition check
            # The entry condition uses: current_volume > average_volume * volume_factor
            self.logger.debug(f"[{self.symbol}] Volume Check: Current={current_volume:.2f}, Avg({self.volume_sma_period})={average_volume:.2f}, Factor={self.volume_factor}")
            return current_volume, average_volume, self.volume_factor

        except Exception as e:
            self.logger.error(f"[{self.symbol}] Error calculating Volume SMA: {e}", exc_info=True)
            return None
    # --- End of added method ---

    def _is_bearish_trend(self, klines):
        """
        Verifica si el mercado está en tendencia bajista comparando precios de cierre.
        
        Args:
            klines (pandas.DataFrame): DataFrame con datos históricos (debe incluir columna 'close')
            
        Returns:
            bool: True si el mercado está en tendencia bajista, False en caso contrario
        """
        if len(klines) < self.trend_period:
            self.logger.warning(f"[{self.symbol}] No hay suficientes datos para verificar tendencia (disponibles: {len(klines)}, necesarios: {self.trend_period})")
            return False  # Si no hay suficientes datos, asumimos que no hay tendencia bajista
            
        # Tomar los últimos N precios de cierre según trend_period
        trend_prices = klines['close'].iloc[-self.trend_period:].values
        
        # Verificar si el primer precio es mayor que el último (tendencia bajista general)
        is_bearish = trend_prices[0] > trend_prices[-1]
        
        # Log para depuración
        self.logger.debug(f"[{self.symbol}] Verificación de tendencia: Primer precio={trend_prices[0]:.4f}, Último precio={trend_prices[-1]:.4f}, ¿Bajista? {is_bearish}")
        
        return is_bearish

    def run_once(self):
        """
        Ejecuta un ciclo de la lógica del bot para self.symbol.
        Ahora maneja órdenes LIMIT, su estado pendiente/timeout y actualiza self.current_state.
        """
        try:
            # Estado inicial del ciclo (si no hay orden pendiente o error)
            if not self.pending_entry_order_id and not self.pending_exit_order_id and self.current_state != BotState.ERROR:
                self._update_state(BotState.IDLE)

            self.logger.debug(f"--- [{self.symbol}] Iniciando ciclo (Estado: {self.current_state.value}) ({time.strftime('%Y-%m-%d %H:%M:%S')}) --- ")

            # --- 0. Recuperación de Errores (Simple) ---
            # Si estamos en estado de error, intentamos resetear y continuar (podría mejorarse)
            if self.current_state == BotState.ERROR:
                 self.logger.warning(f"[{self.symbol}] Intentando recuperarse del estado de ERROR. Reseteando...")
                 self._reset_state() # Intenta resetear variables internas
                 # Volvemos a IDLE para re-evaluar todo
                 self._update_state(BotState.IDLE)
                 # Podríamos añadir un reintento o lógica más compleja aquí.

            # --- 1. MANEJAR ÓRDENES PENDIENTES --- 
            # (Verificar estado, manejar llenado o timeout)

            # 1.1 Orden de ENTRADA pendiente
            if self.pending_entry_order_id:
                self._update_state(BotState.WAITING_ENTRY_FILL)
                order_info = get_order_status(self.symbol, self.pending_entry_order_id)
                if order_info:
                    status = order_info.get('status')
                    self.logger.info(f"[{self.symbol}] Verificando orden de ENTRADA pendiente ID {self.pending_entry_order_id}. Estado: {status}")

                    if status == 'FILLED':
                        filled_qty = Decimal(order_info.get('executedQty', '0'))
                        avg_price = Decimal(order_info.get('avgPrice', '0'))
                        update_time_ms = order_info.get('updateTime', 0)
                        entry_timestamp = pd.Timestamp(update_time_ms, unit='ms', tz='UTC') if update_time_ms else pd.Timestamp.now(tz='UTC')
                        
                        self.logger.info(f"[{self.symbol}] ¡Orden LIMIT BUY {self.pending_entry_order_id} COMPLETADA! Qty: {filled_qty}, Precio Prom: {avg_price:.{self.qty_precision}f}")

                        # --- Registrar en DB --- 
                        trade_data_entry = {
                            'symbol': self.symbol,
                            'trade_type': 'LONG', 
                            'side': 'BUY', 
                            'entry_timestamp': entry_timestamp, 
                            'entry_price': avg_price,
                            'quantity': filled_qty,
                            'position_size_usdt': avg_price * filled_qty, 
                            'order_details': order_info, 
                            'reason': 'limit_order_filled',
                            'parameters': self.params # <-- Use the stored self.params
                        }
                        try:
                            record_trade(**trade_data_entry)
                            self.logger.info(f"[{self.symbol}] Trade de ENTRADA registrado en la base de datos.")
                        except Exception as db_err:
                            self.logger.error(f"[{self.symbol}] Fallo CRÍTICO al registrar trade de ENTRADA en DB: {db_err}", exc_info=True)
                            self._set_error_state(f"DB error on entry record: {db_err}")

                        # Update internal bot state
                        self.in_position = True
                        # Ensure current_position is a dict before assigning
                        self.current_position = { 
                            'entry_price': avg_price,
                            'quantity': filled_qty,
                            'entry_time': entry_timestamp,
                            'position_size_usdt': abs(filled_qty * avg_price), 
                            'positionAmt': filled_qty 
                        }
                        # Guardamos el ID por si necesitamos referenciarlo (aunque no se usa activamente ahora)
                        # self.last_entry_order_id = self.pending_entry_order_id 
                        
                        # Limpiar estado de orden pendiente
                        self.pending_entry_order_id = None
                        self.pending_order_timestamp = None
                        # No necesitamos hacer nada más en este ciclo, ya entramos
                        self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Entrada completada) ---")
                        self._update_state(BotState.IN_POSITION) # ¡Ahora estamos en posición!

                    elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                        self.logger.warning(f"[{self.symbol}] Orden LIMIT BUY {self.pending_entry_order_id} falló (Estado: {status}). Reseteando.")
                        self._reset_state()
                        self._update_state(BotState.IDLE) # Volver a buscar entrada

                    elif status == 'NEW' or status == 'PARTIALLY_FILLED':
                        # Verificar timeout si la orden sigue activa
                        if self.order_timeout_seconds > 0 and self.pending_order_timestamp:
                            elapsed_time = (time.time() - self.pending_order_timestamp)
                            if elapsed_time > self.order_timeout_seconds:
                                self.logger.warning(f"[{self.symbol}] Timeout ({elapsed_time:.1f}s > {self.order_timeout_seconds}s) alcanzado para orden LIMIT BUY {self.pending_entry_order_id}. Cancelando...")
                                self._update_state(BotState.CANCELING_ORDER)
                                cancel_success = cancel_futures_order(self.symbol, self.pending_entry_order_id)
                                if cancel_success:
                                    self.logger.info(f"[{self.symbol}] Orden {self.pending_entry_order_id} cancelada exitosamente.")
                                    self._reset_state()
                                    self._update_state(BotState.IDLE) # Volver a buscar
                                else:
                                    self.logger.error(f"[{self.symbol}] Fallo al cancelar orden {self.pending_entry_order_id} tras timeout.")
                                    # Podríamos entrar en estado ERROR o reintentar cancelación?
                                    self._set_error_state(f"Failed to cancel order {self.pending_entry_order_id} after timeout.")
                            else:
                                # Aún no hay timeout, seguir esperando
                                self.logger.info(f"[{self.symbol}] Orden LIMIT BUY {self.pending_entry_order_id} aún pendiente ({status}). Esperando... ({elapsed_time:.1f}s / {self.order_timeout_seconds}s)")
                                self._update_state(BotState.WAITING_ENTRY_FILL) # Mantener estado
                        else:
                             # Timeout deshabilitado (0) o timestamp no establecido
                             self.logger.info(f"[{self.symbol}] Orden LIMIT BUY {self.pending_entry_order_id} aún pendiente ({status}). Esperando indefinidamente (Timeout={self.order_timeout_seconds}s).")
                             self._update_state(BotState.WAITING_ENTRY_FILL)

                else:
                    # Fallo al obtener estado de la orden
                    self.logger.error(f"[{self.symbol}] No se pudo obtener el estado de la orden de entrada pendiente ID {self.pending_entry_order_id}. Reintentando en el próximo ciclo.")
                    # No cambiamos el estado, reintentará leerlo
                    # Considerar un contador de reintentos aquí?
                    self._update_state(BotState.WAITING_ENTRY_FILL) # O podríamos ir a ERROR? Por ahora reintenta.
                
                # Si aún hay una orden de entrada pendiente, no hacemos nada más este ciclo
                if self.pending_entry_order_id:
                    return

            # 1.2 Orden de SALIDA pendiente
            elif self.pending_exit_order_id:
                self._update_state(BotState.WAITING_EXIT_FILL)
                order_info = get_order_status(self.symbol, self.pending_exit_order_id)
                if order_info:
                    status = order_info.get('status')
                    self.logger.info(f"[{self.symbol}] Verificando orden de SALIDA pendiente ID {self.pending_exit_order_id}. Estado: {status}")

                    if status == 'FILLED':
                        filled_qty = Decimal(order_info.get('executedQty', '0'))
                        avg_price = Decimal(order_info.get('avgPrice', '0'))
                        update_time_ms = order_info.get('updateTime', 0)
                        exit_timestamp = pd.Timestamp(update_time_ms, unit='ms', tz='UTC') if update_time_ms else pd.Timestamp.now(tz='UTC')

                        self.logger.warning(f"[{self.symbol}] ¡Orden LIMIT SELL {self.pending_exit_order_id} COMPLETADA! Qty: {filled_qty}, Precio Prom: {avg_price:.{self.qty_precision}f}")
                        
                        # Calcular PnL final (puede ser aproximado si hubo fees)
                        final_pnl = (avg_price - self.current_position['entry_price']) * filled_qty
                        self.logger.info(f"[{self.symbol}] Registrando cierre de posición: Razón=limit_order_filled, PnL Final={final_pnl:.4f} USDT")

                        # --- Registrar en DB --- 
                        # Ensure we have current position data before accessing it
                        if self.current_position:
                            trade_data_exit = {
                                'symbol': self.symbol,
                                'trade_type': 'LONG', 
                                'side': 'SELL', 
                                'open_timestamp': self.current_position.get('entry_time'), # <-- ADDED from current position
                                'open_price': self.current_position.get('entry_price'),     # <-- ADDED from current position
                                'exit_timestamp': exit_timestamp,
                                'exit_price': avg_price,
                                'quantity': filled_qty,
                                'position_size_usdt': self.current_position.get('position_size_usdt'), # Use original size
                                'pnl_usdt': final_pnl,
                                'close_reason': 'limit_order_filled', # O la razón que disparó la salida
                                'order_details': order_info,
                                'parameters': self.params
                            }
                            try:
                                record_trade(**trade_data_exit)
                                self.logger.info(f"[{self.symbol}] Trade de SALIDA registrado en la base de datos.") # Log success
                            except Exception as db_err:
                                self.logger.error(f"[{self.symbol}] Fallo CRÍTICO al registrar trade de SALIDA en DB: {db_err}", exc_info=True)
                                self._set_error_state(f"DB error on exit record: {db_err}")
                        else:
                             self.logger.error(f"[{self.symbol}] No se encontraron datos de posición actual (self.current_position) al intentar registrar salida en DB.")
                             # We might still reset state, but logging is crucial
                        
                        # Reseteamos estado porque la posición se cerró
                        self._reset_state()
                        self._update_state(BotState.IDLE) # Volver a estado base

                    elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                        self.logger.warning(f"[{self.symbol}] Orden LIMIT SELL {self.pending_exit_order_id} falló (Estado: {status}). La posición sigue abierta. Reevaluando...")
                        # La posición sigue abierta, pero la orden falló. Limpiamos la orden pendiente.
                        self.pending_exit_order_id = None 
                        self.pending_order_timestamp = None
                        # Mantenemos in_position = True, se reevaluarán condiciones de salida.
                        self._update_state(BotState.IN_POSITION) # Volver a estado "en posición"

                    elif status == 'NEW' or status == 'PARTIALLY_FILLED':
                        # Verificar timeout
                        if self.order_timeout_seconds > 0 and self.pending_order_timestamp:
                            elapsed_time = (time.time() - self.pending_order_timestamp)
                            if elapsed_time > self.order_timeout_seconds:
                                self.logger.warning(f"[{self.symbol}] Timeout ({elapsed_time:.1f}s > {self.order_timeout_seconds}s) alcanzado para orden LIMIT SELL {self.pending_exit_order_id}. Cancelando...")
                                self._update_state(BotState.CANCELING_ORDER)
                                cancel_success = cancel_futures_order(self.symbol, self.pending_exit_order_id)
                                if cancel_success:
                                    self.logger.info(f"[{self.symbol}] Orden {self.pending_exit_order_id} cancelada exitosamente. Posición sigue abierta. Reevaluando...")
                                    self.pending_exit_order_id = None # Limpiar orden pendiente
                                    self.pending_order_timestamp = None
                                    self._update_state(BotState.IN_POSITION) # Volver a estado "en posición"
                                else:
                                    self.logger.error(f"[{self.symbol}] Fallo al cancelar orden {self.pending_exit_order_id} tras timeout.")
                                    self._set_error_state(f"Failed to cancel order {self.pending_exit_order_id} after timeout.")
                            else:
                                self.logger.info(f"[{self.symbol}] Orden LIMIT SELL {self.pending_exit_order_id} aún pendiente ({status}). Esperando... ({elapsed_time:.1f}s / {self.order_timeout_seconds}s)")
                                self._update_state(BotState.WAITING_EXIT_FILL)
                        else:
                            self.logger.info(f"[{self.symbol}] Orden LIMIT SELL {self.pending_exit_order_id} aún pendiente ({status}). Esperando indefinidamente (Timeout={self.order_timeout_seconds}s).")
                            self._update_state(BotState.WAITING_EXIT_FILL)
                else:
                    self.logger.error(f"[{self.symbol}] No se pudo obtener el estado de la orden de salida pendiente ID {self.pending_exit_order_id}. Reintentando en el próximo ciclo.")
                    self._update_state(BotState.WAITING_EXIT_FILL) # Reintentará

                # Si aún hay una orden de salida pendiente, no hacemos nada más este ciclo
                if self.pending_exit_order_id:
                    return

            # --- 2. OBTENER DATOS Y CALCULAR INDICADORES --- 
            # (Solo si no hay órdenes pendientes)
            self._update_state(BotState.FETCHING_DATA) # Estado: obteniendo datos
            # 2.1 Obtener posición actual (podríamos obtenerla antes o aquí)
            position_info = get_futures_position(self.symbol)
            current_market_price = None # Initialize

            if position_info:
                 current_pos_qty = Decimal(position_info.get('positionAmt', '0'))
                 entry_price = Decimal(position_info.get('entryPrice', '0'))
                 unrealized_pnl = Decimal(position_info.get('unRealizedProfit', '0'))
                 current_market_price = Decimal(position_info.get('markPrice', '0')) # Get current mark price for exits

                 if abs(current_pos_qty) > Decimal('1e-9'): # Check if effectively in a position
                     # Initialize current_position as dict if bot thinks it's not in position or if it's None
                     if not self.in_position or self.current_position is None: 
                         self.logger.warning(f"[{self.symbol}] Detectada posición externa o recuperada: Qty={current_pos_qty}, Entry={entry_price:.{self.qty_precision}f}")
                         self.current_position = {} 
                         
                     self.in_position = True
                     self.current_position['quantity'] = current_pos_qty
                     self.current_position['entry_price'] = entry_price
                     if 'entry_time' not in self.current_position: # Add if missing
                         self.current_position['entry_time'] = pd.Timestamp.now(tz='UTC') 
                     self.last_known_pnl = unrealized_pnl # Update PnL
                     
                     # --- Verificación de SALIDA por PnL (Stop Loss / Take Profit) --- START ---
                     if self.in_position and not self.pending_exit_order_id: # Only if in position and no exit pending
                        # 1. Stop Loss por PnL
                        sl_pnl_target = self.stop_loss_usdt
                        if sl_pnl_target < Decimal('0'): # Solo si SL es negativo (activo)
                            if self.last_known_pnl is not None and self.last_known_pnl <= sl_pnl_target:
                                self.logger.warning(f"[{self.symbol}] ¡STOP LOSS por PnL alcanzado! PnL Actual: {self.last_known_pnl:.4f} <= Target: {sl_pnl_target:.4f} USDT. Intentando cerrar posición.")
                                # Usar el precio de mercado actual para la orden de salida
                                exit_price_sl = self._get_best_exit_price('SELL') 
                                if exit_price_sl:
                                    self._place_exit_order(price=exit_price_sl, reason="stop_loss_pnl_hit")
                                    return # Terminar ciclo, orden de salida colocada
                                else:
                                    self.logger.error(f"[{self.symbol}] No se pudo obtener precio para colocar orden de Stop Loss por PnL.")
                                    # Considerar qué hacer aquí, ¿reintentar? ¿Error?

                        # 2. Take Profit por PnL (solo si no se activó el SL)
                        if not self.pending_exit_order_id: # Re-check if SL placed an order
                            tp_pnl_target = self.take_profit_usdt
                            if tp_pnl_target > Decimal('0'): # Solo si TP es positivo (activo)
                                if self.last_known_pnl is not None and self.last_known_pnl >= tp_pnl_target:
                                    self.logger.info(f"[{self.symbol}] ¡TAKE PROFIT por PnL alcanzado! PnL Actual: {self.last_known_pnl:.4f} >= Target: {tp_pnl_target:.4f} USDT. Intentando cerrar posición.")
                                    exit_price_tp = self._get_best_exit_price('SELL')
                                    if exit_price_tp:
                                        self._place_exit_order(price=exit_price_tp, reason="take_profit_pnl_hit")
                                        return # Terminar ciclo, orden de salida colocada
                                    else:
                                        self.logger.error(f"[{self.symbol}] No se pudo obtener precio para colocar orden de Take Profit por PnL.")
                     # --- Verificación de SALIDA por PnL (Stop Loss / Take Profit) --- END ---

                     if self.current_state not in [BotState.PLACING_EXIT, BotState.WAITING_EXIT_FILL, BotState.CANCELING_ORDER]:
                          self._update_state(BotState.IN_POSITION) 
                 else:
                     # Si la API dice que no hay posición, reseteamos estado interno
                     if self.in_position:
                         self.logger.info(f"[{self.symbol}] La API indica que ya no hay posición abierta (pos_qty: {current_pos_qty}). Reseteando estado.")
                         self._reset_state()
                     # self.in_position = False # _reset_state() should handle this
                     if self.current_state == BotState.IN_POSITION: # Ensure state transitions correctly
                           self._update_state(BotState.IDLE)
            else:
                 # Si get_futures_position devuelve None, asumimos que no hay posición abierta.
                 self.logger.info(f"[{self.symbol}] No se encontró información de posición desde la API (position_info es None), asumiendo no-posición.")
                 if self.in_position:
                     self.logger.warning(f"[{self.symbol}] El bot creía estar en posición, pero la API no retornó datos de posición. Reseteando estado interno.")
                     self._reset_state() # Esto se encarga de self.in_position = False, self.current_position = None, etc.
                 # Si no creía estar en posición y no hay datos, no es necesario hacer nada más aquí.

            # --- Si hay una orden de salida pendiente (colocada por SL/TP PnL u otra razón), no continuar ---
            if self.pending_exit_order_id:
                self.logger.debug(f"[{self.symbol}] Hay una orden de salida pendiente ID {self.pending_exit_order_id}. Saltando el resto de la lógica de entrada/salida.")
                return

            # 2.2 Obtener klines para RSI y Volumen
            klines = get_historical_klines(self.symbol, self.rsi_interval, limit=self.rsi_period + self.volume_sma_period + 10) # Pedir suficientes klines
            if klines.empty:
                self.logger.warning(f"[{self.symbol}] No se recibieron datos de klines (DataFrame vacío).")
                return # Exit the function for this run if no klines data

            self._update_state(BotState.CHECKING_CONDITIONS) # Estado: analizando datos
            # Calcular RSI y Volumen SMA
            # Pass only the 'close' column (Pandas Series) to calculate_rsi
            rsi_result = calculate_rsi(klines['close'], self.rsi_period)
            # Call the internal method for volume SMA
            volume_result = self._calculate_volume_sma(klines)

            # Process the RSI result (which is a Pandas Series)
            if rsi_result is None or rsi_result.empty:
                self.logger.warning(f"[{self.symbol}] No se pudo calcular el RSI (datos insuficientes o error en cálculo).")
                return 
                
            # Check if we have at least two RSI values to calculate change
            if len(rsi_result.dropna()) < 2:
                 self.logger.warning(f"[{self.symbol}] No hay suficientes valores de RSI ({len(rsi_result.dropna())}) para calcular el cambio.")
                 return
                 
            # Get the last two non-NaN RSI values
            valid_rsi = rsi_result.dropna()
            current_rsi = valid_rsi.iloc[-1]
            previous_rsi = valid_rsi.iloc[-2]
            rsi_change = current_rsi - previous_rsi
            
            self.logger.info(f"[{self.symbol}] RSI actual: {current_rsi:.2f}, Cambio: {rsi_change:.2f}, Entry Level: {self.rsi_entry_level_low:.2f}")

            # --- 3. LÓGICA DE ENTRADA / SALIDA --- 

            # 3.1 Lógica de SALIDA (Prioridad si estamos en posición)
            if self.in_position:
                self._update_state(BotState.IN_POSITION) # Asegurar estado IN_POSITION
                # Obtener precio actual (Ask para vender)
                ticker = get_order_book_ticker(self.symbol)
                current_ask_price = Decimal(ticker.get('askPrice')) if ticker else None
                if current_ask_price is None:
                     self.logger.error(f"[{self.symbol}] No se pudo obtener el precio Ask actual para evaluar salida.")
                     # ¿Mantener posición o intentar cerrar a mercado? Por ahora, mantenemos.
                     return

                # Log PnL actual
                self.last_known_pnl = (current_ask_price - self.current_position['entry_price']) * self.current_position['quantity']
                self.logger.info(f"[{self.symbol}] En posición LONG. Qty={self.current_position['quantity']}, Entry={self.current_position['entry_price']:.{self.qty_precision}f}, Actual={current_ask_price:.{self.qty_precision}f}, PnL={self.last_known_pnl:.4f} USDT")

                # Evaluar condiciones de salida
                exit_condition_met = False
                exit_reason = None

                # Stop Loss
                if self.stop_loss_usdt < 0 and self.last_known_pnl <= self.stop_loss_usdt:
                    exit_condition_met = True
                    exit_reason = "stop_loss"
                    self.logger.warning(f"[{self.symbol}] CONDICIÓN DE SALIDA (stop_loss) CUMPLIDA! (PnL={self.last_known_pnl:.4f} <= {self.stop_loss_usdt:.4f})")
                
                # Take Profit
                elif self.take_profit_usdt > 0 and self.last_known_pnl >= self.take_profit_usdt:
                    exit_condition_met = True
                    exit_reason = "take_profit"
                    self.logger.warning(f"[{self.symbol}] CONDICIÓN DE SALIDA (take_profit) CUMPLIDA! (PnL={self.last_known_pnl:.4f} >= {self.take_profit_usdt:.4f})")
                    
                # Salida por RSI (si el cambio es suficientemente negativo)
                elif rsi_change < self.rsi_threshold_down:
                     exit_condition_met = True
                     exit_reason = "rsi_threshold"
                     self.logger.warning(f"[{self.symbol}] CONDICIÓN DE SALIDA (rsi_threshold) CUMPLIDA! (Cambio={rsi_change:.2f} < {self.rsi_threshold_down:.2f})")

                # Si se cumple alguna condición de salida, colocar orden LIMIT SELL
                if exit_condition_met:
                    self.logger.warning(f"[{self.symbol}] Intentando colocar orden LIMIT SELL para cerrar posición (Razón: {exit_reason})...")
                    self._update_state(BotState.PLACING_EXIT)
                    
                    # Usar Ask Price como base para la orden de venta
                    limit_sell_price = self._adjust_price(current_ask_price)
                    quantity_to_sell = self._adjust_quantity(self.current_position['quantity'])
                    self.logger.info(f"[{self.symbol}] Calculado: Precio LIMIT SELL={limit_sell_price:.{self.qty_precision}f}, Cantidad={quantity_to_sell}")
                    
                    order_result = create_futures_limit_order(self.symbol, 'SELL', quantity_to_sell, limit_sell_price)
                    
                    if order_result and order_result.get('orderId'):
                        self.pending_exit_order_id = order_result['orderId']
                        self.pending_order_timestamp = time.time()
                        self.logger.warning(f"[{self.symbol}] Orden LIMIT SELL {self.pending_exit_order_id} colocada @ {limit_sell_price:.{self.qty_precision}f}. Esperando ejecución...")
                        self._update_state(BotState.WAITING_EXIT_FILL)
                    else:
                        self.logger.error(f"[{self.symbol}] Fallo al colocar la orden LIMIT SELL para cerrar posición.")
                        # ¿Qué hacer? Reintentar en el próximo ciclo? Entrar en ERROR?
                        self._set_error_state(f"Failed to place exit order.")
                # else:
                    # No hay condición de salida, seguimos en posición.
                    # self.logger.debug(f"[{self.symbol}] Manteniendo posición. No hay señal de salida.")
                    # self._update_state(BotState.IN_POSITION) # Ya debería estar en este estado

            # 3.2 Lógica de ENTRADA (Solo si NO estamos en posición y NO hay orden pendiente)
            elif not self.in_position:
                self._update_state(BotState.CHECKING_CONDITIONS) # Estado: buscando entrada
                
                # Verificar si estamos en tendencia bajista
                is_bearish = self._is_bearish_trend(klines)
                
                # Evaluar condiciones de entrada LONG
                rsi_entry_cond = (rsi_change > self.rsi_threshold_up) and (current_rsi < self.rsi_entry_level_low)
                volume_cond = False
                if volume_result:
                     current_volume, average_volume, vol_factor = volume_result
                     volume_cond = current_volume > average_volume * vol_factor
                     volume_threshold_str = f"{(average_volume * vol_factor):.2f}" if pd.notna(average_volume) else 'N/A'
                     volume_check_log = f"Vol: {current_volume:.2f}, AvgVol*Factor: {volume_threshold_str}"
                else:
                     volume_check_log = "Volumen N/A"
                     # Si no hay datos de volumen, ¿permitimos entrada o no?
                     # Por defecto, la haremos más restrictiva: se necesita volumen OK.
                     volume_cond = False

                # Log de condiciones
                self.logger.info(f"[{self.symbol}] Condiciones de entrada: RSI={rsi_entry_cond}, Volumen={volume_cond}, Tendencia Bajista={is_bearish}")
                
                # Loguear chequeo de condiciones - Ahora incluye verificación !is_bearish
                if rsi_entry_cond and volume_cond and not is_bearish:
                    self.logger.warning(f"[{self.symbol}] CONDICIÓN DE ENTRADA LONG CUMPLIDA (RSI + Volumen + No Bajista). Intentando colocar orden LIMIT BUY...")
                    self._update_state(BotState.PLACING_ENTRY)
                    
                    # Obtener Bid price para la orden de compra
                    ticker = get_order_book_ticker(self.symbol)
                    current_bid_price = Decimal(ticker.get('bidPrice')) if ticker else None
                    if current_bid_price is None:
                         self.logger.error(f"[{self.symbol}] No se pudo obtener el precio Bid actual para colocar orden de entrada.")
                         self._set_error_state("Failed to get Bid price for entry.")
                         return
                    
                    # Calcular cantidad basada en tamaño USDT y precio Bid
                    entry_quantity_raw = self.position_size_usdt / current_bid_price
                    entry_quantity = self._adjust_quantity(entry_quantity_raw)
                    limit_buy_price = self._adjust_price(current_bid_price)
                    self.logger.info(f"[{self.symbol}] Calculado: Precio LIMIT BUY={limit_buy_price:.{self.qty_precision}f}, Cantidad={entry_quantity}")
                    
                    # Colocar orden LIMIT BUY
                    order_result = create_futures_limit_order(self.symbol, 'BUY', entry_quantity, limit_buy_price)
                    
                    if order_result and order_result.get('orderId'):
                        self.pending_entry_order_id = order_result['orderId']
                        self.pending_order_timestamp = time.time()
                        self.logger.warning(f"[{self.symbol}] Orden LIMIT BUY {self.pending_entry_order_id} colocada @ {limit_buy_price:.{self.qty_precision}f}. Esperando ejecución...")
                        self._update_state(BotState.WAITING_ENTRY_FILL)
                    else:
                        self.logger.error(f"[{self.symbol}] Fallo al colocar la orden LIMIT BUY.")
                        self._set_error_state("Failed to place entry order.")
                        # ¿Resetear estado o reintentar?
                        # self._reset_state() 
                        # self._update_state(BotState.IDLE)

                elif rsi_entry_cond and not volume_cond:
                     self.logger.debug(f"[{self.symbol}] Condición RSI entrada OK, pero Volumen NO OK ({volume_check_log}). No se entra.")
                     self._update_state(BotState.IDLE) # Volver a esperar
                else: # Si RSI no se cumplió (independiente del volumen)
                     self.logger.debug(f"[{self.symbol}] Condiciones de entrada NO cumplidas (RSI Change: {rsi_change:.2f} vs {self.rsi_threshold_up:.2f}, RSI Level: {current_rsi:.2f} vs {self.rsi_entry_level_low:.2f}).")
                     self._update_state(BotState.IDLE) # Volver a esperar

        except Exception as e:
            # Captura general de errores durante el ciclo
            self.logger.error(f"[{self.symbol}] Error inesperado durante run_once: {e}", exc_info=True)
            self._set_error_state(f"Unhandled exception: {e}")
            # Podríamos intentar resetear el estado aquí también
            # self._reset_state()

    def _handle_successful_closure(self, close_price, quantity_closed, reason, close_timestamp=None):
        """
        Registra el trade completado en la DB y resetea el estado interno del bot para este símbolo.
        Ahora acepta más detalles de la orden completada.
        """
        if not self.current_position:
            self.logger.error(f"[{self.symbol}] Se intentó registrar cierre, pero no había datos de posición interna guardada.")
            self._reset_state() # Aún reseteamos por si acaso
            return

        # Usar datos guardados en self.current_position como base
        entry_price = self.current_position.get('entry_price', Decimal('0'))
        entry_time = self.current_position.get('entry_time')
        # Usar la cantidad real cerrada y el precio real de cierre
        quantity_dec = Decimal(str(quantity_closed))
        close_price_dec = Decimal(str(close_price))
        position_size_usdt_est = abs(entry_price * quantity_dec) # Estimar basado en cantidad cerrada

        final_pnl = (close_price_dec - entry_price) * quantity_dec
        self.logger.info(f"[{self.symbol}] Registrando cierre de posición: Razón={reason}, PnL Final={final_pnl:.4f} USDT")

        if pd.isna(entry_time):
             entry_time = pd.Timestamp.now(tz='UTC') - pd.Timedelta(minutes=1)
             self.logger.warning(f"[{self.symbol}] Timestamp de entrada no era válido, usando valor estimado.")
             
        # Usar timestamp de cierre si se proporciona, si no, usar ahora
        actual_close_timestamp = close_timestamp if close_timestamp else pd.Timestamp.now(tz='UTC')

        try:
            # Preparar parámetros para la DB (estos son los compartidos)
            db_trade_params = {
                'rsi_interval': self.rsi_interval,
                'rsi_period': self.rsi_period,
                'rsi_threshold_up': self.rsi_threshold_up,
                'rsi_threshold_down': self.rsi_threshold_down,
                'rsi_entry_level_low': self.rsi_entry_level_low,
                'trend_period': self.trend_period,
                'position_size_usdt': float(self.position_size_usdt),
                'take_profit_usdt': float(self.take_profit_usdt),
                'stop_loss_usdt': float(self.stop_loss_usdt)
            }
            record_trade(
                symbol=self.symbol,
                trade_type='LONG',
                open_timestamp=entry_time,
                close_timestamp=actual_close_timestamp, # Usar timestamp real/proporcionado
                open_price=float(entry_price),
                close_price=float(close_price_dec),
                quantity=float(quantity_dec),
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
        """Resetea el estado relacionado con órdenes pendientes y posición."""
        self.logger.debug(f"[{self.symbol}] Reseteando estado de orden pendiente/posición.")
        self.in_position = False
        self.current_position = None
        # --- Resetear también estado de órdenes pendientes ---
        self.pending_entry_order_id = None
        self.pending_exit_order_id = None
        self.pending_order_timestamp = None
        self.current_exit_reason = None # <-- Asegurar que se resetea aquí también
        # ---------------------------------------------------
        # self.last_rsi_value = None # Podríamos mantenerlo o resetearlo

    # --- Métodos para actualizar estado ---
    # (Estos se llamarán desde run_once)
    def _update_state(self, new_state: BotState, error_message: str | None = None):
        if self.current_state != new_state:
             self.logger.debug(f"[{self.symbol}] State changed from {self.current_state.value} to {new_state.value}")
             self.current_state = new_state
        if new_state == BotState.ERROR and error_message:
             self.last_error_message = error_message
             self.logger.error(f"[{self.symbol}] Error detail: {error_message}")
        elif new_state != BotState.ERROR:
             self.last_error_message = None # Limpiar mensaje de error si salimos del estado ERROR

    def get_current_status(self) -> dict:
         """Devuelve el estado actual del bot y datos relevantes."""
         status_data = {
             'symbol': self.symbol,
             'state': self.current_state.value,
             'in_position': self.in_position,
             'entry_price': float(self.current_position['entry_price']) if self.in_position else None,
             'quantity': float(self.current_position['quantity']) if self.in_position else None,
             'pnl': float(self.last_known_pnl) if self.in_position else None,
             'pending_entry_order_id': self.pending_entry_order_id,
             'pending_exit_order_id': self.pending_exit_order_id,
             'last_error': self.last_error_message
         }
         return status_data

    def _set_error_state(self, message: str):
        """Establece el estado del bot a ERROR y guarda el mensaje."""
        self.current_state = BotState.ERROR
        self.last_error_message = message
        self.logger.error(f"[{self.symbol}] Entering ERROR state: {message}")

    # --- Nuevo método para obtener el mejor precio de salida ---
    def _get_best_exit_price(self, side: str) -> Decimal | None:
        """
        Obtiene el mejor precio disponible del order book para una orden de SALIDA.
        Para salir de un LONG (SELL), usamos el mejor Bid.
        Para salir de un SHORT (BUY), usamos el mejor Ask.
        """
        ticker = get_order_book_ticker(self.symbol)
        if not ticker:
            self.logger.error(f"[{self.symbol}] No se pudo obtener el order book ticker para el precio de salida.")
            return None

        price_str = None
        if side == 'SELL': # Cerrando un LONG
            price_str = ticker.get('bidPrice')
            price_type = "Bid"
        elif side == 'BUY': # Cerrando un SHORT (cuando se implemente)
            price_str = ticker.get('askPrice')
            price_type = "Ask"
        else:
            self.logger.error(f"[{self.symbol}] Lado de orden desconocido '{side}' en _get_best_exit_price.")
            return None

        if price_str:
            price = Decimal(price_str)
            self.logger.info(f"[{self.symbol}] Mejor precio {price_type} obtenido para salida ({side}): {price}")
            return price
        else:
            self.logger.error(f"[{self.symbol}] No se pudo obtener el precio {price_type} del ticker: {ticker}")
            return None
    # --- Fin del nuevo método ---

    # --- Nuevo método para colocar una orden de salida ---
    def _place_exit_order(self, price: Decimal, reason: str):
        """
        Coloca una orden LIMIT SELL para cerrar la posición actual.
        Args:
            price (Decimal): El precio al cual intentar vender.
            reason (str): La razón para el cierre (e.g., 'take_profit', 'stop_loss').
        """
        if not self.in_position or not self.current_position:
            self.logger.error(f"[{self.symbol}] Se intentó colocar orden de salida, pero no se está en posición.")
            return

        self.logger.warning(f"[{self.symbol}] Intentando colocar orden LIMIT SELL para cerrar posición (Razón: {reason})...")
        self._update_state(BotState.PLACING_EXIT)

        # Usar el precio proporcionado (ya debería ser el mejor bid o ask según el caso)
        limit_sell_price_adjusted = self._adjust_price(price)
        quantity_to_sell = self._adjust_quantity(self.current_position['quantity'])
        
        self.logger.info(f"[{self.symbol}] Calculado para salida: Precio LIMIT SELL={limit_sell_price_adjusted:.{self.price_tick_size.as_tuple().exponent*-1 if self.price_tick_size else 2}f}, Cantidad={quantity_to_sell}")

        order_result = create_futures_limit_order(self.symbol, 'SELL', quantity_to_sell, limit_sell_price_adjusted)

        if order_result and order_result.get('orderId'):
            self.pending_exit_order_id = order_result['orderId']
            self.pending_order_timestamp = time.time()
            # Guardar la razón de la salida para usarla al registrar en DB si se llena
            self.current_exit_reason = reason 
            self.logger.warning(f"[{self.symbol}] Orden LIMIT SELL {self.pending_exit_order_id} colocada @ {limit_sell_price_adjusted:.{self.price_tick_size.as_tuple().exponent*-1 if self.price_tick_size else 2}f}. Esperando ejecución...")
            self._update_state(BotState.WAITING_EXIT_FILL)
        else:
            self.logger.error(f"[{self.symbol}] Fallo al colocar la orden LIMIT SELL para cerrar posición (Razón: {reason}).")
            self._set_error_state(f"Failed to place exit order (reason: {reason}).")
    # --- Fin del nuevo método ---


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