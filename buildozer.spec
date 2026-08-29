[app]
title = BlueBlood
package.name = blueblood
package.domain = org.blueblood
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,txt,csv
version = 1.0
requirements = python3,kivy==2.3.0,kivymd==1.2.0,pillow,numpy==1.26.4,pandas==2.2.2,yfinance,plyer,requests,urllib3,certifiorientation = portrait
fullscreen = 0
android.permissions = INTERNET,VIBRATE
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
icon.filename = icon.png
presplash.filename = icon.png

[buildozer]
log_level = 2
warn_on_root = 0