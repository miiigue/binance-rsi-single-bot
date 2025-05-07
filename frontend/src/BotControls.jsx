import React, { useState } from 'react';

// --- Componente para los botones de control (sin título) ---
function BotControls({ botsRunning, onStart, onShutdown }) {
  const [isActionPending, setIsActionPending] = useState(false);
  const [actionMessage, setActionMessage] = useState('');

  const handleStartClick = async () => {
    setIsActionPending(true);
    setActionMessage('Iniciando bots...');
    const success = await onStart(); // Llama a la función pasada por props
    if (!success) {
      setActionMessage('Error al iniciar bots. Revisa los logs del servidor.');
      // Mantener el mensaje de error por un tiempo
      setTimeout(() => setActionMessage(''), 5000);
    } else {
       setActionMessage(''); // Limpiar mensaje en éxito
    }
    setIsActionPending(false);
  };

  const handleShutdownClick = async () => {
    if (!window.confirm("¿Estás seguro de que deseas apagar todos los bots?")) return;
    setIsActionPending(true);
    setActionMessage('Enviando señal de apagado...');
    const success = await onShutdown(); // Llama a la función pasada por props
    if (!success) {
      setActionMessage('Error al enviar señal de apagado. Revisa los logs.');
       setTimeout(() => setActionMessage(''), 5000);
    } else {
      setActionMessage('Apagado solicitado.'); // Mensaje temporal
      setTimeout(() => setActionMessage(''), 3000);
    }
    setIsActionPending(false);
  };

  // Determinar el estado de los botones
  const startDisabled = botsRunning === null || botsRunning === true || isActionPending;
  const shutdownDisabled = botsRunning === null || botsRunning === false || isActionPending;

  // Ajustar el div exterior para que no tenga su propio fondo/sombra si se prefiere
  return (
    <div className="mb-4"> {/* Quitado p-4, bg-*, rounded, shadow */}
      {/* <h3 className="text-lg font-semibold mb-3 text-center text-gray-800 dark:text-gray-200">Control de Bots</h3> */} {/* TÍTULO ELIMINADO */}
      <div className="flex justify-center space-x-4">
        <button
          onClick={handleStartClick}
          disabled={startDisabled}
          className={`px-5 py-2 font-semibold rounded-md text-white transition-colors duration-150 ease-in-out 
            ${startDisabled 
              ? 'bg-gray-400 dark:bg-gray-600 cursor-not-allowed' 
              : 'bg-green-600 hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-green-500 focus:ring-offset-2'}
          `}
        >
          {isActionPending && !botsRunning ? 'Iniciando...' : 'Iniciar Todos los Bots'}
        </button>
        <button
          onClick={handleShutdownClick}
          disabled={shutdownDisabled}
          className={`px-5 py-2 font-semibold rounded-md text-white transition-colors duration-150 ease-in-out 
            ${shutdownDisabled 
              ? 'bg-gray-400 dark:bg-gray-600 cursor-not-allowed' 
              : 'bg-red-600 hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2'}
          `}
        >
          {isActionPending && botsRunning ? 'Apagando...' : 'Apagar Todos los Bots'}
        </button>
      </div>
      {actionMessage && (
         <p className={`text-sm text-center mt-3 ${actionMessage.includes('Error') ? 'text-red-600 dark:text-red-400' : 'text-gray-600 dark:text-gray-400'}`}>
           {actionMessage}
         </p>
      )}
    </div>
  );
}

export default BotControls; // Exportar el componente 