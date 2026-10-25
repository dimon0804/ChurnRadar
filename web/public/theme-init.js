;(function () {
  var stored = null
  try {
    stored = localStorage.getItem('cr-theme')
  } catch (e) {}
  // по макету тёмная тема — основная: светлая включается только явным выбором пользователя
  var dark = stored !== 'light'
  document.documentElement.classList.toggle('dark', dark)
})()

// Если при загрузке оборвался файл приложения (сбой сети или прокси), страница остаётся пустой.
// Один раз перезагружаемся сами; флаг в sessionStorage не даёт уйти в цикл.
;(function () {
  var failed = false
  window.addEventListener(
    'error',
    function (e) {
      var t = e.target
      if (t && (t.tagName === 'SCRIPT' || t.tagName === 'LINK')) failed = true
    },
    true,
  )
  window.addEventListener('load', function () {
    setTimeout(function () {
      var root = document.getElementById('root')
      var mounted = root && root.childElementCount > 0
      try {
        if (mounted) return sessionStorage.removeItem('cr-boot-retry')
        if (!failed || sessionStorage.getItem('cr-boot-retry')) return
        sessionStorage.setItem('cr-boot-retry', '1')
      } catch (e) {
        if (mounted || !failed) return
      }
      location.reload()
    }, 1000)
  })
})()
