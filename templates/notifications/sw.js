/* Service worker de Madara : réception Web Push, jalon 5.
 * Servi depuis la racine (/sw.js) pour contrôler toutes les pages.
 * La clé publique VAPID est injectée côté serveur (cf. la vue /sw.js).
 */
/* eslint-env serviceworker */
"use strict";

const VAPID_PUBLIC_KEY = "{{ vapid_public_key }}";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

/* Message du site : re-vérification de la souscription guidée par la page. */
self.addEventListener("message", (event) => {
  if (event.data === "reclaim") {
    self.clients.claim();
  }
});

/* Réception : la notification système (comme sur Android/iPhone). */
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = { title: "Madara", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Madara", {
      body: data.body || "",
      icon: "/static/img/logo.png",
      badge: "/static/img/badge.png",
      tag: "madara",
      renotify: true,
      data: { url: data.url || "/notifications/" },
    })
  );
});

/* Clic : ouvre le casier de notifications du back-office. */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/notifications/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.includes("/notifications") && "focus" in client) {
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
