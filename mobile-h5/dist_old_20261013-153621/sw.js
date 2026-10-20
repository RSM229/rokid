
const CACHE_PREFIX = 'gdjl-mobile-h5-'
const CACHE_NAME = CACHE_PREFIX + '32ae3218b0f28354'
const PRECACHE_URLS = ["/","/index.html","/manifest.json","/icons/icon-192.svg","/icons/icon-512.svg","/assets/AcceptanceGuide-BgQ2dSKD.js","/assets/AcceptanceGuide-DN_BwmkE.css","/assets/DefaultLayout-DerwICpK.js","/assets/DefaultLayout-DnMN_8Td.css","/assets/DocumentCreate-BG9WCoqQ.css","/assets/DocumentCreate-DRXt_VE9.js","/assets/DocumentDetail-B1fmLI4S.js","/assets/DocumentDetail-DXuer5qQ.css","/assets/DocumentList-CNEeSG4Z.js","/assets/ForcePassword-22Wh8pAY.css","/assets/ForcePassword-DemHxo5l.js","/assets/ForgotPassword-D7xXEV0n.css","/assets/ForgotPassword-DySaYpUs.js","/assets/HazardDetail-D3RghmlK.css","/assets/HazardDetail-DgacXuaC.js","/assets/HazardList-DJTHzy4y.js","/assets/Home-C25bGy_B.js","/assets/Home-DCJYc_8k.css","/assets/ImageCapture-CSga4voi.css","/assets/ImageCapture-DMeNKAZv.js","/assets/ImageList-2Fqe8MLX.js","/assets/ImageList-ztXoDVge.css","/assets/InspectionDetail-D5fs03G2.css","/assets/InspectionDetail-Jclc9UFO.js","/assets/InspectionList-CPDOG785.css","/assets/InspectionList-CStkzeGC.js","/assets/InspectionStart-DV2wo9ML.css","/assets/InspectionStart-qebiLCBs.js","/assets/KnowledgeSearch-B2gv5DFz.js","/assets/KnowledgeSearch-B88yq4Lr.css","/assets/LedgerDetail-80e_RTfL.css","/assets/LedgerDetail-D2HwWZk9.js","/assets/LedgerList-BLI5hMA8.css","/assets/LedgerList-gwemElL4.js","/assets/Login-3JhLHPuq.js","/assets/Login-D5SN4Q2_.css","/assets/Notifications-DISKGGyU.js","/assets/Notifications-Dz3WaGet.css","/assets/Profile-CSw-PNI7.js","/assets/Profile-DEwNssmd.css","/assets/Register-Bgtue2Ci.css","/assets/Register-DMw5uf5j.js","/assets/TaskDetail-Cp0IEVMI.css","/assets/TaskDetail-DXnaufxn.js","/assets/TaskList-BQscwbcq.js","/assets/TaskList-CFvNHkAQ.css","/assets/WorkRecordCreate-C82KapvI.css","/assets/WorkRecordCreate-js_YXd77.js","/assets/WorkRecordDetail-BH3JcJtB.css","/assets/WorkRecordDetail-BIyCVctt.js","/assets/WorkRecordList-B3T1VOsU.js","/assets/WorkRecordList-BSpF1Ro9.css","/assets/format-DbP_NGTS.js","/assets/haptic-CEiuAJ2c.js","/assets/index-B1vNIoP5.js","/assets/index-B4wmwnXg.css","/assets/index-B5COpqMx.css","/assets/index-B5t-luGu.js","/assets/index-B6T51wDm.css","/assets/index-B7Anyui2.css","/assets/index-BK94yxQ0.css","/assets/index-Bl2YSPiJ.js","/assets/index-BzrbYvYM.css","/assets/index-C9OPLXKi.css","/assets/index-CCjFVJ_3.js","/assets/index-CROaeh4W.js","/assets/index-CSXQS9uc.js","/assets/index-CUlHYqo-.js","/assets/index-C_c56t6o.css","/assets/index-CjAwa605.css","/assets/index-Ck4FUjXP.js","/assets/index-CmemF0xM.css","/assets/index-Cr9FdApc.js","/assets/index-CuxF09mD.css","/assets/index-CzXXK3_u.js","/assets/index-D-A-V2Q_.css","/assets/index-D7Vbqvgd.js","/assets/index-DCvfypF6.js","/assets/index-DGnB79RJ.css","/assets/index-DMUIn7P2.js","/assets/index-DX4tk93E.js","/assets/index-D_8vhTzU.js","/assets/index-DbCjY-4u.js","/assets/index-DlNFuQ4l.css","/assets/index-Dv-04oeB.css","/assets/index-DxD3z1V7.css","/assets/index-Dzdet8la.js","/assets/index-JGmO16DQ.js","/assets/index-MA5Ray2J.css","/assets/index-TlSNdJro.css","/assets/index-W1INwVKf.css","/assets/index-g3liu6Q1.css","/assets/index-iyi8d43w.js","/assets/index-p_Y1ee2a.css","/assets/index-sKgZjszW.js","/assets/index-tK-Q9NMd.js","/assets/permission-Ji63DadH.js","/assets/vant-bErQF6-E.js","/assets/vendor-Bxp0rZcI.js"]
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
