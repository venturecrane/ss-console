;(function () {
  var scriptEl = document.currentScript
  if (!scriptEl) return

  var measurementId = scriptEl.dataset.ga4MeasurementId
  if (!measurementId) return

  var INTERNAL_STORAGE_KEY = 'ss_internal_traffic'
  var searchParams = new URLSearchParams(window.location.search)
  var internalOverride = searchParams.get('internal')

  if (internalOverride === '1') {
    window.localStorage.setItem(INTERNAL_STORAGE_KEY, '1')
  } else if (internalOverride === '0') {
    window.localStorage.removeItem(INTERNAL_STORAGE_KEY)
  }

  var rawPatterns = scriptEl.dataset.internalHostPatterns || ''
  var patternList = rawPatterns
    .split(',')
    .map(function (value) {
      return value.trim().toLowerCase()
    })
    .filter(Boolean)

  var hostname = window.location.hostname.toLowerCase()
  var internalFromHost = patternList.some(function (pattern) {
    if (pattern === hostname) return true
    if (!pattern.startsWith('*.')) return false
    var suffix = pattern.slice(1)
    return hostname.endsWith(suffix)
  })

  var internalFromStorage = window.localStorage.getItem(INTERNAL_STORAGE_KEY) === '1'
  var isInternalTraffic = internalFromHost || internalFromStorage

  window.dataLayer = window.dataLayer || []
  window.gtag =
    window.gtag ||
    function () {
      window.dataLayer.push(arguments)
    }

  if (isInternalTraffic) {
    window.gtag('set', 'traffic_type', 'internal')
  }

  window.gtag('js', new Date())
  window.gtag('config', measurementId, {
    anonymize_ip: true,
    allow_google_signals: false,
    traffic_type: isInternalTraffic ? 'internal' : undefined,
    user_properties: {
      internal_traffic: isInternalTraffic ? 'true' : 'false',
    },
  })

  window.ssTrackEvent = function (eventName, params) {
    if (!eventName || typeof window.gtag !== 'function') return
    window.gtag('event', eventName, params || {})
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('a[href]')
    if (!link) return
    if (!link.href) return

    var isExternal = link.origin && link.origin !== window.location.origin
    if (!isExternal) return

    window.ssTrackEvent('outbound_click', {
      link_url: link.href,
      link_domain: link.hostname,
      link_path: link.pathname,
    })
  })
})()
