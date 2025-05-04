import React, { useState, useEffect } from 'react';

// Valores iniciales o por defecto para el formulario
const initialConfig = {
  apiKey: '',
  apiSecret: '',
  symbol: 'BTCUSDT',
  rsiInterval: '5m',
  rsiPeriod: 14,
  rsiThresholdUp: 8,
  rsiThresholdDown: -8,
  rsiEntryLevelLow: 25,
  positionSizeUSDT: 50,
  stopLossUSDT: -0.3,
  takeProfitUSDT: 0,
  cycleSleepSeconds: 0,
  mode: 'paper',
  active: false,
};

// Helper function to parse potential numbers from config
const parseValue = (value) => {
  if (value === '' || value === '-') {
    return value;
  }
  const num = Number(value);
  if (!isNaN(num)) {
    if (value === 'stopLossUSDT' && num > 0) {
      console.warn("Stop Loss debe ser negativo o cero.");
    }
    if (value === 'takeProfitUSDT' && num < 0) {
      console.warn("Take Profit debe ser positivo o cero.");
    }
    if (value === 'rsiPeriod' && (!Number.isInteger(num) || num <= 0)) {
      console.warn("RSI Period debe ser un entero positivo.");
    }
    if (value === 'cycleSleepSeconds' && num !== 0 && (num < 5 || !Number.isInteger(num))) {
      console.warn("Tiempo de espera debe ser 0 (auto) o un entero >= 5 segundos.");
    }
    return num;
  }
  return value;
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
          symbol: backendConfig.TRADING?.symbol || 'BTCUSDT',
          rsiInterval: backendConfig.TRADING?.rsi_interval || '5m',
          rsiPeriod: parseValue(backendConfig.TRADING?.rsi_period, 14),
          rsiThresholdUp: parseValue(backendConfig.TRADING?.rsi_threshold_up, 8),
          rsiThresholdDown: parseValue(backendConfig.TRADING?.rsi_threshold_down, -8),
          rsiEntryLevelLow: parseValue(backendConfig.TRADING?.rsi_entry_level_low, 25),
          positionSizeUSDT: parseValue(backendConfig.TRADING?.position_size_usdt, 50),
          stopLossUSDT: parseValue(backendConfig.TRADING?.stop_loss_usdt, -0.3),
          takeProfitUSDT: parseValue(backendConfig.TRADING?.take_profit_usdt, 0),
          cycleSleepSeconds: parseValue(backendConfig.TRADING?.cycle_sleep_seconds, 0),
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

    // Procesamiento especial para campos numéricos
    if (type === 'number' || ['rsiPeriod', 'takeProfitUSDT', 'stopLossUSDT', 'positionSizeUSDT', 'rsiThresholdUp', 'rsiThresholdDown', 'cycleSleepSeconds'].includes(name)) {
      if (value === '' || (value === '-' && name === 'stopLossUSDT')) {
        processedValue = value;
      } else {
        const num = Number(value);
        if (!isNaN(num)) {
          if (name === 'stopLossUSDT' && num > 0) {
             console.warn("Stop Loss debe ser negativo o cero.");
          }
          if (name === 'takeProfitUSDT' && num < 0) {
              console.warn("Take Profit debe ser positivo o cero.");
          }
           if (name === 'rsiPeriod' && (!Number.isInteger(num) || num <= 0)) {
              console.warn("RSI Period debe ser un entero positivo.");
          }
          if (name === 'cycleSleepSeconds' && num !== 0 && (num < 5 || !Number.isInteger(num))) {
            console.warn("Tiempo de espera debe ser 0 (auto) o un entero >= 5 segundos.");
          }
          processedValue = num;
        } else {
          return;
        }
      }
    }
     else if (type === 'checkbox') {
      processedValue = checked;
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

    // Validar y ajustar valores antes de enviar
    let sleepSecondsToSend = 0;
    if (config.cycleSleepSeconds !== '' && !isNaN(Number(config.cycleSleepSeconds))) {
        const sleepNum = Number(config.cycleSleepSeconds);
        if (Number.isInteger(sleepNum) && sleepNum >= 5) {
            sleepSecondsToSend = sleepNum;
        } else if (sleepNum !== 0) {
             console.warn(`Valor inválido para Tiempo de Espera (${config.cycleSleepSeconds}). Se usará cálculo automático (0).`);
             // Dejar sleepSecondsToSend en 0
        }
         // Si es 0, también se queda en 0
    } // Si está vacío o no es número, también se queda en 0

    const configToSend = {
        ...config,
        rsiPeriod: Number.isInteger(config.rsiPeriod) && config.rsiPeriod > 0 ? config.rsiPeriod : 14,
        stopLossUSDT: config.stopLossUSDT <= 0 ? config.stopLossUSDT : 0,
        takeProfitUSDT: config.takeProfitUSDT >= 0 ? config.takeProfitUSDT : 0,
        cycleSleepSeconds: sleepSecondsToSend
    };
    setConfig(configToSend);

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
  const inputBaseClass = "mt-1 block w-full px-3 py-2 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm";
  const inputClass = `${inputBaseClass}`;
  const inputNumberClass = `${inputBaseClass} [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none`; // Oculta flechas en input number
  const selectClass = inputBaseClass;

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
            <legend className="text-base font-medium text-gray-900 dark:text-gray-100 px-2">Configuración Trading</legend>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-4">
              <div>
                <label htmlFor="symbol" className={labelClass}>
                  Par de Trading <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  name="symbol"
                  id="symbol"
                  value={config.symbol}
                  onChange={handleChange}
                  className={inputClass}
                  required
                  placeholder="Ej: BTCUSDT"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Asegúrate que sea un par de futuros válido.</p>
              </div>
              <div>
                <label htmlFor="rsiInterval" className={labelClass}>
                  Intervalo RSI <span className="text-red-500">*</span>
                </label>
                <select
                  name="rsiInterval"
                  id="rsiInterval"
                  value={config.rsiInterval}
                  onChange={handleChange}
                  className={selectClass}
                  required
                >
                  <option value="1m">1 minuto</option>
                  <option value="3m">3 minutos</option>
                  <option value="5m">5 minutos</option>
                  <option value="15m">15 minutos</option>
                  <option value="30m">30 minutos</option>
                  <option value="1h">1 hora</option>
                  <option value="2h">2 horas</option>
                  <option value="4h">4 horas</option>
                  {/* Añadir más intervalos si es necesario */}
                </select>
              </div>
            </div>
        </fieldset>

        {/* Sección Parámetros RSI */}
        <fieldset className="border pt-4 px-4 pb-6 rounded-md border-gray-300 dark:border-gray-600">
            <legend className="text-base font-medium text-gray-900 dark:text-gray-100 px-2">Parámetros RSI</legend>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mt-4"> 
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
                  type="number"
                  name="rsiThresholdUp"
                  id="rsiThresholdUp"
                  value={config.rsiThresholdUp}
                  onChange={handleChange}
                  className={inputNumberClass}
                  required
                  step="any"
                  placeholder="Ej: 8"
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Cambio positivo de RSI para entrar.</p>
              </div>
              <div>
                <label htmlFor="rsiThresholdDown" className={labelClass}>
                  RSI Bajada <span className="text-red-500">*</span>
                </label>
                <input
                  type="number"
                  name="rsiThresholdDown"
                  id="rsiThresholdDown"
                  value={config.rsiThresholdDown}
                  onChange={handleChange}
                  className={inputNumberClass}
                  required
                  step="any"
                  placeholder="Ej: -8"
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Cambio negativo de RSI para salir.</p>
              </div>
              <div>
                <label htmlFor="rsiEntryLevelLow" className={labelClass}>
                  RSI Entry
                </label>
                <input
                  type="number"
                  name="rsiEntryLevelLow"
                  id="rsiEntryLevelLow"
                  value={config.rsiEntryLevelLow}
                  onChange={handleChange}
                  className={inputNumberClass}
                  step="0.1"
                  placeholder="e.g., 25"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Entrar si RSI está bajo este valor (y cambio RSI OK).</p>
              </div>
            </div>
        </fieldset>

        {/* Sección Gestión de Riesgo */}
        <fieldset className="border pt-4 px-4 pb-6 rounded-md border-gray-300 dark:border-gray-600">
            <legend className="text-base font-medium text-gray-900 dark:text-gray-100 px-2">Gestión de Riesgo</legend>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-4">
              <div>
                <label htmlFor="positionSizeUSDT" className={labelClass}>
                  Tamaño Posición (USDT) <span className="text-red-500">*</span>
                </label>
                <input
                  type="number"
                  name="positionSizeUSDT"
                  id="positionSizeUSDT"
                  value={config.positionSizeUSDT}
                  onChange={handleChange}
                  className={inputNumberClass}
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
                  name="stopLossUSDT"
                  id="stopLossUSDT"
                  value={config.stopLossUSDT}
                  onChange={handleChange}
                  className={inputClass}
                  placeholder="Ej: -0.3 (o 0)"
                />
                 <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Pérdida máx. (negativo o 0). 0 o vacío = deshabilitado.</p>
              </div>
               <div>
                <label htmlFor="takeProfitUSDT" className={labelClass}>
                  Take Profit (USDT)
                </label>
                <input
                  type="number"
                  name="takeProfitUSDT"
                  id="takeProfitUSDT"
                  value={config.takeProfitUSDT}
                  onChange={handleChange}
                  className={inputNumberClass}
                  min="0"
                  step="any"
                  placeholder="Ej: 5 (o 0)"
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