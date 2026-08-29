/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './marketing-website/**/*.html',
    './templates/**/*.html',
    './apps/**/*.py',
  ],
  theme: {
    extend: {
      colors: {
        forest: '#2C4A45',
        deepTeal: '#1A7A7A',
        mediumTeal: '#2EB8B8',
        eucalyptus: '#5A9E8F',
        ink: '#0F2B35',
        gold: '#F5A623',
        linen: '#F7F2EA',
        seafoam: '#A8CFC4',
        sand: '#E8D5B0',
      },
      boxShadow: {
        glow: '0 24px 80px rgba(26, 122, 122, 0.22)',
        mediumTealGlow: '0 22px 70px rgba(46, 184, 184, 0.18)',
      },
      fontFamily: {
        sans: ['Plus Jakarta Sans', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['Plus Jakarta Sans', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
