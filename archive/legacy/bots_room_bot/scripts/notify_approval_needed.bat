@echo off
REM === 承認要求通知（他の通知と区別するため Hand音 + 2回ビープ） ===
powershell -NoProfile -Command ^
[System.Media.SystemSounds]::Hand.Play(); ^
Start-Sleep -Milliseconds 300; ^
[System.Media.SystemSounds]::Hand.Play(); ^
Start-Sleep -Milliseconds 300; ^
Add-Type -AssemblyName System.Speech; ^
$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; ^
$speak.Speak('確認をお願いします')
