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
    get_futures_position,
    get_order_book_ticker,
    create_futures_limit_order,
    get_order_status,
    cancel_futures_order
)
from .rsi_calculator import calculate_rsi
from .database import init_db_pool, init_db_schema, record_trade # DB funcs son globales

class TradingBot:
    """
    Clase que encapsula la lógica de trading RSI para UN símbolo específico.
    Interactúa con Binance Futures (Testnet/Live según cliente global).
    Diseñada para ser instanciada por cada símbolo a operar.
    Ahora usa órdenes LIMIT.
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
            # --- Leer parámetros de volumen --- 
            self.volume_sma_period = int(self.params.get('volume_sma_period', 20))
            self.volume_factor = float(self.params.get('volume_factor', 1.5))
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
            if self.stop_loss_usdt > 0:
                 self.logger.warning(f"[{self.symbol}] STOP_LOSS_USDT ({self.stop_loss_usdt}) debe ser negativo o cero. Usando 0.")
                 self.stop_loss_usdt = Decimal('0')
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
        # --------------------------------------------------
        
        self._check_initial_position() # Llama a get_futures_position con self.symbol

        self.logger.info(f"[{self.symbol}] Worker inicializado exitosamente (Timeout Órdenes: {self.order_timeout_seconds}s).")

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

        # Asegurarse de que no hay órdenes pendientes si encontramos una posición inicial
        if self.in_position:
             self.pending_entry_order_id = None
             self.pending_exit_order_id = None
             self.pending_order_timestamp = None

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
        Ahora maneja órdenes LIMIT y su estado pendiente/timeout.
        """
        self.logger.debug(f"--- [{self.symbol}] Iniciando ciclo ({time.strftime('%Y-%m-%d %H:%M:%S')}) --- ")

        # --- 1. MANEJAR ÓRDENES PENDIENTES --- 
        # Primero, verificar si tenemos una orden de entrada pendiente
        if self.pending_entry_order_id:
            order_info = get_order_status(self.symbol, self.pending_entry_order_id)
            if order_info:
                status = order_info.get('status')
                self.logger.info(f"[{self.symbol}] Verificando orden de ENTRADA pendiente ID {self.pending_entry_order_id}. Estado: {status}")

                if status == 'FILLED':
                    filled_qty = Decimal(order_info.get('executedQty', '0'))
                    avg_price = Decimal(order_info.get('avgPrice', '0')) # Usar avgPrice para precio real
                    # Obtener timestamp de la actualización de la orden
                    update_time_ms = order_info.get('updateTime', 0)
                    entry_timestamp = pd.Timestamp(update_time_ms, unit='ms', tz='UTC') if update_time_ms else pd.Timestamp.now(tz='UTC')
                    
                    self.logger.warning(f"[{self.symbol}] ¡Orden LIMIT BUY {self.pending_entry_order_id} COMPLETADA! Qty: {filled_qty}, Precio Prom: {avg_price:.4f}")
                    self.in_position = True
                    self.current_position = {
                        'entry_price': avg_price,
                        'quantity': filled_qty,
                        'entry_time': entry_timestamp,
                        'position_size_usdt': abs(avg_price * filled_qty),
                        'positionAmt': filled_qty # Guardar como Decimal si es posible
                    }
                    self.pending_entry_order_id = None
                    self.pending_order_timestamp = None
                    # No necesitamos hacer nada más en este ciclo, ya entramos
                    self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Entrada completada) ---")
                    return 
                
                elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                    self.logger.warning(f"[{self.symbol}] Orden LIMIT BUY {self.pending_entry_order_id} falló o fue cancelada. Estado: {status}. Reevaluando condiciones...")
                    self.pending_entry_order_id = None
                    self.pending_order_timestamp = None
                    # Continuar el ciclo para reevaluar entrada
                
                elif status in ['NEW', 'PARTIALLY_FILLED']:
                    # Verificar timeout si está habilitado (> 0)
                    if self.order_timeout_seconds > 0 and self.pending_order_timestamp:
                        elapsed_time = time.time() - self.pending_order_timestamp
                        if elapsed_time > self.order_timeout_seconds:
                            self.logger.warning(f"[{self.symbol}] Timeout ({elapsed_time:.1f}s > {self.order_timeout_seconds}s) alcanzado para orden LIMIT BUY {self.pending_entry_order_id}. Cancelando...")
                            cancel_response = cancel_futures_order(self.symbol, self.pending_entry_order_id)
                            if cancel_response:
                                self.logger.info(f"[{self.symbol}] Orden {self.pending_entry_order_id} cancelada exitosamente.")
                            else:
                                self.logger.error(f"[{self.symbol}] Fallo al cancelar orden {self.pending_entry_order_id}. Puede que ya no exista.")
                            # Limpiar estado pendiente independientemente del resultado de cancelación
                            self.pending_entry_order_id = None
                            self.pending_order_timestamp = None
                            # Continuar el ciclo para reevaluar
                        else:
                            self.logger.info(f"[{self.symbol}] Orden LIMIT BUY {self.pending_entry_order_id} aún pendiente ({status}). Esperando... ({elapsed_time:.1f}s / {self.order_timeout_seconds}s)")
                            # No hacer nada más, esperar al siguiente ciclo
                            self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Esperando entrada pendiente) ---")
                            return
                    else: # Timeout deshabilitado o timestamp no disponible
                         self.logger.info(f"[{self.symbol}] Orden LIMIT BUY {self.pending_entry_order_id} aún pendiente ({status}). Esperando (sin timeout)... ")
                         self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Esperando entrada pendiente) ---")
                         return
                else: # Otro estado inesperado?
                    self.logger.error(f"[{self.symbol}] Estado inesperado '{status}' para orden pendiente {self.pending_entry_order_id}. Limpiando.")
                    self.pending_entry_order_id = None
                    self.pending_order_timestamp = None
                    # Continuar ciclo
            else: # Error al obtener el estado de la orden
                 self.logger.warning(f"[{self.symbol}] No se pudo obtener estado para orden BUY pendiente {self.pending_entry_order_id}. Podría no existir. Limpiando.")
                 self.pending_entry_order_id = None
                 self.pending_order_timestamp = None
                 # Continuar ciclo

        # Luego, verificar si tenemos una orden de salida pendiente
        elif self.pending_exit_order_id:
            order_info = get_order_status(self.symbol, self.pending_exit_order_id)
            if order_info:
                status = order_info.get('status')
                self.logger.info(f"[{self.symbol}] Verificando orden de SALIDA pendiente ID {self.pending_exit_order_id}. Estado: {status}")
                
                if status == 'FILLED':
                    filled_qty = Decimal(order_info.get('executedQty', '0'))
                    avg_price = Decimal(order_info.get('avgPrice', '0'))
                    update_time_ms = order_info.get('updateTime', 0)
                    close_timestamp = pd.Timestamp(update_time_ms, unit='ms', tz='UTC') if update_time_ms else pd.Timestamp.now(tz='UTC')
                    close_reason = order_info.get('closePosition', False) # No tenemos la razón original, podemos intentar inferirla o usar 'limit_exit'
                    
                    self.logger.warning(f"[{self.symbol}] ¡Orden LIMIT SELL {self.pending_exit_order_id} COMPLETADA! Qty: {filled_qty}, Precio Prom: {avg_price:.4f}")
                    # Llamar a handle_successful_closure para registrar y resetear TODO el estado
                    # Le pasamos los datos reales de la orden completada
                    self._handle_successful_closure(
                        close_price=avg_price, 
                        quantity_closed=filled_qty,
                        reason='limit_order_filled', # Razón genérica para salida LIMIT
                        close_timestamp=close_timestamp
                    )
                    # _handle_successful_closure ya limpia los IDs pendientes via _reset_state()
                    # No necesitamos hacer nada más en este ciclo, ya salimos
                    self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Salida completada) ---")
                    return
                
                elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                    self.logger.warning(f"[{self.symbol}] Orden LIMIT SELL {self.pending_exit_order_id} falló o fue cancelada. Estado: {status}. Reevaluando condiciones de salida...")
                    self.pending_exit_order_id = None
                    self.pending_order_timestamp = None
                    # Continuar el ciclo para reevaluar salida
                
                elif status in ['NEW', 'PARTIALLY_FILLED']:
                     # Verificar timeout
                    if self.order_timeout_seconds > 0 and self.pending_order_timestamp:
                        elapsed_time = time.time() - self.pending_order_timestamp
                        if elapsed_time > self.order_timeout_seconds:
                            self.logger.warning(f"[{self.symbol}] Timeout ({elapsed_time:.1f}s > {self.order_timeout_seconds}s) alcanzado para orden LIMIT SELL {self.pending_exit_order_id}. Cancelando...")
                            cancel_response = cancel_futures_order(self.symbol, self.pending_exit_order_id)
                            if cancel_response:
                                self.logger.info(f"[{self.symbol}] Orden {self.pending_exit_order_id} cancelada exitosamente.")
                            else:
                                self.logger.error(f"[{self.symbol}] Fallo al cancelar orden {self.pending_exit_order_id}.")
                            self.pending_exit_order_id = None
                            self.pending_order_timestamp = None
                            # Continuar el ciclo para reevaluar
                        else:
                            self.logger.info(f"[{self.symbol}] Orden LIMIT SELL {self.pending_exit_order_id} aún pendiente ({status}). Esperando... ({elapsed_time:.1f}s / {self.order_timeout_seconds}s)")
                            self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Esperando salida pendiente) ---")
                            return
                    else: # Timeout 0
                        self.logger.info(f"[{self.symbol}] Orden LIMIT SELL {self.pending_exit_order_id} aún pendiente ({status}). Esperando (sin timeout)... ")
                        self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Esperando salida pendiente) ---")
                        return
                else: # Estado inesperado
                    self.logger.error(f"[{self.symbol}] Estado inesperado '{status}' para orden de salida pendiente {self.pending_exit_order_id}. Limpiando.")
                    self.pending_exit_order_id = None
                    self.pending_order_timestamp = None
                    # Continuar ciclo
            else: # Error al obtener estado
                self.logger.warning(f"[{self.symbol}] No se pudo obtener estado para orden SELL pendiente {self.pending_exit_order_id}. Limpiando.")
                self.pending_exit_order_id = None
                self.pending_order_timestamp = None
                # Continuar ciclo

        # --- FIN MANEJO ÓRDENES PENDIENTES ---

        # --- 2. SI NO HAY ÓRDENES PENDIENTES, PROCEDER CON LÓGICA NORMAL --- 

        # Verificar estado real de la posición (importante si una orden se llenó sin detectarlo o hubo cierre manual)
        live_position_data = get_futures_position(self.symbol)
        live_pos_amt = Decimal('0')
        live_entry_price = Decimal('0')
        if live_position_data:
            live_pos_amt = Decimal(live_position_data.get('positionAmt', '0'))
            live_entry_price = Decimal(live_position_data.get('entryPrice', '0'))

        # Actualizar estado interno basado en la información real
        was_in_position = self.in_position # Guardar estado previo
        should_be_in_position = abs(live_pos_amt) > Decimal('1e-9') and live_pos_amt > 0 # Solo LONG
        
        if should_be_in_position and not self.in_position:
             # Entramos en posición sin tenerlo registrado (quizás orden llenada entre ciclos)
             self.logger.warning(f"[{self.symbol}] Detectada posición LONG en Binance ({live_pos_amt}) no registrada internamente. Actualizando estado.")
             self.in_position = True
             self.current_position = {
                'entry_price': live_entry_price,
                'quantity': live_pos_amt,
                'entry_time': pd.Timestamp.now(tz='UTC'), # Placeholder, idealmente obtener de la orden llenada
                'position_size_usdt': abs(live_pos_amt * live_entry_price),
                'positionAmt': live_pos_amt
             }
        elif not should_be_in_position and self.in_position:
             # Salimos de posición sin tenerlo registrado (manual, SL/TP server, orden llenada...)
             self.logger.warning(f"[{self.symbol}] Binance indica que no hay posición, pero el bot la tenía registrada. Reseteando estado.")
             # No podemos registrar trade aquí si no sabemos cómo se cerró
             self._reset_state()
             was_in_position = False # Actualizar para lógica posterior
        elif should_be_in_position and self.in_position:
             # Actualizar datos de la posición existente si es necesario
             self.current_position['quantity'] = live_pos_amt
             self.current_position['entry_price'] = live_entry_price
             self.current_position['positionAmt'] = live_pos_amt
        
        # Ahora self.in_position refleja el estado real más reciente

        # Obtener Datos Históricos y Calcular Indicadores
        klines_df = get_historical_klines(self.symbol, self.rsi_interval, limit=max(self.rsi_period + 10, self.volume_sma_period + 10))
        if klines_df is None or klines_df.empty:
            self.logger.error(f"[{self.symbol}] No se pudieron obtener datos de klines. Saltando ciclo.")
            self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Error klines) ---")
            return
        
        # Calcular Volumen y RSI (código existente)
        volume_filter_enabled = True
        current_volume = Decimal('0')
        average_volume = Decimal('NaN') # Usar NaN como default
        if 'Volume' not in klines_df.columns:
             self.logger.error(f"[{self.symbol}] Columna 'Volume' no encontrada. Filtro de volumen deshabilitado.")
             volume_filter_enabled = False
        else:
             klines_df['Volume_SMA'] = klines_df['Volume'].rolling(window=self.volume_sma_period, min_periods=self.volume_sma_period).mean()
             current_volume = Decimal(str(klines_df['Volume'].iloc[-1])) # Convertir a Decimal
             # Manejar posible NaN en SMA
             avg_vol_raw = klines_df['Volume_SMA'].iloc[-1]
             average_volume = Decimal(str(avg_vol_raw)) if pd.notna(avg_vol_raw) else Decimal('NaN')
             avg_vol_str = f"{average_volume:.2f}" if not average_volume.is_nan() else 'N/A'
             self.logger.debug(f"[{self.symbol}] Vol Actual: {current_volume:.2f}, Vol SMA({self.volume_sma_period}): {avg_vol_str}, Factor: {self.volume_factor}")

        rsi_series = calculate_rsi(klines_df['Close'], period=self.rsi_period)
        if rsi_series is None or rsi_series.empty or len(rsi_series) < 2:
            self.logger.warning(f"[{self.symbol}] No hay suficientes datos de RSI. Esperando...")
            self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Datos RSI insuficientes) ---")
            return
            
        current_rsi = rsi_series.iloc[-1]
        previous_rsi = rsi_series.iloc[-2]
        if pd.isna(current_rsi) or pd.isna(previous_rsi):
            self.logger.warning(f"[{self.symbol}] RSI actual o previo es NaN. Esperando...")
            self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (RSI NaN) ---")
            return
            
        rsi_change = current_rsi - previous_rsi
        self.logger.info(f"[{self.symbol}] RSI actual: {current_rsi:.2f}, Cambio: {rsi_change:.2f}, Entry Level: {self.rsi_entry_level_low:.2f}")
        self.last_rsi_value = current_rsi
        latest_close_price = Decimal(str(klines_df['Close'].iloc[-1]))

        # --- 3. LÓGICA DE ENTRADA/SALIDA con ÓRDENES LIMIT --- 

        # A. Lógica de ENTRADA (Si NO estamos en posición Y NO hay orden de entrada pendiente)
        if not self.in_position and not self.pending_entry_order_id:
            rsi_condition_met = rsi_change >= self.rsi_threshold_up and current_rsi < self.rsi_entry_level_low
            volume_condition_met = False
            if volume_filter_enabled and not average_volume.is_nan() and average_volume > 0:
                volume_condition_met = current_volume > (average_volume * Decimal(str(self.volume_factor)))
            elif not volume_filter_enabled:
                volume_condition_met = True
                
            if rsi_condition_met and volume_condition_met:
                self.logger.warning(f"[{self.symbol}] CONDICIÓN DE ENTRADA LONG CUMPLIDA (RSI + Volumen). Intentando colocar orden LIMIT BUY...")
                # Obtener Bid actual
                ticker_info = get_order_book_ticker(self.symbol)
                if ticker_info and 'bidPrice' in ticker_info:
                    bid_price = Decimal(ticker_info['bidPrice'])
                    if bid_price > 0:
                        limit_buy_price = bid_price # Usar Bid como precio límite
                        adjusted_buy_price = self._adjust_price(limit_buy_price)
                        
                        desired_quantity = self.position_size_usdt / Decimal(str(adjusted_buy_price)) # Usar precio ajustado para calcular qty
                        adjusted_quantity = self._adjust_quantity(desired_quantity)
                        
                        if adjusted_quantity > 0:
                            self.logger.info(f"[{self.symbol}] Calculado: Precio LIMIT BUY={adjusted_buy_price:.4f}, Cantidad={adjusted_quantity:.8f}")
                            buy_order = create_futures_limit_order(
                                symbol=self.symbol,
                                side='BUY',
                                quantity=adjusted_quantity,
                                price=adjusted_buy_price
                            )
                            if buy_order and buy_order.get('orderId'):
                                self.pending_entry_order_id = buy_order['orderId']
                                self.pending_order_timestamp = time.time()
                                self.logger.warning(f"[{self.symbol}] Orden LIMIT BUY {self.pending_entry_order_id} colocada @ {adjusted_buy_price:.4f}. Esperando ejecución...")
                                # Salir del ciclo actual, se manejará en el siguiente
                                self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Orden entrada colocada) ---")
                                return
                            else:
                                self.logger.error(f"[{self.symbol}] Fallo al crear la orden LIMIT BUY.")
                                # Continuar para posible reintento en el siguiente ciclo
                        else:
                            self.logger.error(f"[{self.symbol}] Cantidad ajustada para BUY es <= 0. No se puede crear orden.")
                    else:
                        self.logger.error(f"[{self.symbol}] Precio Bid inválido ({bid_price}) obtenido. No se puede colocar orden BUY.")
                else:
                    self.logger.error(f"[{self.symbol}] No se pudo obtener el ticker (Bid/Ask) para colocar orden BUY.")
            # Log si condiciones no cumplidas (código existente)
            elif rsi_condition_met and not volume_condition_met:
                 volume_threshold_str = f"{(average_volume * Decimal(str(self.volume_factor))):.2f}" if not average_volume.is_nan() else 'N/A'
                 self.logger.debug(f"[{self.symbol}] Condición RSI entrada OK, pero Volumen NO OK (Vol: {current_volume:.2f}, AvgVol*Factor: {volume_threshold_str}). No se entra.")
            else:
                 self.logger.debug(f"[{self.symbol}] No en posición. Condición RSI entrada no cumplida.")

        # B. Lógica de SALIDA (Si SÍ estamos en posición Y NO hay orden de salida pendiente)
        elif self.in_position and not self.pending_exit_order_id and self.current_position:
            entry_price_dec = self.current_position['entry_price']
            quantity_dec = self.current_position['quantity']
            current_pnl_usdt = (latest_close_price - entry_price_dec) * quantity_dec
            self.logger.info(f"[{self.symbol}] En posición LONG. Qty={quantity_dec:.8f}, Entry={entry_price_dec:.4f}, "
                           f"Actual={latest_close_price:.4f}, PnL={current_pnl_usdt:.4f} USDT")

            close_reason = None
            if rsi_change <= self.rsi_threshold_down:
                close_reason = 'rsi_threshold'
            elif self.take_profit_usdt > 0 and current_pnl_usdt >= self.take_profit_usdt:
                close_reason = 'take_profit'
            elif self.stop_loss_usdt < 0 and current_pnl_usdt <= self.stop_loss_usdt:
                close_reason = 'stop_loss'

            if close_reason:
                self.logger.warning(f"[{self.symbol}] CONDICIÓN DE SALIDA ({close_reason}) CUMPLIDA! Intentando colocar orden LIMIT SELL...")
                # Obtener Ask actual
                ticker_info = get_order_book_ticker(self.symbol)
                if ticker_info and 'askPrice' in ticker_info:
                    ask_price = Decimal(ticker_info['askPrice'])
                    if ask_price > 0:
                        limit_sell_price = ask_price # Usar Ask como precio límite
                        adjusted_sell_price = self._adjust_price(limit_sell_price)
                        close_quantity = self._adjust_quantity(abs(quantity_dec)) # Usar la cantidad de la posición
                        
                        if close_quantity > 0:
                            self.logger.info(f"[{self.symbol}] Calculado: Precio LIMIT SELL={adjusted_sell_price:.4f}, Cantidad={close_quantity:.8f}")
                            sell_order = create_futures_limit_order(
                                symbol=self.symbol,
                                side='SELL',
                                quantity=close_quantity,
                                price=adjusted_sell_price
                            )
                            if sell_order and sell_order.get('orderId'):
                                self.pending_exit_order_id = sell_order['orderId']
                                self.pending_order_timestamp = time.time()
                                self.logger.warning(f"[{self.symbol}] Orden LIMIT SELL {self.pending_exit_order_id} colocada @ {adjusted_sell_price:.4f}. Esperando ejecución...")
                                # Salir del ciclo actual
                                self.logger.debug(f"--- [{self.symbol}] Fin de ciclo (Orden salida colocada) ---")
                                return
                            else:
                                self.logger.error(f"[{self.symbol}] Fallo al crear la orden LIMIT SELL.")
                        else:
                             self.logger.error(f"[{self.symbol}] Cantidad ajustada para SELL es <= 0. No se puede crear orden.")
                    else:
                        self.logger.error(f"[{self.symbol}] Precio Ask inválido ({ask_price}) obtenido. No se puede colocar orden SELL.")
                else:
                     self.logger.error(f"[{self.symbol}] No se pudo obtener el ticker (Bid/Ask) para colocar orden SELL.")
            else:
                 self.logger.debug(f"[{self.symbol}] En posición LONG. Condiciones de salida no cumplidas.")
        
        elif self.in_position and self.pending_exit_order_id:
             # Ya estamos manejando la orden de salida pendiente al inicio del ciclo
             self.logger.debug(f"[{self.symbol}] En posición, pero esperando que la orden de salida {self.pending_exit_order_id} se resuelva.")
             # No necesitamos hacer nada más aquí
        
        # Caso final: No estamos en posición y ya estamos esperando una orden de entrada
        elif not self.in_position and self.pending_entry_order_id:
             self.logger.debug(f"[{self.symbol}] No en posición, esperando que la orden de entrada {self.pending_entry_order_id} se resuelva.")
             # No necesitamos hacer nada más aquí

        self.logger.debug(f"--- [{self.symbol}] Fin de ciclo --- ")

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
        """Resetea las variables de estado interno de la posición y órdenes pendientes."""
        self.logger.debug(f"[{self.symbol}] Reseteando estado interno completo (posición y órdenes pendientes).")
        self.in_position = False
        self.current_position = None
        # --- Resetear también estado de órdenes pendientes ---
        self.pending_entry_order_id = None
        self.pending_exit_order_id = None
        self.pending_order_timestamp = None
        # ---------------------------------------------------
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