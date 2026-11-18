@echo off
rem 打开 p5 Bridge 的画布窗口。
rem 关键参数：禁用"窗口被遮挡就降频/暂停"的行为，否则 Blender 全屏时画布会静默。
rem 用一个独立 profile，避免和你日常浏览器的标签混在一起。
setlocal
set PORT=%1
if "%PORT%"=="" set PORT=9877

set EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
set CHROME86=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe

set BROWSER=%EDGE%
if exist "%CHROME%" set BROWSER=%CHROME%
if exist "%CHROME86%" set BROWSER=%CHROME86%

start "" "%BROWSER%" ^
  --app=http://127.0.0.1:%PORT%/ ^
  --user-data-dir=%TEMP%\p5bridge-profile ^
  --disable-background-timer-throttling ^
  --disable-renderer-backgrounding ^
  --disable-backgrounding-occluded-windows ^
  --disable-features=CalculateNativeWinOcclusion ^
  --window-size=680,460

echo opened http://127.0.0.1:%PORT%/ in %BROWSER%
