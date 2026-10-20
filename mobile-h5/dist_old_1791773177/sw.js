
const CACHE_PREFIX = 'gdjl-mobile-h5-'
const CACHE_NAME = CACHE_PREFIX + '2207cb2342987183'
const PRECACHE_URLS = ["/","/index.html","/manifest.json","/icons/icon-192.svg","/icons/icon-512.svg","/assets/AcceptanceGuide-CCoYks34.js","/assets/AcceptanceGuide-DN_BwmkE.css","/assets/DefaultLayout-DnMN_8Td.css","/assets/DefaultLayout-RakqQFwb.js","/assets/DocumentCreate-BG9WCoqQ.css","/assets/DocumentCreate-BYkfIGfw.js","/assets/DocumentDetail-DXuer5qQ.css","/assets/DocumentDetail-RkzUAP4I.js","/assets/DocumentList-KnsOkiaS.js","/assets/HazardDetail-CF3FbN4C.js","/assets/HazardDetail-D3RghmlK.css","/assets/HazardList-Cydx4p0f.js","/assets/Home-C_PBgwMf.js","/assets/Home-DCJYc_8k.css","/assets/ImageCapture-CSga4voi.css","/assets/ImageCapture-D1N9mHll.js","/assets/ImageList-CxK7nQ_D.js","/assets/ImageList-ztXoDVge.css","/assets/InspectionDetail-D5fs03G2.css","/assets/InspectionDetail-Dqp5V5Gu.js","/assets/InspectionList-CEox7J2B.js","/assets/InspectionList-CPDOG785.css","/assets/InspectionStart-DV2wo9ML.css","/assets/InspectionStart-DgHMaASq.js","/assets/KnowledgeSearch-B88yq4Lr.css","/assets/KnowledgeSearch-BLh5OoB_.js","/assets/LedgerDetail-80e_RTfL.css","/assets/LedgerDetail-BiNdYhN5.js","/assets/LedgerList-BLI5hMA8.css","/assets/LedgerList-BcJydoEB.js","/assets/Login-HYzDY7J1.css","/assets/Login-e0eqPygT.js","/assets/Notifications-BGsnlSrN.js","/assets/Notifications-Dz3WaGet.css","/assets/Profile-BXlnrDJ7.js","/assets/Profile-DCCGf6S1.css","/assets/Register-B0VsRIox.js","/assets/Register-Bgtue2Ci.css","/assets/TaskDetail-Cp0IEVMI.css","/assets/TaskDetail-DW55PrIn.js","/assets/TaskList-CFvNHkAQ.css","/assets/TaskList-DG5AL0e7.js","/assets/WorkRecordCreate-C82KapvI.css","/assets/WorkRecordCreate-UxeeJQKY.js","/assets/WorkRecordDetail-BH3JcJtB.css","/assets/WorkRecordDetail-CjgAbObe.js","/assets/WorkRecordList-2_teYtr7.js","/assets/WorkRecordList-BSpF1Ro9.css","/assets/format-DbP_NGTS.js","/assets/haptic-CEiuAJ2c.js","/assets/index-B1vNIoP5.js","/assets/index-B4wmwnXg.css","/assets/index-B5COpqMx.css","/assets/index-B5t-luGu.js","/assets/index-B6T51wDm.css","/assets/index-B7Anyui2.css","/assets/index-BK94yxQ0.css","/assets/index-Bl2YSPiJ.js","/assets/index-BzTnTalI.js","/assets/index-BzrbYvYM.css","/assets/index-C9OPLXKi.css","/assets/index-CCjFVJ_3.js","/assets/index-CSXQS9uc.js","/assets/index-CUlHYqo-.js","/assets/index-C_c56t6o.css","/assets/index-CjAwa605.css","/assets/index-Ck4FUjXP.js","/assets/index-Cr9FdApc.js","/assets/index-CuxF09mD.css","/assets/index-CzXXK3_u.js","/assets/index-D-A-V2Q_.css","/assets/index-D7Vbqvgd.js","/assets/index-DCvfypF6.js","/assets/index-DGnB79RJ.css","/assets/index-DMUIn7P2.js","/assets/index-DX4tk93E.js","/assets/index-D_8vhTzU.js","/assets/index-DbCjY-4u.js","/assets/index-DlNFuQ4l.css","/assets/index-Dv-04oeB.css","/assets/index-DxD3z1V7.css","/assets/index-JGmO16DQ.js","/assets/index-MA5Ray2J.css","/assets/index-TlSNdJro.css","/assets/index-W1INwVKf.css","/assets/index-g3liu6Q1.css","/assets/index-iyi8d43w.js","/assets/index-p_Y1ee2a.css","/assets/index-sKgZjszW.js","/assets/index-tK-Q9NMd.js","/assets/permission-O4z-puOF.js","/assets/vant-C3NUKTw4.js","/assets/vendor-pLDdq9FN.js"]
const NEVER_CACHE_PREFIXES = ['/api/', '/uploads/', '/exports/']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  const url = new URL(request.url)
  if (
    request.method !== 'GET' ||
    url.origin !== self.location.origin ||
    NEVER_CACHE_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))
  ) {
    return
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/index.html'))
    )
    return
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      if (response.ok && response.type === 'basic') {
        const copy = response.clone()
        caches.open(CACHE_NAME).then((cache) => cache.put(request, copy))
      }
      return response
    }))
  )
})
