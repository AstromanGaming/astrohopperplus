package com.astromangaming.astrohopperplus

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.view.View
import android.webkit.GeolocationPermissions
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.google.android.material.appbar.MaterialToolbar

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private lateinit var toolbar: MaterialToolbar
    private var isFullscreen = false

    private val REQUEST_LOCATION_PERMISSION = 1001
    private var pendingGeolocationOrigin: String? = null
    private var pendingGeolocationCallback: GeolocationPermissions.Callback? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayShowTitleEnabled(false)
        supportActionBar?.title = ""
        toolbar.setTitleTextColor(Color.BLACK)

        progressBar = findViewById(R.id.progressBar)
        progressBar.max = 100

        webView = findViewById(R.id.webview)
        configureWebView()

        webView.addJavascriptInterface(WebAppInterface(), "AndroidApp")

        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedError(view: WebView?, request: WebResourceRequest?, error: WebResourceError?) {
                Log.e("WebView", "onReceivedError: ${error?.errorCode} ${error?.description}")
            }
            override fun onReceivedHttpError(view: WebView?, request: WebResourceRequest?, errorResponse: WebResourceResponse?) {
                Log.e("WebView", "HTTP error ${errorResponse?.statusCode} for ${request?.url}")
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                if (newProgress < 100) {
                    progressBar.visibility = View.VISIBLE
                    progressBar.progress = newProgress
                } else {
                    progressBar.visibility = View.GONE
                }
            }

            override fun onGeolocationPermissionsShowPrompt(origin: String?, callback: GeolocationPermissions.Callback?) {
                val permission = Manifest.permission.ACCESS_FINE_LOCATION
                if (ContextCompat.checkSelfPermission(this@MainActivity, permission) == PackageManager.PERMISSION_GRANTED) {
                    callback?.invoke(origin, true, false)
                } else {
                    pendingGeolocationOrigin = origin
                    pendingGeolocationCallback = callback
                    checkAndRequestLocationPermission()
                }
            }

            // Optional: log console messages for easier JS debugging
            override fun onConsoleMessage(message: android.webkit.ConsoleMessage?): Boolean {
                Log.d("WebConsole", "${message?.message()} -- ${message?.sourceId()}:${message?.lineNumber()}")
                return super.onConsoleMessage(message)
            }
        }

        webView.loadUrl("file:///android_asset/astrohopperplus/astrohopper.html")
    }

    private fun configureWebView() {
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.allowFileAccess = true
        settings.setGeolocationEnabled(true)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }
    }

    // Fullscreen toggle exposé et gestion UI Android
    inner class WebAppInterface {
        @JavascriptInterface
        fun toggleFullscreen() {
            runOnUiThread { setFullscreen(!isFullscreen) }
        }

        @JavascriptInterface
        fun enterFullscreen() {
            runOnUiThread { setFullscreen(true) }
        }

        @JavascriptInterface
        fun exitFullscreen() {
            runOnUiThread { setFullscreen(false) }
        }

        @JavascriptInterface
        fun requestLocationPermission() {
            runOnUiThread { checkAndRequestLocationPermission() }
        }

        @JavascriptInterface
        fun reloadPage() {
            runOnUiThread { webView.reload() }
        }
    }

    private fun setFullscreen(enable: Boolean) {
        val insetsController = WindowInsetsControllerCompat(window, window.decorView)
        if (enable) {
            insetsController.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            insetsController.hide(android.view.WindowInsets.Type.systemBars())
            toolbar.visibility = View.GONE
            isFullscreen = true
        } else {
            insetsController.show(android.view.WindowInsets.Type.systemBars())
            toolbar.visibility = View.VISIBLE
            isFullscreen = false
        }
        // Informer la page web de l'état (optionnel)
        webView.post {
            webView.evaluateJavascript("if(window.onAndroidFullscreenChanged) window.onAndroidFullscreenChanged($isFullscreen);", null)
        }
    }

    // Permission location et callbacks geolocation
    private fun checkAndRequestLocationPermission() {
        val permission = Manifest.permission.ACCESS_FINE_LOCATION
        if (ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED) {
            pendingGeolocationOrigin?.let { origin ->
                pendingGeolocationCallback?.invoke(origin, true, false)
                pendingGeolocationOrigin = null
                pendingGeolocationCallback = null
            }
            return
        }

        if (ActivityCompat.shouldShowRequestPermissionRationale(this, permission)) {
            AlertDialog.Builder(this)
                .setTitle("Permission de localisation")
                .setMessage("AstroHopperPlus a besoin d'accéder à votre position pour certaines fonctionnalités. Autoriser la localisation ?")
                .setPositiveButton("Autoriser") { _, _ ->
                    ActivityCompat.requestPermissions(this, arrayOf(permission), REQUEST_LOCATION_PERMISSION)
                }
                .setNegativeButton("Refuser") { dialog, _ ->
                    dialog.dismiss()
                    pendingGeolocationOrigin?.let { origin ->
                        pendingGeolocationCallback?.invoke(origin, false, false)
                        pendingGeolocationOrigin = null
                        pendingGeolocationCallback = null
                    }
                }
                .show()
        } else {
            ActivityCompat.requestPermissions(this, arrayOf(permission), REQUEST_LOCATION_PERMISSION)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_LOCATION_PERMISSION) {
            val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
            if (granted) {
                Toast.makeText(this, "Permission localisation accordée", Toast.LENGTH_SHORT).show()
                pendingGeolocationOrigin?.let { origin ->
                    pendingGeolocationCallback?.invoke(origin, true, false)
                    pendingGeolocationOrigin = null
                    pendingGeolocationCallback = null
                }
                webView.post {
                    webView.evaluateJavascript("if(window.onAndroidLocationPermissionGranted) window.onAndroidLocationPermissionGranted();", null)
                }
            } else {
                Toast.makeText(this, "Permission localisation refusée", Toast.LENGTH_SHORT).show()
                pendingGeolocationOrigin?.let { origin ->
                    pendingGeolocationCallback?.invoke(origin, false, false)
                    pendingGeolocationOrigin = null
                    pendingGeolocationCallback = null
                }
                webView.post {
                    webView.evaluateJavascript("if(window.onAndroidLocationPermissionDenied) window.onAndroidLocationPermissionDenied();", null)
                }
            }
        }
    }

    override fun onBackPressed() {
        if (this::webView.isInitialized && webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }
}