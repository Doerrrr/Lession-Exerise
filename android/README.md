# Android build

This is a thin Android shell around the same local Python server and Web UI.

- The WebView loads `http://127.0.0.1:<random>/` from `android_server.py`.
- Grading is **API-only** (DeepSeek or any OpenAI-compatible `/chat/completions`); there is no local grading path in the app.
- Speech uses Android's `TextToSpeech` via the `AndroidTTS` JavaScript bridge (the desktop Web Speech API is not reliable inside Android WebView).
- Bluetooth/headphone keep-alive is implemented in the Web UI (inaudible low-frequency Web Audio) and works with the native TTS media audio attributes.

## Build

Requirements: JDK 17, Android SDK 34, Android build-tools 34.0.0, Gradle 8.7, Python 3.8+ for Chaquopy.

1. Run `./sync-assets.ps1` to copy the shared Python/Web/word assets into `app/src/main/python/android_assets`.
2. Set `sdk.dir` in `local.properties` (or `ANDROID_HOME`).
3. Optional signing: create `keystore.properties` with `storeFile`, `storePassword`, `keyAlias`, `keyPassword`; put the keystore at that path. Without it, `assembleRelease` produces an unsigned APK.
4. Build:

   ```bat
   gradle assembleRelease
   ```

   The APK is at `app/build/outputs/apk/release/app-release.apk`.

The generated `android_assets` folder and the keystore are intentionally ignored by Git. Do not commit `data/secrets.json`, user sessions/checks, or imported wordbooks.
