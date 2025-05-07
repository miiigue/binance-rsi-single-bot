import React, { useState, useEffect } from 'react';
import ConfigForm from './ConfigForm'; // Importa el componente del formulario
import StatusDisplay from './StatusDisplay'; // <-- Importar el nuevo componente
import './index.css'; // Importar el archivo CSS principal existente

function App() {
  const [config, setConfig] = useState(null); // Estado para la configuración
  const [botsRunning, setBotsRunning] = useState(null); // null: desconocido, true: corriendo, false: detenidos
  const [initialLoadingError, setInitialLoadingError] = useState(null); // Para errores de carga inicial

  // Efecto para la carga inicial de configuración y estado
  useEffect(() => {
    const fetchInitialData = async () => {
        setInitialLoadingError(null); // Resetear error
        try {
            // Intentar obtener la configuración primero
            const configResponse = await fetch('/api/config');
            if (!configResponse.ok) {
                throw new Error(`Error al cargar configuración: ${configResponse.status}`);
            }
            const configData = await configResponse.json();
            // Aplanar configuración como antes...
            const symbolsString = configData.SYMBOLS?.symbols_to_trade || ''; 
            const flatConfig = {
                 apiKey: configData.BINANCE?.api_key || '',
                 apiSecret: configData.BINANCE?.api_secret || '',
                 mode: configData.BINANCE?.mode || 'paper',
                 rsiInterval: configData.TRADING?.rsi_interval || '1m',
                 rsiPeriod: configData.TRADING?.rsi_period || 14,
                 rsiThresholdUp: configData.TRADING?.rsi_threshold_up || 1.5,
                 rsiThresholdDown: configData.TRADING?.rsi_threshold_down || -1.0,
                 rsiEntryLevelLow: configData.TRADING?.rsi_entry_level_low || 30,
                 positionSizeUSDT: configData.TRADING?.position_size_usdt || 50,
                 stopLossUSDT: configData.TRADING?.stop_loss_usdt || 0,
                 takeProfitUSDT: configData.TRADING?.take_profit_usdt || 0,
                 cycleSleepSeconds: configData.TRADING?.cycle_sleep_seconds || 60,
                 volumeSmaPeriod: configData.TRADING?.volume_sma_period || 20,
                 volumeFactor: configData.TRADING?.volume_factor || 1.5,
                 orderTimeoutSeconds: configData.TRADING?.order_timeout_seconds || 60,
                 symbolsToTrade: symbolsString 
            };
            setConfig(flatConfig);
            console.log("Configuración inicial cargada.", flatConfig);

            // Ahora, obtener el estado general (que incluye si los bots están corriendo)
            const statusResponse = await fetch('/api/status');
            if (!statusResponse.ok) {
                 // Si la config cargó pero el estado falla, aún podemos mostrar config
                 console.warn("Configuración cargada, pero falló la carga inicial del estado de los bots.");
                 setBotsRunning(false); // Asumir que no corren si el estado falla
                 // No lanzar error aquí para permitir que ConfigForm se muestre
            } else {
                const statusData = await statusResponse.json();
                setBotsRunning(statusData.bots_running); // Establecer estado basado en la respuesta
                console.log("Estado inicial de bots cargado. Corriendo:", statusData.bots_running);
            }
            
        } catch (error) {
            console.error("Error crítico durante la carga inicial:", error);
            setInitialLoadingError(`Error al cargar datos iniciales: ${error.message}. Intenta recargar o revisa el servidor.`);
            setConfig(null); // No mostrar config si hay error crítico
            setBotsRunning(false); // Asumir que no corren
        }
    };

    fetchInitialData();
}, []);

  const handleSave = (newConfig) => {
    console.log('Sending updated config to API:', newConfig);

    // Devolver una promesa para que se pueda esperar si es necesario
    return fetch('/api/config', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(newConfig),
    })
    .then(response => {
        if (!response.ok) {
          return response.json().then(errData => {
              throw new Error(errData.error || `HTTP error! status: ${response.status}`);
          });
        }
        return response.json();
    })
    .then(data => {
        console.log('API response after save:', data);
        alert(data.message || 'Configuration saved! Podría requerir reiniciar los bots para aplicar todos los cambios.');
        // Recargar la config después de guardar para asegurar consistencia?
        // Podría ser buena idea, o simplemente informar al usuario.
        // fetchInitialData(); // Opcional: Recargar todo
        return true; // Indicar éxito
    })
    .catch(error => {
        console.error('Error saving configuration:', error);
        alert(`Error saving configuration: ${error.message}`);
        return false; // Indicar fallo
    });
  };
  
  // --- Funciones para INICIAR y DETENER bots --- 
  const handleStartBots = async () => {
    try {
      const response = await fetch('/api/start_bots', { method: 'POST' });
      const data = await response.json(); // Intentar leer JSON siempre
      if (!response.ok) {
        throw new Error(data.error || `Error HTTP ${response.status}`);
      }
      console.log("Start bots response:", data);
      setBotsRunning(true); // Actualizar estado local
      return true; // Éxito
    } catch (error) {
      console.error('Error starting bots:', error);
      // El mensaje de error se maneja en BotControls
      setBotsRunning(false); // Asegurarse de que el estado refleje el fallo
      return false; // Fallo
    }
  };

  const handleShutdown = async () => {
    try {
      const response = await fetch('/api/shutdown', { method: 'POST' });
      const data = await response.json(); // Intentar leer JSON siempre
       if (!response.ok) {
        // Incluso si falla, asumimos que el intento de apagar significa que ya no corren
        console.warn("Respuesta no OK de shutdown, pero actualizando UI a no corriendo.");
        // throw new Error(data.message || `Error HTTP ${response.status}`); // Opcional: lanzar error
      }
      console.log('Shutdown API response:', data);
      setBotsRunning(false); // Actualizar estado local
      return true; // Considerar éxito para la UI incluso si hubo error leve
    } catch (error) {
      console.error('Error sending shutdown signal:', error);
      // El mensaje de error se maneja en BotControls
       setBotsRunning(false); // Asegurarse de que el estado refleje el fallo
      return false; // Fallo
    }
  };
  // ------------------------------------------

  return (
    <div className="min-h-screen bg-gray-100 dark:bg-gray-900 text-gray-900 dark:text-gray-100">
      <div className="container mx-auto p-4 md:p-8 max-w-5xl">
        <header className="mb-8">
          <h1 className="text-3xl font-bold text-blue-600 dark:text-blue-400 text-center">Trading Bot Dashboard</h1>
        </header>

        {/* Mostrar error de carga inicial si existe */} 
        {initialLoadingError && (
          <div className="mb-6 p-4 bg-red-100 dark:bg-red-900 border border-red-400 dark:border-red-700 text-red-700 dark:text-red-200 rounded-lg">
             <p className="font-semibold text-center">Error de Carga</p>
             <p className="text-center">{initialLoadingError}</p>
           </div>
        )}

        {/* Solo mostrar controles y status si no hubo error crítico inicial */} 
        {!initialLoadingError && (
          <>
             {/* La sección BotControls fue movida a StatusDisplay */}
             {/* Asegurarse que no queden restos aquí */}

            {/* -- Sección de Configuración -- */}
            {config ? (
                <ConfigForm initialConfig={config} onSave={handleSave} />
            ) : (
                <p className="text-center">(Loading configuration...)</p>
            )}

            {/* -- Sección de Estado (sin cambios, ya pasa props) -- */}
            <StatusDisplay 
                botsRunning={botsRunning} 
                onStart={handleStartBots} 
                onShutdown={handleShutdown} 
            /> 
          </>
        )}
      </div>
    </div>
  );
}

export default App; 