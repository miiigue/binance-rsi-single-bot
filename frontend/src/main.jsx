import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx' // Importa el componente principal de la aplicación
import './index.css' // Importa los estilos globales (incluye Tailwind)

// Busca el div con id 'root' en index.html y monta la aplicación React dentro de él
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
) 