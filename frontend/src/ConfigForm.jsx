import React, { useState, useEffect } from 'react';

// Valores iniciales o por defecto para el formulario
const initialConfig = {
  apiKey: '',
  apiSecret: '',
  symbolsToTrade: '',
  rsiInterval: '5m',
  rsiPeriod: 14,
  rsiThresholdUp: 8,
  rsiThresholdDown: -8,
  rsiEntryLevelLow: 25,
  volumeSmaPeriod: 20,
  volumeFactor: 1.5,
  positionSizeUSDT: 50,
  stopLossUSDT: 20,
  takeProfitUSDT: 30,
  cycleSleepSeconds: 0,
  mode: 'paper',
  active: false,
  orderTimeoutSeconds: 60,
};

// Helper function to parse potential numbers from config
const parseValue = (value, defaultValue, name = '') => {
  if (value === '' || value === null || value === undefined) return defaultValue;
  // Allow '-' as a valid starting character for relevant fields
  if ((name === 'stopLossUSDT' || name === 'rsiThresholdDown') && value === '-') {
      return '-'; // Keep it as a string for now, will be parsed later or on blur
  }
  const num = Number(value);

  if (!isNaN(num)) {
    if (name === 'takeProfitUSDT' && num < 0) {
      console.warn("Take Profit debe ser positivo o cero.");
    }
    if (name === 'rsiPeriod' && (!Number.isInteger(num) || num <= 0)) {
      console.warn("RSI Period debe ser un entero positivo.");
    }
    if (name === 'cycleSleepSeconds' && num !== 0 && (num < 5 || !Number.isInteger(num))) {
      console.warn("Tiempo de espera debe ser 0 (auto) o un entero >= 5 segundos.");
    }
    if (name === 'volumeSmaPeriod' && (!Number.isInteger(num) || num <= 0)) {
      console.warn("Volume SMA Period debe ser un entero positivo.");
    }
    if (name === 'volumeFactor' && num <= 0) {
      console.warn("Volume Factor debe ser positivo.");
    }
    return num;
  }
  return defaultValue;
};

