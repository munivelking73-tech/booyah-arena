# BOOYAH ARENA — Firebase Cloud Messaging setup

This version is wired for Firebase Cloud Messaging (FCM) on the **website and Android player app**.

## Notification flow

1. A player joins a specific match slot.
2. The player dashboard registers its FCM device token with `/api/notifications/register-token`.
3. The admin publishes the Room ID/password for that exact slot.
4. The backend sends an FCM notification only to players who joined that exact slot.
5. Android shows the notification even when the app is in the background.
6. Web browsers show the notification when browser notifications are enabled.
7. Tapping the notification opens the player dashboard.

## 1. Create/connect Firebase

Create a Firebase project (or use an existing project). Add:
- Android app package: `com.booyaharena.app`
- A Web app for `https://booyah-arena-yxvl.onrender.com`

Enable the **Firebase Cloud Messaging API** in the Firebase project.

## 2. Android Firebase file

Download `google-services.json` from Firebase Console and put it here:

`BooyahArenaAndroid/app/google-services.json`

Do not rename it to `google-services (1).json` or another filename.

The Android ZIP deliberately does not contain your Firebase configuration file.

## 3. Website public Firebase values

Copy the Web app's values into Render Environment Variables:

- `FIREBASE_WEB_API_KEY`
- `FIREBASE_WEB_AUTH_DOMAIN`
- `FIREBASE_WEB_PROJECT_ID`
- `FIREBASE_WEB_STORAGE_BUCKET`
- `FIREBASE_WEB_MESSAGING_SENDER_ID`
- `FIREBASE_WEB_APP_ID`

Create a Web Push certificate key in Firebase Console > Project Settings > Cloud Messaging and put its public VAPID key in:

- `FIREBASE_WEB_VAPID_KEY`

The website exposes only these public client values through `/api/firebase-config`.

## 4. Backend service account

Firebase Console > Project Settings > Service Accounts:
- Generate a new private key.
- Copy the complete JSON contents into the Render environment variable:
  `FIREBASE_SERVICE_ACCOUNT_JSON`
- Set `FIREBASE_PROJECT_ID` to the Firebase project ID.

**Never put the service-account JSON in the Android app, browser code, GitHub repository, or public website.**

## 5. Deploy

After saving the Render environment variables, redeploy the service.

Then:
- Log into a player account.
- Open the player dashboard.
- Click **ENABLE NOTIFICATIONS** in a normal browser, or allow Android notifications in the Android app.
- Join a match.
- In Admin, publish that exact match room.
- The joined player's device should receive the room notification.

## 6. Test safely

The notification contains the Room ID/password because the backend sends it only to devices registered to players who joined that exact match slot. Do not expose room credentials in public schedule endpoints.

If FCM is not configured, publishing a room still works; the admin response will show `Firebase Admin SDK is not configured` and zero notifications sent.
