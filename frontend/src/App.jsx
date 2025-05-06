import React, { useState, useEffect } from 'react';
import ConfigForm from './ConfigForm'; // Importa el componente del formulario
import StatusDisplay from './StatusDisplay'; // <-- Importar el nuevo componente
import './index.css'; // Importar el archivo CSS principal existente

function App() {
  const [config, setConfig] = useState(null); // Estado para la configuración
  // const [theme, setTheme] = useState(localStorage.getItem('theme') || 'light'); // REMOVED theme state

  // useEffect para cargar la configuración inicial desde la API
  useEffect(() => {
    const apiUrl = '/api/config'; // La URL del endpoint GET de tu API Flask

    console.log("App component mounted - Fetching initial config from", apiUrl);

    fetch(apiUrl)
      .then(response => {
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        return response.json();
      })
      .then(data => {
        console.log("Config received from API:", data);
        // Aquí asumimos que la API devuelve un objeto con secciones 
        // como 'BINANCE', 'TRADING', 'SYMBOLS'.
        // El componente ConfigForm probablemente espere una estructura plana.
        // Vamos a aplanar la estructura necesaria para ConfigForm.
        
        // Extraer símbolos
        const symbolsString = data.SYMBOLS?.symbols_to_trade || ''; 
        
        // Combinar parámetros de BINANCE y TRADING (y otros si existieran)
        // Mapeando a las claves que espera ConfigForm (ej: apiKey, rsiInterval...)
        // Usaremos un mapeo inverso o crearemos el objeto plano directamente
        const flatConfig = {
            apiKey: data.BINANCE?.api_key || '',
            apiSecret: data.BINANCE?.api_secret || '', // Ten cuidado al exponer secretos
            mode: data.BINANCE?.mode || 'paper',
            rsiInterval: data.TRADING?.rsi_interval || '1m',
            rsiPeriod: data.TRADING?.rsi_period || 14,
            rsiThresholdUp: data.TRADING?.rsi_threshold_up || 1.5,
            rsiThresholdDown: data.TRADING?.rsi_threshold_down || -1.0,
            rsiEntryLevelLow: data.TRADING?.rsi_entry_level_low || 30,
            positionSizeUSDT: data.TRADING?.position_size_usdt || 50,
            stopLossUSDT: data.TRADING?.stop_loss_usdt || 0,
            takeProfitUSDT: data.TRADING?.take_profit_usdt || 0,
            cycleSleepSeconds: data.TRADING?.cycle_sleep_seconds || 60,
            volumeSmaPeriod: data.TRADING?.volume_sma_period || 20,
            volumeFactor: data.TRADING?.volume_factor || 1.5,
            orderTimeoutSeconds: data.TRADING?.order_timeout_seconds || 60,
            // Añadir la clave para los símbolos
            symbolsToTrade: symbolsString 
        };
        
        console.log("Flattened config for state:", flatConfig);
        setConfig(flatConfig); // Guarda la configuración APLANADA en el estado
      })
      .catch(error => {
        console.error("Error fetching initial configuration:", error);
        // Podrías poner una configuración por defecto o mostrar un error
        // setConfig({ ...defaultConfig, error: "Failed to load config" });
      });

  }, []); // Array vacío para ejecutar solo al montar

  // REMOVED useEffect for theme handling

  // REMOVED toggleTheme function placeholder

  // Function to handle saving config
  const handleSave = (newConfig) => {
    const apiUrl = '/api/config'; // La URL del endpoint POST
    console.log('Sending updated config to API:', newConfig);

    fetch(apiUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(newConfig), // Enviar el objeto plano directamente
    })
    .then(response => {
        if (!response.ok) {
          // Si hay error, intentar leer el mensaje de error del JSON de la API
          return response.json().then(errData => {
              throw new Error(errData.error || `HTTP error! status: ${response.status}`);
          });
        }
        return response.json();
    })
    .then(data => {
        console.log('API response after save:', data);
        // Opcional: Mostrar mensaje de éxito al usuario
        alert(data.message || 'Configuration saved!');
        // Opcional: Actualizar el estado local si es necesario (aunque recargar puede ser más simple)
        // setConfig(newConfig); 
    })
    .catch(error => {
        console.error('Error saving configuration:', error);
        // Mostrar mensaje de error al usuario
        alert(`Error saving configuration: ${error.message}`);
    });
  };
  
  return (
    // REMOVED dynamic theme classes from root div
    <div className="min-h-screen"> 
      {/* Applied base background and text colors directly */}
      <div className="bg-gray-100 text-gray-900 p-4 md:p-8">
        <div className="max-w-4xl mx-auto">
          {/* REMOVED flex justify-between from header as button is gone */}
          <header className="items-center mb-8">
            {/* Centered the title slightly for balance */}
            <h1 className="text-3xl font-bold text-blue-600 text-center">Trading Bot Dashboard</h1>
            {/* REMOVED Theme toggle button */}
          </header>

          {/* -- Sección de Configuración -- */}
          {config ? (
            <ConfigForm initialConfig={config} onSave={handleSave} />
          ) : (
            <p className="text-center">(Loading configuration...)</p>
          )}

          {/* -- NUEVA Sección de Estado -- */}
          <StatusDisplay /> 
          {/* -------------------------- */}

        </div> 
      </div>
    </div>
  );
}

export default App; 