function ConfigForm() {
  // Estado para guardar los valores del formulario
  const [config, setConfig] = useState(initialConfig);
  // Estado para mensajes (opcional, para feedback al usuario)
  const [message, setMessage] = useState('');
  const [isLoading, setIsLoading] = useState(true); // Estado para indicar carga inicial

  // --- Cargar configuración inicial desde la API ---
  useEffect(() => {
    const fetchConfig = async () => {
      setMessage('Cargando configuración...');
      setIsLoading(true);
      try {
        const response = await fetch('http://localhost:5001/api/config');
        if (!response.ok) {
          throw new Error(`Error HTTP ${response.status}: ${response.statusText}`);
        }
        const backendConfig = await response.json();

        // Mapear la estructura del backend al estado plano del frontend
        const frontendState = {
          apiKey: backendConfig.BINANCE?.api_key || '',
          apiSecret: backendConfig.BINANCE?.api_secret || '',
          mode: backendConfig.BINANCE?.mode || 'paper',
          symbolsToTrade: backendConfig.SYMBOLS?.symbols_to_trade || '',
          rsiInterval: backendConfig.TRADING?.rsi_interval || '5m',
          rsiPeriod: parseValue(backendConfig.TRADING?.rsi_period, initialConfig.rsiPeriod, 'rsiPeriod'),
          rsiThresholdUp: parseValue(backendConfig.TRADING?.rsi_threshold_up, initialConfig.rsiThresholdUp, 'rsiThresholdUp'),
          rsiThresholdDown: parseValue(backendConfig.TRADING?.rsi_threshold_down, initialConfig.rsiThresholdDown, 'rsiThresholdDown'),
          rsiEntryLevelLow: parseValue(backendConfig.TRADING?.rsi_entry_level_low, initialConfig.rsiEntryLevelLow, 'rsiEntryLevelLow'),
          volumeSmaPeriod: parseValue(backendConfig.TRADING?.volume_sma_period, initialConfig.volumeSmaPeriod, 'volumeSmaPeriod'),
          volumeFactor: parseValue(backendConfig.TRADING?.volume_factor, initialConfig.volumeFactor, 'volumeFactor'),
          positionSizeUSDT: parseValue(backendConfig.TRADING?.position_size_usdt, initialConfig.positionSizeUSDT, 'positionSizeUSDT'),
          stopLossUSDT: parseValue(backendConfig.TRADING?.stop_loss_usdt, initialConfig.stopLossUSDT, 'stopLossUSDT'),
          takeProfitUSDT: parseValue(backendConfig.TRADING?.take_profit_usdt, initialConfig.takeProfitUSDT, 'takeProfitUSDT'),
          cycleSleepSeconds: parseValue(backendConfig.TRADING?.cycle_sleep_seconds, initialConfig.cycleSleepSeconds, 'cycleSleepSeconds'),
          orderTimeoutSeconds: parseValue(backendConfig.TRADING?.order_timeout_seconds, initialConfig.orderTimeoutSeconds, 'orderTimeoutSeconds'),
          active: config.active,
        };

        setConfig(frontendState);
        setMessage('Configuración cargada.');

      } catch (error) {
        console.error('Error al cargar la configuración:', error);
        setMessage(`Error al cargar configuración: ${error.message}. Usando valores por defecto.`);
        setConfig(initialConfig);
      } finally {
          setIsLoading(false);
          setTimeout(() => setMessage(''), 3000);
      }
    };

    fetchConfig();
  }, []);

  // Manejador genérico para cambios en los inputs
  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;

    let processedValue = value;

    const numericTextFields = [
      'rsiThresholdUp', 'rsiThresholdDown', 'rsiEntryLevelLow', 'volumeFactor',
      'positionSizeUSDT', 'stopLossUSDT', 'takeProfitUSDT'
    ];
    const integerNumberFields = [
      'rsiPeriod', 'volumeSmaPeriod', 'cycleSleepSeconds', 'orderTimeoutSeconds'
    ];

    if (type === 'checkbox') {
      processedValue = checked;
    } else if (numericTextFields.includes(name)) {
      const isValidNumericString = (val) => {
        if (val === '') return true;
        // Allow just a minus sign for fields that can be negative
        if ((name === 'rsiThresholdDown' || name === 'stopLossUSDT') && val === '-') return true;
        // Regex to allow optional leading minus, digits, optional single decimal point, and more digits
        return /^-?\d*\.?\d*$/.test(val) && (val.match(/\./g) || []).length <= 1 && (val.match(/-/g) || []).length <= (val.startsWith('-') ? 1: 0) ;
      };

      if (isValidNumericString(value)) {
        // Removed the specific block preventing '-' for stopLossUSDT
        processedValue = value;
      } else {
        // If not valid, revert to the current value in state for that field
        processedValue = config[name]; 
      }
    } else if (integerNumberFields.includes(name)) {
      if (value === '') {
        processedValue = ''; 
      } else {
        const num = Number(value);
        processedValue = Number.isInteger(num) ? num : config[name]; 
        if (name === 'cycleSleepSeconds' && num !== 0 && num < 5 && value !== '') {
             processedValue = num;
        }
      }
    }

    setConfig(prevConfig => ({
      ...prevConfig,
      [name]: processedValue,
    }));
  };

  // --- Manejador para enviar el formulario a la API ---
  const handleSubmit = async (e) => {
    e.preventDefault();
    setMessage('Guardando configuración...');

    // Limpiar y validar la lista de símbolos antes de enviar
    const symbolsRaw = config.symbolsToTrade || '';
    const symbolsList = symbolsRaw.split(',').map(s => s.trim().toUpperCase()).filter(s => s);
    const cleanSymbolsString = symbolsList.join(',');

    // Crear el objeto a enviar, asegurando tipos correctos usando parseValue
    const configToSend = {
      apiKey: config.apiKey,
      apiSecret: config.apiSecret,
      mode: config.mode,
      symbolsToTrade: cleanSymbolsString,
      rsiInterval: config.rsiInterval,
      rsiPeriod: parseValue(config.rsiPeriod, initialConfig.rsiPeriod, 'rsiPeriod'),
      rsiThresholdUp: parseValue(config.rsiThresholdUp, initialConfig.rsiThresholdUp, 'rsiThresholdUp'),
      rsiThresholdDown: parseValue(config.rsiThresholdDown, initialConfig.rsiThresholdDown, 'rsiThresholdDown'),
      rsiEntryLevelLow: parseValue(config.rsiEntryLevelLow, initialConfig.rsiEntryLevelLow, 'rsiEntryLevelLow'),
      volumeSmaPeriod: parseValue(config.volumeSmaPeriod, initialConfig.volumeSmaPeriod, 'volumeSmaPeriod'),
      volumeFactor: parseValue(config.volumeFactor, initialConfig.volumeFactor, 'volumeFactor'),
      positionSizeUSDT: parseValue(config.positionSizeUSDT, initialConfig.positionSizeUSDT, 'positionSizeUSDT'),
      stopLossUSDT: parseValue(config.stopLossUSDT, initialConfig.stopLossUSDT, 'stopLossUSDT'),
      takeProfitUSDT: parseValue(config.takeProfitUSDT, initialConfig.takeProfitUSDT, 'takeProfitUSDT'),
      cycleSleepSeconds: parseValue(config.cycleSleepSeconds, initialConfig.cycleSleepSeconds, 'cycleSleepSeconds'),
      orderTimeoutSeconds: parseValue(config.orderTimeoutSeconds, initialConfig.orderTimeoutSeconds, 'orderTimeoutSeconds'),
    };
    
    // Actualizar el estado local con los valores limpios/parseados (bueno para UI consistency)
    setConfig(prev => ({ 
        ...prev,
        ...configToSend,
        symbolsToTrade: cleanSymbolsString,
        active: prev.active
    }));

    try {
      const response = await fetch('http://localhost:5001/api/config', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(configToSend),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(`Error HTTP ${response.status}: ${response.statusText} - ${errorData.error || 'Error desconocido'}`);
      }

      const result = await response.json();
      setMessage(result.message || '¡Configuración guardada con éxito!');
      console.log('Respuesta del backend:', result);

    } catch (error) {
      console.error('Error al guardar la configuración:', error);
      setMessage(`Error al guardar: ${error.message}`);
    } finally {
         setTimeout(() => setMessage(''), 5000);
    }
  };

  // Clases reutilizables de Tailwind para los inputs y labels
  const labelClass = "block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1";
  const inputBaseClass = "mt-1 block w-full px-3 py-2 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm text-gray-900 dark:text-gray-100";
  const inputClass = `${inputBaseClass}`;
  const inputNumberClass = `${inputBaseClass} [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none`; // Oculta flechas en input number
  const selectClass = inputBaseClass;
  const textareaClass = `${inputBaseClass} min-h-[60px]`;

  return (
    // Añadir un div para mostrar mensajes de estado/error
    <>
      {message && (
        <div className={`mb-4 p-3 rounded text-center ${message.startsWith('Error') ? 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200' : 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200'}`}>
          {message}
        </div>
      )}
      {isLoading && (
         <div className="mb-4 p-3 rounded text-center bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-200">
            Cargando configuración inicial...
         </div>
      )}

      {/* Contenedor del formulario principal (deshabilitar si está cargando) */}
      <form onSubmit={handleSubmit} className={`space-y-6 p-6 bg-white dark:bg-gray-800 shadow-lg rounded-lg border border-gray-200 dark:border-gray-700 ${isLoading ? 'opacity-50 pointer-events-none' : ''}`}>

        {/* Sección API Keys */}
        <fieldset className="border pt-4 px-4 pb-6 rounded-md border-gray-300 dark:border-gray-600">
            <legend className="text-base font-medium text-gray-900 dark:text-gray-100 px-2">Credenciales API</legend>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-4">
              <div>
                <label htmlFor="apiKey" className={labelClass}>
                  API Key <span className="text-red-500">*</span>
                </label>
                <input
                  type="password"
                  name="apiKey"
                  id="apiKey"
                  value={config.apiKey}
                  onChange={handleChange}
                  className={inputClass}
                  required
                  autoComplete="new-password"
                />
              </div>
              <div>
                <label htmlFor="apiSecret" className={labelClass}>
                  API Secret <span className="text-red-500">*</span>
                </label>
                <input
                  type="password"
                  name="apiSecret"
                  id="apiSecret"
                  value={config.apiSecret}
                  onChange={handleChange}
                  className={inputClass}
                  required
                  autoComplete="new-password"
                />
              </div>
            </div>
        </fieldset>

        {/* Sección Configuración Trading */}
        <fieldset className="border pt-4 px-4 pb-6 rounded-md border-gray-300 dark:border-gray-600">
            <legend className="text-base font-medium text-gray-900 dark:text-gray-100 px-2">Configuración General Trading</legend>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-4">
              <div className="md:col-span-2">
                <label htmlFor="symbolsToTrade" className={labelClass}>
                  Símbolos a Operar (separados por coma) <span className="text-red-500">*</span>
                </label>
                <textarea
                  name="symbolsToTrade"
                  id="symbolsToTrade"
                  value={config.symbolsToTrade}
                  onChange={handleChange}
                  className={textareaClass}
                  required
                  placeholder="Ej: ETHUSDT,ADAUSDT,DOTUSDT,SOLUSDT"
                  rows={2}
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Lista de pares de futuros a operar. Asegúrate que sean válidos.</p>
              </div>
              <div>
                <label htmlFor="rsiInterval" className={labelClass}>
                  Intervalo RSI (para todos) <span className="text-red-500">*</span>
                </label>
                <select
                  name="rsiInterval"
                  id="rsiInterval"
                  value={config.rsiInterval}
                  onChange={handleChange}
                  className={selectClass}
                  required
                >
                  {['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d'].map(interval => (
                    <option key={interval} value={interval}>{interval}</option>
                  ))}
                </select>
              </div>
            </div>
        </fieldset>

        {/* Sección Parámetros RSI */}
        <fieldset className="border pt-4 px-4 pb-6 rounded-md border-gray-300 dark:border-gray-600">
            <legend className="text-base font-medium text-gray-900 dark:text-gray-100 px-2">Parámetros RSI y Volumen (Compartidos)</legend>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-4"> 
              <div>
                <label htmlFor="rsiPeriod" className={labelClass}>
                  Periodo RSI <span className="text-red-500">*</span>
                </label>
                <input
                  type="number"
                  name="rsiPeriod"
                  id="rsiPeriod"
                  value={config.rsiPeriod}
                  onChange={handleChange}
                  className={inputNumberClass}
                  required
                  min="2"
                  step="1"
                  placeholder="Ej: 14"
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Número de velas para calcular RSI.</p>
              </div>
              <div>
                <label htmlFor="rsiThresholdUp" className={labelClass}>
                  RSI Subida <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  inputMode="decimal"
                  name="rsiThresholdUp"
                  id="rsiThresholdUp"
                  value={config.rsiThresholdUp}
                  onChange={handleChange}
                  className={inputClass}
                  required
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Cambio positivo de RSI para entrar.</p>
              </div>
              <div>
                <label htmlFor="rsiThresholdDown" className={labelClass}>
                  RSI Bajada <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  inputMode="decimal"
                  name="rsiThresholdDown"
                  id="rsiThresholdDown"
                  value={config.rsiThresholdDown}
                  onChange={handleChange}
                  className={inputClass}
                  required
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Cambio negativo de RSI para salir.</p>
              </div>
              <div>
                <label htmlFor="rsiEntryLevelLow" className={labelClass}>
                  RSI Entry
                </label>
                <input
                  type="text"
                  inputMode="decimal"
                  name="rsiEntryLevelLow"
                  id="rsiEntryLevelLow"
                  value={config.rsiEntryLevelLow}
                  onChange={handleChange}
                  className={inputClass}
                  step="0.1"
                  placeholder="e.g., 25"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Entrar si RSI está bajo este valor (y cambio RSI OK).</p>
              </div>
              <div>
                <label htmlFor="volumeSmaPeriod" className={labelClass}>
                  Periodo SMA Volumen
                </label>
                <input
                  type="number"
                  name="volumeSmaPeriod"
                  id="volumeSmaPeriod"
                  value={config.volumeSmaPeriod}
                  onChange={handleChange}
                  className={inputNumberClass}
                  min="1"
                  step="1"
                  placeholder="Ej: 20"
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Velas para calcular volumen promedio.</p>
              </div>
              <div>
                <label htmlFor="volumeFactor" className={labelClass}>
                  Factor Volumen
                </label>
                <input
                  type="text"
                  inputMode="decimal"
                  name="volumeFactor"
                  id="volumeFactor"
                  value={config.volumeFactor}
                  onChange={handleChange}
                  className={inputClass}
                  min="0.01"
                  step="0.01"
                  placeholder="Ej: 1.5"
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Volumen actual {'>'} SMA * Factor (Ej: 1.5 = +50%).</p>
              </div>
            </div>
        </fieldset>

        {/* Sección Gestión de Riesgo */}
        <fieldset className="border pt-4 px-4 pb-6 rounded-md border-gray-300 dark:border-gray-600">
            <legend className="text-base font-medium text-gray-900 dark:text-gray-100 px-2">Gestión de Riesgo (Compartida)</legend>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-4">
              <div>
                <label htmlFor="positionSizeUSDT" className={labelClass}>
                  Tamaño Posición (USDT) <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  inputMode="decimal"
                  name="positionSizeUSDT"
                  id="positionSizeUSDT"
                  value={config.positionSizeUSDT}
                  onChange={handleChange}
                  className={inputClass}
                  required
                  min="1"
                  step="any"
                  placeholder="Ej: 50"
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Cantidad en USDT por operación.</p>
              </div>
              <div>
                <label htmlFor="stopLossUSDT" className={labelClass}>
                  Stop Loss (USDT)
                </label>
                <input
                  type="text"
                  inputMode="decimal"
                  name="stopLossUSDT"
                  id="stopLossUSDT"
                  value={config.stopLossUSDT}
                  onChange={handleChange}
                  className={inputClass}
                  placeholder="Ej: 20 (o 0)"
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Pérdida máx. (negativo o 0). 0 o vacío = deshabilitado.</p>
              </div>
               <div>
                <label htmlFor="takeProfitUSDT" className={labelClass}>
                  Take Profit (USDT)
                </label>
                <input
                  type="text"
                  inputMode="decimal"
                  name="takeProfitUSDT"
                  id="takeProfitUSDT"
                  value={config.takeProfitUSDT}
                  onChange={handleChange}
                  className={inputClass}
                  min="0"
                  step="any"
                  placeholder="Ej: 30 (o 0)"
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Ganancia a la que cerrar (positivo o 0). 0 o vacío = deshabilitado.</p>
              </div>
            </div>
        </fieldset>

        {/* Sección Control del Bot */}
        <fieldset className="border pt-4 px-4 pb-6 rounded-md border-gray-300 dark:border-gray-600">
            <legend className="text-base font-medium text-gray-900 dark:text-gray-100 px-2">Control del Bot</legend>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-4">
              <div>
                <label htmlFor="mode" className={labelClass}>
                  Modo de Operación <span className="text-red-500">*</span>
                </label>
                <select
                  name="mode"
                  id="mode"
                  value={config.mode}
                  onChange={handleChange}
                  className={selectClass}
                  required
                >
                  <option value="paper">Paper Trading (Simulado)</option>
                  <option value="live">Live Trading (Real)</option>
                </select>
                <p className={`mt-1 text-xs ${config.mode === 'live' ? 'text-red-600 dark:text-red-400 font-bold' : 'text-gray-500 dark:text-gray-400'}`}>
                  {config.mode === 'live' ? '¡CUIDADO! Operaciones con dinero real.' : 'Operaciones simuladas sin riesgo.'}
                </p>
              </div>
              <div>
                <label htmlFor="cycleSleepSeconds" className={labelClass}>
                  Espera entre Ciclos (seg)
                </label>
                <input
                  type="number"
                  name="cycleSleepSeconds"
                  id="cycleSleepSeconds"
                  value={config.cycleSleepSeconds}
                  onChange={handleChange}
                  className={inputNumberClass}
                  min="0"
                  step="1"
                  placeholder="Ej: 10 (0 = auto)"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Segundos entre ciclos. 0 = Auto (basado en intervalo RSI, mín 60s).</p>
              </div>
              <div>
                <label htmlFor="orderTimeoutSeconds" className={labelClass}>
                  Timeout (seg)
                </label>
                <input
                  type="number"
                  name="orderTimeoutSeconds"
                  id="orderTimeoutSeconds"
                  value={config.orderTimeoutSeconds}
                  onChange={handleChange}
                  className={inputNumberClass}
                  min="0"
                  step="1"
                  placeholder="Ej: 60"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Segundos a esperar antes de cancelar una orden LIMIT no completada (0 = sin timeout).</p>
              </div>
              <div className="flex items-center justify-start pt-5">
                  <input
                    id="active"
                    name="active"
                    type="checkbox"
                    checked={config.active}
                    onChange={handleChange}
                    className="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded mr-2"
                  />
                  <label htmlFor="active" className={labelClass + " mb-0"}>
                    Activar Bot (Experimental)
                  </label>
              </div>
            </div>
        </fieldset>

        {/* Botón de Envío */}
        <div className="pt-2">
          <button
            type="submit"
            // Deshabilitar botón mientras se guarda
            disabled={isLoading || message.includes('Guardando...')}
            className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {message.includes('Guardando...') ? 'Guardando...' : 'Guardar Configuración'} {/* Cambiar texto del botón */}
          </button>
        </div>

      </form>
    </>
  );
}

export default ConfigForm; 