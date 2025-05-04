/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}", // Busca clases de Tailwind en estos archivos
  ],
  theme: {
    extend: {}, // Aquí puedes extender o personalizar el tema de Tailwind
  },
  plugins: [], // Aquí puedes añadir plugins de Tailwind
} 