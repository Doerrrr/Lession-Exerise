package com.doerrrr.dictation;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.view.Menu;
import android.view.MenuItem;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public class MainActivity extends Activity {
    private static final int FILE_CHOOSER = 1001;
    private static final String DEFAULT_SERVER = "http://123.207.184.26:8765";
    private static final String PREFS = "dictation";
    private static final String KEY_SERVER = "server_url";

    private WebView webView;
    private SharedPreferences prefs;
    private TextToSpeech tts;
    private boolean ttsInitialized = false;
    private boolean ttsReady = false;
    private String ttsError = "";
    private final List<PendingUtterance> pendingUtterances = new ArrayList<>();
    private ValueCallback<Uri[]> filePathCallback;

    private static class PendingUtterance {
        final String id;
        final String text;
        final float rate;

        PendingUtterance(String id, String text, float rate) {
            this.id = id;
            this.text = text;
            this.rate = rate;
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        setupWebView();
        showStartDialog();
    }

    private void setupWebView() {
        if (webView != null) return;
        webView = new WebView(this);
        webView.setBackgroundColor(0xFFF6F1E7);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowContentAccess(true);
        settings.setAllowFileAccess(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setTextZoom(100);

        webView.addJavascriptInterface(new TtsBridge(), "AndroidTTS");
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return openExternally(request.getUrl().toString());
            }

            @Override
            @SuppressWarnings("deprecation")
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return openExternally(url);
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;
                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("*/*");
                intent.putExtra(Intent.EXTRA_MIME_TYPES,
                        new String[]{"application/json", "text/csv", "text/comma-separated-values", "text/plain"});
                try {
                    startActivityForResult(Intent.createChooser(intent, "选择 JSON / CSV 词本"), FILE_CHOOSER);
                } catch (Exception exc) {
                    filePathCallback = null;
                    Toast.makeText(MainActivity.this, "没有文件选择器", Toast.LENGTH_SHORT).show();
                    return false;
                }
                return true;
            }
        });
        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition,
                                        String mimeType, long contentLength) {
                download(url, userAgent, contentDisposition, mimeType);
            }
        });

        setContentView(webView);
        initTts();
    }

    private void showStartDialog() {
        final EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(prefs.getString(KEY_SERVER, DEFAULT_SERVER));
        input.setHint(DEFAULT_SERVER);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(48, 16, 48, 0);
        layout.addView(input);

        new AlertDialog.Builder(this)
                .setTitle("听写本")
                .setMessage("连接服务器使用账号数据；本地模式使用本机离线数据。")
                .setView(layout)
                .setCancelable(false)
                .setPositiveButton("连接服务器", (dialog, which) -> {
                    String url = input.getText().toString().trim();
                    if (!url.startsWith("http://") && !url.startsWith("https://")) {
                        url = "http://" + url;
                    }
                    if (url.isEmpty()) url = DEFAULT_SERVER;
                    prefs.edit().putString(KEY_SERVER, url).apply();
                    setupWebView();
                    webView.loadUrl(url);
                })
                .setNegativeButton("本地模式", (dialog, which) -> {
                    prefs.edit().putString(KEY_SERVER, "").apply();
                    startLocal();
                })
                .show();
    }

    private void startLocal() {
        setupWebView();
        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }
            int port = Python.getInstance()
                    .getModule("android_server")
                    .callAttr("start", getFilesDir().getAbsolutePath())
                    .toJava(int.class);
            webView.loadUrl("http://127.0.0.1:" + port + "/");
        } catch (Exception exc) {
            TextView error = new TextView(this);
            error.setPadding(48, 96, 48, 48);
            error.setText("Python 服务启动失败：\n" + exc);
            setContentView(error);
        }
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add("切换服务器 / 本地模式");
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        showStartDialog();
        return true;
    }

    private boolean openExternally(String url) {
        if (url.startsWith("http://127.0.0.1:") || url.startsWith("http://localhost:")) {
            return false;
        }
        if (url.startsWith("http://") || url.startsWith("https://")) {
            return false;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Exception ignored) {
        }
        return true;
    }

    private void initTts() {
        tts = new TextToSpeech(this, this::onTtsInit);
        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override
            public void onStart(String utteranceId) {
            }

            @Override
            public void onDone(String utteranceId) {
                ttsDone(utteranceId, true, "");
            }

            @Override
            public void onError(String utteranceId) {
                ttsDone(utteranceId, false, "TTS 播放失败");
            }

            @Override
            public void onError(String utteranceId, int errorCode) {
                ttsDone(utteranceId, false, ttsErrorText(errorCode));
            }
        });
    }

    private void onTtsInit(int status) {
        runOnUiThread(() -> {
            ttsInitialized = true;
            if (status != TextToSpeech.SUCCESS || tts == null) {
                ttsReady = false;
                ttsError = "系统文字转语音引擎初始化失败；请安装/启用 TTS 引擎（例如 Google 文字转语音）。";
                failPending();
                return;
            }
            int language = tts.setLanguage(Locale.US);
            if (isMissingLanguage(language)) language = tts.setLanguage(Locale.UK);
            if (isMissingLanguage(language)) language = tts.setLanguage(Locale.ENGLISH);
            if (isMissingLanguage(language)) language = tts.setLanguage(Locale.getDefault());
            if (isMissingLanguage(language)) {
                ttsReady = false;
                ttsError = "系统没有可用的语音数据；请在系统设置 → 文字转语音中安装英文语音。";
                failPending();
                return;
            }
            AudioAttributes attrs = new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build();
            tts.setAudioAttributes(attrs);
            ttsReady = true;
            flushPending();
        });
    }

    private static boolean isMissingLanguage(int result) {
        return result == TextToSpeech.LANG_MISSING_DATA
                || result == TextToSpeech.LANG_NOT_SUPPORTED;
    }

    private void failPending() {
        for (PendingUtterance item : pendingUtterances) {
            ttsDone(item.id, false, ttsError);
        }
        pendingUtterances.clear();
    }

    private void flushPending() {
        for (PendingUtterance item : pendingUtterances) {
            doSpeak(item.id, item.text, item.rate);
        }
        pendingUtterances.clear();
    }

    private void doSpeak(String id, String text, float rate) {
        if (tts == null) {
            ttsDone(id, false, "TTS 未初始化");
            return;
        }
        if (!ttsReady) {
            ttsDone(id, false, ttsError.isEmpty() ? "TTS 不可用" : ttsError);
            return;
        }
        try {
            tts.setSpeechRate(Math.max(0.6f, Math.min(1.2f, rate)));
            int result = tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, id);
            if (result != TextToSpeech.SUCCESS) {
                ttsDone(id, false, "TTS 启动失败（code " + result + "）");
            }
        } catch (Exception exc) {
            ttsDone(id, false, "TTS 异常：" + exc.getMessage());
        }
    }

    private static String ttsErrorText(int errorCode) {
        switch (errorCode) {
            case TextToSpeech.ERROR_OUTPUT:
                return "音频输出不可用；请检查耳机/音量/蓝牙连接。";
            case TextToSpeech.ERROR_NETWORK:
                return "语音数据需要网络；请联网后重试。";
            case TextToSpeech.ERROR_NETWORK_TIMEOUT:
                return "语音合成网络超时。";
            case TextToSpeech.ERROR_NOT_INSTALLED_YET:
                return "语音数据还没安装完成；请到系统文字转语音设置安装英文数据。";
            case TextToSpeech.ERROR_SYNTHESIS:
                return "语音合成失败；请换一个 TTS 引擎或安装英文数据。";
            default:
                return "TTS 播放失败（code " + errorCode + "）";
        }
    }

    private void ttsDone(String id, boolean ok, String reason) {
        runOnUiThread(() -> {
            if (webView == null) return;
            String js = "window.__androidTtsDone(" + JSONObject.quote(id) + "," + ok + ","
                    + JSONObject.quote(reason == null ? "" : reason) + ")";
            webView.evaluateJavascript(js, null);
        });
    }

    private void download(String url, String userAgent, String contentDisposition, String mimeType) {
        try {
            String fileName = URLUtil.guessFileName(url, contentDisposition, mimeType);
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.addRequestHeader("User-Agent", userAgent);
            request.setMimeType(mimeType);
            request.setTitle(fileName);
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName);
            DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            manager.enqueue(request);
            Toast.makeText(this, "已保存到下载：" + fileName, Toast.LENGTH_LONG).show();
        } catch (Exception exc) {
            Toast.makeText(this, "保存失败：" + exc.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_CHOOSER && filePathCallback != null) {
            filePathCallback.onReceiveValue(
                    WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            filePathCallback = null;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        pendingUtterances.clear();
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    private class TtsBridge {
        @JavascriptInterface
        public void speak(String id, String text, float rate) {
            runOnUiThread(() -> {
                if (!ttsInitialized) {
                    pendingUtterances.add(new PendingUtterance(id, text, rate));
                    return;
                }
                doSpeak(id, text, rate);
            });
        }

        @JavascriptInterface
        public void stop() {
            runOnUiThread(() -> {
                if (tts != null) tts.stop();
            });
        }

        @JavascriptInterface
        public void openSettings() {
            runOnUiThread(() -> {
                try {
                    startActivity(new Intent("com.android.settings.TTS_SETTINGS"));
                } catch (Exception exc) {
                    try {
                        startActivity(new Intent(android.provider.Settings.ACTION_SETTINGS));
                    } catch (Exception ignored) {
                    }
                }
            });
        }
    }
}
