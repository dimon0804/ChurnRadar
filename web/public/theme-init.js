;(function () {
  var stored = null
  try {
    stored = localStorage.getItem('cr-theme')
  } catch (e) {}
  var dark = stored === 'dark' || (stored !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
})()
