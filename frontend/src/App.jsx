import React from 'react';
import ConfigForm from './ConfigForm'; // Importa el componente del formulario

function App() {
  return (
    // Contenedor principal con algo de padding y centrado (usando Tailwind)
    <div className="container mx-auto p-4 max-w-2xl">
      {/* Título de la aplicación */}
      <h1 className="text-2xl font-bold mb-6 text-center text-gray-800 dark:text-gray-200">
        Configuración del Bot de Trading RSI
      </h1>

      {/* Renderiza el componente del formulario */}
      <ConfigForm />

      {/* Aquí podríamos añadir secciones para ver el estado del bot, logs, historial, etc. */}
      {/* 
      <div className="mt-8 p-4 border rounded bg-gray-100 dark:bg-gray-800">
        <h2 className="text-xl font-semibold mb-2">Estado del Bot</h2>
        <p>Estado: <span className="font-mono">Pausado</span></p>
        <p>Última Actualización: <span className="font-mono">N/A</span></p>
      </div> 
      */}
    </div>
  );
}

export default App; 