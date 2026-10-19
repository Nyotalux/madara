/* Madara — inscriptions Web Push (jalon 5).
 *
 * Enregistre le service worker (/sw.js) et demande la permission de notifier,
 * pour le personnel connecté au back-office uniquement. La clé publique VAPID
 * et l'URL d'abonnement sont passées en attributs data-* du gabarit de base.
 *
 * Comportement : la question (permission) n'est posée qu'une fois par
 * navigateur (balise localStorage « madara-webpush »).
 */
"use strict";

(function () {
  const script = document.currentScript;
  if (!script) return;
  const vapidKey = script.dataset.vapid || "";
  const subscribeUrl = script.dataset.subscribe || "/api/v1/webpush/";
  const STORAGE_KEY = "madara-webpush";

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function sendSubscription(subscription) {
    const json = subscription.toJSON();
    return fetch(subscribeUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      credentials: "same-origin",
      body: JSON.stringify({
        endpoint: json.endpoint,
        keys: json.keys || {},
        user_agent: navigator.userAgent.slice(0, 255),
      }),
    });
  }

  function urlToUint8(value) {
    const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
    const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
    const output = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index += 1) {
      output[index] = raw.charCodeAt(index);
    }
    return output;
  }

  async function subscribe(registration) {
    try {
      const existing = await registration.pushManager.getSubscription();
      if (existing) {
        sendSubscription(existing);
        return;
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        localStorage.setItem(STORAGE_KEY, "refusé");
        return;
      }
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlToUint8(vapidKey),
      });
      localStorage.setItem(STORAGE_KEY, "abonné");
      sendSubscription(subscription);
    } catch (error) {
      /* Silencieux : le push navigateur est un bonus, jamais bloquant. */
    }
  }

  function start() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
    if (!vapidKey) return; // VAPID non configuré : pas d'invitation.
    if (localStorage.getItem(STORAGE_KEY) === "refusé") return;
    navigator.serviceWorker.register("/sw.js").then(subscribe).catch(function () {
      /* HTTPS absent ou sw.js indisponible : on ignore. */
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
