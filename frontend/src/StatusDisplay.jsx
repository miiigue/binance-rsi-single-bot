import React, { useState, useEffect } from 'react';

function StatusDisplay() {
  const [statuses, setStatuses] = useState({}); // Estado para guardar los datos de /api/status
  const [error, setError] = useState(null); // Estado para errores de fetch

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await fetch('/api/status');
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        setStatuses(data);
        setError(null); // Limpiar error si la llamada fue exitosa
        // console.log("Status updated:", data); // Para depuración
      } catch (e) {
        console.error("Error fetching bot status:", e);
        setError("Failed to fetch status. Is the API server running?");
        // Podríamos limpiar statuses aquí o mantener el último estado válido
        // setStatuses({}); 
      }
    };

    fetchData(); // Llamar una vez al montar el componente
    const intervalId = setInterval(fetchData, 5000); // Llamar cada 5 segundos

    // Función de limpieza para detener el intervalo cuando el componente se desmonte
    return () => clearInterval(intervalId);
  }, []); // El array vacío asegura que el efecto se ejecute solo una vez al montar (para el intervalo)

  // Convertir el objeto de statuses en un array para mapearlo fácilmente
  const statusArray = Object.values(statuses);

  return (
    <div className="bg-white dark:bg-gray-800 shadow-md rounded-lg p-6 mt-6">
      <h2 className="text-xl font-semibold mb-4 text-gray-900 dark:text-white">Bot Status</h2>
      {error && <p className="text-red-500 dark:text-red-400 mb-4">{error}</p>}
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
                PnL (USDT)
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
            {statusArray.length > 0 ? (
              statusArray.map((status) => (
                <tr key={status.symbol}>
                  <td className="px-4 py-2 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-white">
                    {status.symbol}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300">
                     {/* Podríamos añadir colores según el estado */}
                     <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                         status.state === 'IN_POSITION' ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200' :
                         status.state === 'ERROR' ? 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200' :
                         status.state?.includes('WAITING') ? 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200' :
                         'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200'
                     }`}>
                       {status.state || 'N/A'}
                     </span>
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-500 dark:text-gray-300">
                    {status.pnl !== null && status.pnl !== undefined ? status.pnl.toFixed(4) : '-'}
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
                <td colSpan="6" className="px-4 py-4 text-center text-sm text-gray-500 dark:text-gray-400">
                  No status data available. Waiting for API...
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