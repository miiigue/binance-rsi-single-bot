import React, { useState, useEffect } from 'react';
import BotControls from './BotControls';

// Clave para guardar/leer en localStorage
const STATUS_CACHE_KEY = 'botStatusesCache';

function StatusDisplay({ botsRunning, onStart, onShutdown }) {
  // Intentar cargar el estado inicial desde localStorage, asegurando que sea un array válido
  const [statuses, setStatuses] = useState(() => {
    const cachedData = localStorage.getItem(STATUS_CACHE_KEY);
    let parsedData = [];
    if (cachedData) {
        try {
          const rawParsed = JSON.parse(cachedData);
          // Asegurarse de que sea un array y filtrar elementos no válidos/null
          if (Array.isArray(rawParsed)) {
              parsedData = rawParsed.filter(item => item !== null && typeof item === 'object');
          } else {
               console.warn("Cached status data was not an array:", rawParsed);
          }
        } catch (e) {
          console.error("Error parsing cached status data:", e);
          // Si hay error de parseo, localStorage se limpiará en el próximo guardado exitoso
        }
    }
    return parsedData; // Devuelve un array vacío o el array filtrado
  });
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await fetch('/api/status');
        if (!response.ok) {
          // Si la respuesta no es OK, lanzar un error para ir al catch
          // Podríamos intentar leer un mensaje de error específico si la API lo envía
          let errorMsg = `HTTP error! status: ${response.status}`;
          try {
             const errData = await response.json();
             errorMsg = errData.error || errorMsg;
          } catch (jsonError) { /* Ignorar si el cuerpo del error no es JSON */ }
          throw new Error(errorMsg); 
        }
        const data = await response.json(); // data ahora es { bots_running: ..., statuses: [...] }
        
        // --- EXTRAER el array 'statuses' de la respuesta --- 
        if (data && Array.isArray(data.statuses)) {
            setStatuses(data.statuses); // Guardar el array actual
             // Guardar los datos exitosos en localStorage (solo el array de statuses)
            try {
                localStorage.setItem(STATUS_CACHE_KEY, JSON.stringify(data.statuses));
            } catch (e) {
                console.error("Error saving status to localStorage:", e);
            }
        } else {
             console.warn("La respuesta de /api/status no contenía un array 'statuses' válido:", data);
             // ¿Qué hacer aquí? Podríamos mantener el estado anterior o limpiarlo.
             // Mantener el estado anterior si ya teníamos algo es más seguro.
             if (statuses.length === 0) {
                 setStatuses([]); // Limpiar solo si no teníamos nada antes
             }
        }
        // -----------------------------------------------------
        setError(null); // Limpiar cualquier error anterior
        
      } catch (e) {
        // Error al hacer fetch (ej: red, API apagada)
        console.error("Error fetching bot status:", e);
        // Establecer mensaje de error específico sin borrar los datos
        setError("Bot apagado o API no disponible. Mostrando últimos datos conocidos.");
        // NO HACEMOS setStatuses([]) para mantener los últimos datos visibles
      }
    };

    fetchData(); // Llamar una vez al montar
    const intervalId = setInterval(fetchData, 5000); // Refrescar cada 5s
    return () => clearInterval(intervalId); // Limpiar intervalo al desmontar
  }, []);

  // statusArray ya no es necesario, statuses es el array directamente
  // const statusArray = Object.values(statuses);

  return (
    <div className="bg-white dark:bg-gray-800 shadow-md rounded-lg p-6 mt-6">
      <h2 className="text-xl font-semibold mb-4 text-gray-900 dark:text-white">Bot Status</h2>
      
      <BotControls 
        botsRunning={botsRunning} 
        onStart={onStart} 
        onShutdown={onShutdown} 
      />
      
      {/* Mostrar el mensaje de error personalizado */}
      {error && <p className="text-yellow-600 dark:text-yellow-400 mb-4 font-medium">{error}</p>}
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-700">
            <tr>
              <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                Symbol
              </th>
              <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                State
              </th>
              <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                Current PnL
              </th>
              <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                Hist. PnL
              </th>
               <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                Pending Entry ID
              </th>
               <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                Pending Exit ID
              </th>
              <th scope="col" className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase tracking-wider">
                Last Error
              </th>
            </tr>
          </thead>
          <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
            {statuses.length > 0 ? (
              statuses.map((status) => ( 
                <tr key={status.symbol}>
                  <td className="px-4 py-2 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-white">
                    {status.symbol}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300">
                     <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                         status.state === 'IN_POSITION' ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200' :
                         status.state === 'ERROR' ? 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200' :
                         status.state?.includes('WAITING') ? 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200' :
                         status.state === 'Inactive' ? 'bg-gray-100 text-gray-800 dark:bg-gray-600 dark:text-gray-300' : /* Estilo para Inactivo */
                         'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200'
                     }`}>
                       {status.state || 'N/A'}
                     </span>
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300">
                    {status.pnl !== null && status.pnl !== undefined ? status.pnl.toFixed(4) : '-'}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300">
                    {status.cumulative_pnl !== null && status.cumulative_pnl !== undefined ? status.cumulative_pnl.toFixed(4) : '-'}
                  </td>
                   <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300">
                    {status.pending_entry_order_id || '-'}
                  </td>
                   <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300">
                    {status.pending_exit_order_id || '-'}
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-500 dark:text-gray-300 break-words max-w-xs">
                    {status.last_error || '-'}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="7" className="px-4 py-4 text-center text-sm text-gray-500 dark:text-gray-400">
                  {error && statuses.length === 0 
                     ? 'No se pudieron obtener datos y la API no responde.'
                     : error 
                         ? 'API no disponible. Mostrando últimos datos conocidos.'
                         : 'Esperando datos de la API...'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default StatusDisplay; 