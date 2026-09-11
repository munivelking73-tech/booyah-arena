/* BOOYAH ARENA — Firebase Cloud Messaging service worker.
   The backend exposes the public Firebase web config at /api/firebase-config. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

let messagingReady = null;

async function getMessaging() {
  if (messagingReady) return messagingReady;
  messagingReady = (async () => {
    const configResponse = await fetch('/api/firebase-config', {cache: 'no-store'});
    const config = await configResponse.json();
    if (!config.enabled) return null;
    importScripts('https://www.gstatic.com/firebasejs/11.10.0/firebase-app-compat.js');
    importScripts('https://www.gstatic.com/firebasejs/11.10.0/firebase-messaging-compat.js');
    firebase.initializeApp({
      apiKey: config.apiKey,
      authDomain: config.authDomain,
      projectId: config.projectId,
      storageBucket: config.storageBucket,
      messagingSenderId: config.messagingSenderId,
      appId: config.appId
    });
    return firebase.messaging();
  })();
  return messagingReady;
}

getMessaging().then(messaging => {
  if (!messaging) return;
  messaging.onBackgroundMessage(payload => {
    const data = payload.data || {};
    const notification = payload.notification || {};
    const title = notification.title || data.title || 'BOOYAH ARENA';
    const body = notification.body || data.body || 'A match room update is available.';
    self.registration.showNotification(title, {
      body,
      icon: '/favicon.ico',
      data: data
    });
  });
}).catch(err => console.error('BOOYAH FCM service worker init failed', err));

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const data = event.notification.data || {};
  const target = data.url || '/player-dashboard.html';
  event.waitUntil(clients.matchAll({type:'window', includeUncontrolled:true}).then(list => {
    for (const client of list) {
      if ('focus' in client) {
        client.navigate(target);
        return client.focus();
      }
    }
    return clients.openWindow(target);
  }));
});
